from fastapi import FastAPI, APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import re
import json
import uuid
import base64
import logging
import requests
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List
from datetime import datetime, timezone, timedelta
from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
from pywebpush import webpush, WebPushException
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Rate limiter
def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return get_remote_address(request)

limiter = Limiter(key_func=_client_ip, default_limits=["240/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

api_router = APIRouter(prefix="/api")


class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.get("/wakeup")
async def wakeup():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}

@api_router.get("/quiz")
async def serve_quiz():
    return FileResponse(ROOT_DIR.parent / "quiz.html", media_type="text/html")

STATIC_DIR = ROOT_DIR.parent / "static"

@api_router.get("/manifest.json")
async def serve_manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")

@api_router.get("/sw.js")
async def serve_sw():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={
        "Service-Worker-Allowed": "/api/",
        "Cache-Control": "no-cache",
    })

@api_router.get("/icon-192.png")
async def serve_icon_192():
    return FileResponse(STATIC_DIR / "icon-192.png", media_type="image/png")

@api_router.get("/icon-512.png")
async def serve_icon_512():
    return FileResponse(STATIC_DIR / "icon-512.png", media_type="image/png")

@api_router.get("/icon-maskable-512.png")
async def serve_icon_maskable():
    return FileResponse(STATIC_DIR / "icon-maskable-512.png", media_type="image/png")

@api_router.get("/apple-touch-icon.png")
async def serve_apple_icon():
    return FileResponse(STATIC_DIR / "apple-touch-icon.png", media_type="image/png")

@api_router.get("/source.zip")
async def serve_source_zip():
    return FileResponse(STATIC_DIR / "newsguess-source.zip", media_type="application/zip", filename="newsguess-source.zip")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

@api_router.get("/frontpage")
async def frontpage(slug: str = Query(...), date: str | None = Query(None)):
    base = f"https://www.giornalone.it/prima-pagina-{slug}/"
    if date:
        y, m, d = date.split("-")
        base += f"{y}/{m}/{d}/"
    try:
        page = requests.get(base, headers={"User-Agent": UA}, timeout=15)
        if page.status_code != 200:
            raise HTTPException(502, f"giornalone.it page HTTP {page.status_code}")
        m = re.search(r'og:image"[^>]*content="([^"]+)"', page.text)
        if not m:
            raise HTTPException(502, "og:image not found on page")
        img_url = m.group(1)
        img = requests.get(img_url, headers={"User-Agent": UA, "Referer": base}, timeout=20)
        if img.status_code != 200:
            raise HTTPException(502, f"image HTTP {img.status_code}")
        ct = img.headers.get("content-type", "image/webp")
        return Response(content=img.content, media_type=ct, headers={
            "Cache-Control": "public, max-age=3600",
            "X-Source-Url": img_url,
        })
    except requests.RequestException as e:
        raise HTTPException(502, f"fetch error: {e}")


# =====================================================================
# FIX 2: Prompt migliorato per trascrizione più accurata
# =====================================================================
PROMPT_HEADLINES = (
    "Sei un assistente che trascrive ESATTAMENTE il testo visibile sulle prime pagine di giornali italiani.\n\n"
    "Estrai dalla prima pagina mostrata:\n"
    "- sovratitolo (occhiello): la riga piccola sopra il titolo principale, se presente\n"
    "- titolo principale: il titolo più grande della notizia di apertura\n"
    "- sottotitolo: la riga sotto il titolo principale, se presente\n\n"
    "REGOLE IMPORTANTI:\n"
    "1. Copia il testo LETTERALMENTE, esattamente come appare nell'immagine\n"
    "2. NON correggere, NON interpretare, NON riformulare\n"
    "3. Rispetta maiuscole, minuscole e punteggiatura originali\n"
    "4. Per nomi propri, sigle, acronimi: copia esattamente (es. 'von der Leyen', 'MIT', 'M5S')\n"
    "5. Se una parola è difficile da leggere, trascrivi quello che vedi senza inventare\n\n"
    "Restituisci SOLO un JSON valido con queste chiavi esatte:\n"
    '{"sopratitolo": "..." | null, "titolo_principale": "...", "sottotitolo": "..." | null}\n'
    "Se sovratitolo o sottotitolo non esistono, usa null. Niente testo extra, solo il JSON."
)

def _fetch_frontpage_bytes(slug: str, date: str | None):
    base = f"https://www.giornalone.it/prima-pagina-{slug}/"
    if date:
        y, m, d = date.split("-")
        base += f"{y}/{m}/{d}/"
    page = requests.get(base, headers={"User-Agent": UA}, timeout=15)
    if page.status_code != 200:
        raise HTTPException(502, f"giornalone.it page HTTP {page.status_code}")
    m = re.search(r'og:image"[^>]*content="([^"]+)"', page.text)
    if not m:
        raise HTTPException(502, "og:image not found on page")
    img_url = m.group(1)
    img = requests.get(img_url, headers={"User-Agent": UA, "Referer": base}, timeout=20)
    if img.status_code != 200:
        raise HTTPException(502, f"image HTTP {img.status_code}")
    return img.content, img.headers.get("content-type", "image/webp").split(";")[0].strip()

def _quiz_today() -> str:
    rome = datetime.now(timezone.utc) + timedelta(hours=2)
    if rome.hour < 6:
        rome = rome - timedelta(days=1)
    return rome.strftime("%Y-%m-%d")

async def _try_fetch_frontpage_today(slug: str):
    """Tenta di scaricare la prima pagina di OGGI.
    Restituisce (bytes, content_type) oppure None se non ancora disponibile."""
    try:
        img_bytes, ct = _fetch_frontpage_bytes(slug, None)  # None = oggi
        return img_bytes, ct
    except Exception as e:
        logger.info(f"[UNAVAILABLE] {slug} non disponibile oggi: {e}")
        return None


@api_router.get("/extract-headlines")
@limiter.limit("30/minute")
async def extract_headlines(request: Request, slug: str = Query(...), date: str | None = Query(None)):
    """
    Fetch front page from giornalone.it e chiede a Claude di estrarre sovratitolo/titolo/sottotitolo.
    FIX 1: Se la prima pagina di oggi non è disponibile, usa silenziosamente l'archivio di ieri.
    FIX 2: Prompt migliorato per trascrizione più accurata.
    """
    today = _quiz_today()
    eff_date = date or today

    # Controlla cache MongoDB
    cache_key = {"slug": slug, "date": eff_date}
    cached = await db.headlines_archive.find_one(cache_key, {"_id": 0})
    if cached:
        return {
            "sopratitolo": cached.get("sopratitolo"),
            "titolo_principale": cached.get("titolo_principale", ""),
            "sottotitolo": cached.get("sottotitolo"),
            "from_archive": True,
        }

    # Se è una data passata non in archivio → errore 404
    if eff_date != today:
        raise HTTPException(404, f"Edizione del {eff_date} per {slug} non in archivio")

    # FIX 1: Prova a scaricare oggi.
    # Se non disponibile → 503 con unavailable=true.
    # Il frontend intercetta questo codice e sostituisce il giornale
    # con un altro dei 10 disponibili, senza dirlo all'utente.
    fetch_result = await _try_fetch_frontpage_today(slug)
    if fetch_result is None:
        logger.warning(f"[UNAVAILABLE] {slug}: prima pagina di oggi non ancora online, il frontend sostituirà con altro giornale")
        raise HTTPException(503, detail={"unavailable": True, "slug": slug,
            "message": f"Prima pagina di {slug} non ancora disponibile oggi."})

    img_bytes, _ct = fetch_result

    # Ridimensiona immagine per ottimizzare la chiamata a Claude
    try:
        from PIL import Image
        from io import BytesIO
        im = Image.open(BytesIO(img_bytes)).convert("RGB")
        max_side = 1600
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            im = im.resize((int(w*scale), int(h*scale)))
        out = BytesIO()
        im.save(out, format="JPEG", quality=85)
        img_bytes = out.getvalue()
    except Exception as e:
        logger.warning(f"image transcode failed, sending original: {e}")

    b64 = base64.b64encode(img_bytes).decode("ascii")
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(500, "EMERGENT_LLM_KEY non configurato")

    chat = LlmChat(
        api_key=api_key,
        session_id=f"headlines-{slug}-{eff_date}-{uuid.uuid4().hex[:6]}",
        system_message="Sei un assistente che trascrive esattamente il testo visibile sulle prime pagine di giornali italiani. Non interpretare, non correggere: copia letteralmente."
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")

    msg = UserMessage(text=PROMPT_HEADLINES, file_contents=[ImageContent(image_base64=b64)])
    try:
        resp = await chat.send_message(msg)
    except Exception as e:
        raise HTTPException(502, f"Claude error: {e}")

    text = resp if isinstance(resp, str) else str(resp)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise HTTPException(502, f"Claude reply not JSON: {text[:200]}")
    try:
        data = json.loads(m.group(0))
    except Exception:
        raise HTTPException(502, f"Claude JSON parse error: {text[:200]}")

    record = {
        "slug": slug,
        "date": eff_date,
        "sopratitolo": data.get("sopratitolo"),
        "titolo_principale": data.get("titolo_principale", ""),
        "sottotitolo": data.get("sottotitolo"),
        "createdAt": datetime.utcnow(),
    }
    try:
        await db.headlines_archive.update_one(cache_key, {"$set": record}, upsert=True)
        from bson.binary import Binary
        await db.headline_images.update_one(
            cache_key,
            {"$set": {**cache_key, "image": Binary(img_bytes), "createdAt": datetime.utcnow()}},
            upsert=True
        )
    except Exception as e:
        logger.warning(f"archive save failed: {e}")

    return {
        "sopratitolo": record["sopratitolo"],
        "titolo_principale": record["titolo_principale"],
        "sottotitolo": record["sottotitolo"],
        "from_archive": False,
    }


@api_router.get("/archive-dates")
async def archive_dates():
    today = _quiz_today()
    pipeline = [
        {"$match": {"date": {"$ne": today}}},
        {"$group": {"_id": "$date", "count": {"$sum": 1}}},
        {"$sort": {"_id": -1}},
        {"$limit": 60},
    ]
    out = []
    async for row in db.headlines_archive.aggregate(pipeline):
        out.append({"date": row["_id"], "papers": row["count"]})
    return {"dates": out}

@api_router.get("/archive-papers")
async def archive_papers(date: str = Query(...)):
    today = _quiz_today()
    if date == today:
        return {"date": date, "slugs": [], "items": []}
    cur = db.headlines_archive.find({"date": date}, {"_id": 0, "createdAt": 0})
    items = [doc async for doc in cur]
    slugs = [d["slug"] for d in items]
    return {"date": date, "slugs": slugs, "items": items}

@api_router.get("/archive-paper-dates")
async def archive_paper_dates(slug: str = Query(...)):
    today = _quiz_today()
    cur = db.headlines_archive.find(
        {"slug": slug, "date": {"$ne": today}},
        {"_id": 0, "createdAt": 0}
    ).sort("date", -1).limit(60)
    items = [doc async for doc in cur]
    return {"slug": slug, "items": items}

@api_router.get("/archive-image")
async def archive_image(slug: str = Query(...), date: str = Query(...)):
    today = _quiz_today()
    if date == today:
        raise HTTPException(404, "Immagine non disponibile per la data corrente")
    doc = await db.headline_images.find_one({"slug": slug, "date": date})
    if not doc or not doc.get("image"):
        raise HTTPException(404, "Immagine non in archivio")
    return Response(content=bytes(doc["image"]), media_type="image/jpeg", headers={
        "Cache-Control": "public, max-age=86400",
    })


@api_router.get("/backup.zip")
@limiter.limit("3/hour")
async def backup_zip(request: Request):
    import io as _io
    import zipfile as _zf
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, 'w', _zf.ZIP_DEFLATED) as zf:
        headlines = []
        async for doc in db.headlines_archive.find({}, {"_id": 0}):
            ca = doc.get("createdAt")
            if ca:
                doc["createdAt"] = ca.isoformat()
            headlines.append(doc)
        zf.writestr('headlines_archive.json',
                    json.dumps(headlines, indent=2, ensure_ascii=False, default=str))
        async for doc in db.headline_images.find({}):
            slug = doc.get("slug"); date = doc.get("date"); img = doc.get("image")
            if slug and date and img:
                zf.writestr(f'images/{date}/{slug}.jpg', bytes(img))
        zf.writestr('README.txt',
            f"Backup Newsguess archivio\n"
            f"Generato: {datetime.utcnow().isoformat()} UTC\n"
            f"Documenti titoli: {len(headlines)}\n\n"
            f"Per ripristinare:\n"
            f"  mongorestore --collection headlines_archive --db NEWSGUESS_DB headlines_archive.json\n"
            f"Le immagini sono singoli file jpg sotto images/<data>/<slug>.jpg\n")
    buf.seek(0)
    fname = f"newsguess-backup-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return Response(content=buf.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename={fname}'
    })


# ============= WEB PUSH NOTIFICATIONS =============
VAPID_FILE = STATIC_DIR / "vapid.json"
_vapid = None
def _load_vapid():
    global _vapid
    if _vapid is not None:
        return _vapid
    pub = os.environ.get("VAPID_PUBLIC_KEY")
    priv = os.environ.get("VAPID_PRIVATE_PEM")
    if pub and priv:
        _vapid = {"public": pub, "private_pem": priv.replace("\\n", "\n")}
        return _vapid
    if VAPID_FILE.exists():
        with open(VAPID_FILE) as f:
            _vapid = json.load(f)
    return _vapid

@api_router.get("/push-vapid-key")
async def push_vapid_key():
    v = _load_vapid()
    if not v:
        raise HTTPException(500, "VAPID keys not configured")
    return {"publicKey": v["public"]}

class PushSubscribePayload(BaseModel):
    subscription: dict
    tzOffsetMinutes: int
    hour: int = 8

@api_router.post("/push-subscribe")
@limiter.limit("10/minute")
async def push_subscribe(request: Request, payload: PushSubscribePayload):
    sub = payload.subscription
    endpoint = sub.get("endpoint")
    if not endpoint:
        raise HTTPException(400, "subscription.endpoint missing")
    doc = {
        "endpoint": endpoint,
        "subscription": sub,
        "tzOffsetMinutes": payload.tzOffsetMinutes,
        "hour": payload.hour,
        "createdAt": datetime.now(timezone.utc),
        "lastSentDate": None,
        "active": True,
    }
    await db.push_subs.update_one({"endpoint": endpoint}, {"$set": doc}, upsert=True)
    return {"ok": True}

@api_router.post("/push-unsubscribe")
async def push_unsubscribe(payload: dict):
    endpoint = payload.get("endpoint")
    if endpoint:
        await db.push_subs.delete_one({"endpoint": endpoint})
    return {"ok": True}

async def _send_push(sub_doc):
    v = _load_vapid()
    if not v:
        return
    try:
        webpush(
            subscription_info=sub_doc["subscription"],
            data=json.dumps({
                "title": "Newsguess · È utile essere aggiornati",
                "body": "La nuova sfida del giorno è pronta. Sei un Direttore emerito o un Lettore distratto?",
                "url": "/api/quiz",
            }),
            vapid_private_key=v["private_pem"],
            vapid_claims={"sub": "mailto:newsguess@example.com"},
            ttl=3600,
        )
        await db.push_subs.update_one(
            {"endpoint": sub_doc["endpoint"]},
            {"$set": {"lastSentDate": datetime.now(timezone.utc).strftime("%Y-%m-%d")}}
        )
    except WebPushException as e:
        logger.warning(f"push failed for {sub_doc['endpoint'][:60]}…: {e}")
        if "410" in str(e) or "404" in str(e):
            await db.push_subs.delete_one({"endpoint": sub_doc["endpoint"]})

async def _scheduled_check():
    now_utc = datetime.now(timezone.utc)
    cur = db.push_subs.find({"active": True})
    async for sub in cur:
        try:
            local = now_utc - timedelta(minutes=sub.get("tzOffsetMinutes", 0))
            target_hour = sub.get("hour", 8)
            if local.hour == target_hour and local.minute < 5:
                if sub.get("lastSentDate") != local.strftime("%Y-%m-%d"):
                    await _send_push(sub)
        except Exception as e:
            logger.warning(f"check err: {e}")

scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def _startup_scheduler():
    if not scheduler.running:
        scheduler.add_job(_scheduled_check, "interval", minutes=1, id="push_check", replace_existing=True)
        scheduler.start()
        logger.info("Push scheduler started (every minute, sends at user-local 8:00)")


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
