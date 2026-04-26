# 🚀 Newsguess — Deploy su Render.com (gratis, niente carta di credito)

Guida passo-passo, **solo via interfaccia web**. Niente terminale.

## ✅ Cosa ti serve
- Account **GitHub** (gratis, no CC) — https://github.com/signup
- Account **Render** (gratis, no CC) — https://render.com (sign up con GitHub, è il modo più rapido)
- Account **MongoDB Atlas** con cluster M0 free + connection string `mongodb+srv://...`
- Le tue chiavi (te le ho già fornite in chat):
  - `EMERGENT_LLM_KEY`
  - `VAPID_PUBLIC_KEY` + `VAPID_PRIVATE_KEY`

---

## 1️⃣ Carica il codice su GitHub (5 min, tutto via browser)

1. Vai su https://github.com/new
2. **Repository name**: `newsguess`
3. Lascia **Public** (necessario per il piano free di Render senza CC)
4. Spunta **"Add a README file"**
5. Click **Create repository**
6. Sulla pagina del repo appena creato click **Add file → Upload files**
7. **Trascina** la cartella scompattata di `newsguess-source.zip` (tutti i file insieme: `backend/`, `static/`, `quiz.html`, `render.yaml`, `Procfile`, `runtime.txt`)
8. In fondo alla pagina click **Commit changes**

## 2️⃣ Configura MongoDB Atlas per Render (1 min)

1. Apri https://cloud.mongodb.com → **Network Access** (menu sinistro)
2. Click **Add IP Address** → **Allow Access from Anywhere** (`0.0.0.0/0`) → Confirm
   *(Render usa IP dinamici, quindi serve aprirlo a tutti — la sicurezza è data dall'utente/password)*
3. Vai su **Database** → click **Connect** sul tuo cluster → **Drivers** → copia la connection string (`mongodb+srv://...`)
4. Sostituisci `<password>` con la password del database user

## 3️⃣ Deploy su Render (3 min)

1. Vai su https://dashboard.render.com
2. Click **New +** (in alto a dx) → **Blueprint**
3. Click **Connect GitHub** se è la prima volta, autorizza Render ad accedere al repo `newsguess`
4. Seleziona il repo `newsguess` → click **Connect**
5. Render legge il `render.yaml` e mostra il servizio `newsguess`
6. Click **Apply**
7. Render ti chiederà i valori delle variabili ("Environment Variables"). Inseriscili così:

   | Chiave | Valore |
   |---|---|
   | `MONGO_URL` | `mongodb+srv://USER:PASSWORD@cluster0...mongodb.net/?retryWrites=true&w=majority` |
   | `EMERGENT_LLM_KEY` | `sk-emergent-7DeA0Db7aAaCe6859D` |
   | `VAPID_PUBLIC_KEY` | `BKNvjLquh3ec7t7O_IRp5NrLWq3IMTIUFpjq_-ZyZdm676Yx2fASRZNWCoelLorpZ2TLwACKNE-BOMzQ8JjNaXg` |
   | `VAPID_PRIVATE_KEY` | `AYziHbh_r1hbWvNesDdmFG-yhCTyBu1nxcCYAOaEjY4` |
   | `VAPID_CONTACT_EMAIL` | `mailto:tu@example.com` (la tua email) |

8. Click **Apply** → parte il build (3-5 min). Vedrai il log scorrere.
9. Quando appare `Live` con il pallino verde è fatto. URL pubblico tipo `https://newsguess.onrender.com`
10. Apri `https://newsguess.onrender.com/api/quiz` → l'app è online! 🎉

## 4️⃣ Tieni sveglia l'app per le push delle 08:00 (cron-job.org, gratis, no CC)

Il piano free di Render addormenta l'app dopo 15 min di inattività. Per fare partire le notifiche alle 8:00 dobbiamo "svegliarla" prima.

1. Vai su https://cron-job.org → sign up (no CC)
2. **CREATE CRONJOB**
3. **Title**: `Newsguess wakeup mattina`
4. **URL**: `https://newsguess.onrender.com/api/wakeup` (sostituisci con il tuo URL Render)
5. **Execution schedule** → **Every X minutes** → ogni **10 minuti**
6. Click **Advanced** → **Schedule restrictions**:
   - **Hours**: spunta solo `6, 7, 8, 9` (UTC) → corrisponde a 7-10 ora italiana
   - *(In estate con ora legale è 8-11, va bene comunque, copre la finestra 08:00 IT)*
7. **CREATE**

Fatto! L'app sarà sveglia tutte le mattine quando lo scheduler interno deve mandare le push alle 08:00 locali degli utenti.

## 5️⃣ Aggiorna il manifest PWA

Opzionale ma consigliato: cambia eventuali URL hardcoded. Apri `quiz.html` su GitHub (matita ✏️ in alto a dx del file), cerca menzioni a `preview.emergentagent.com` e sostituiscile con il tuo URL Render. Salva → Render rideploya in automatico.

## ❓ Problemi?

- **Build fallisce con "emergentintegrations not found"** → in `backend/requirements.txt` la riga `emergentintegrations==0.1.0` deve restare. Render la scarica dall'index pubblico.
- **App parte ma `/api/quiz` dà 500** → controlla i log Render (Logs tab). Probabile `MONGO_URL` errato o IP non whitelisted.
- **Push non arrivano** → verifica che cron-job.org stia pingando `/api/wakeup` (vedi tab Executions, devono essere tutti 200 OK).
