# Deploying Teacher AI

The app runs as a **single Flask service** that serves both the API and the three
HTML pages. That keeps everything same-origin (no CORS issues) and gives you HTTPS
automatically — which matters because the teacher password is sent in the request body.

These instructions target **Render** (free tier), but the same files work on Railway,
Fly.io, or any host that reads a `Procfile`.

---

## 1. Push to GitHub

```bash
git init
git add .
git commit -m "Teacher AI"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Before committing, confirm secrets are NOT staged:

```bash
git status   # .env and firebase_credentials.json must NOT appear
```

They're excluded by `.gitignore`. If they show up, stop and fix it before pushing.

## 2. Create the service on Render

1. Go to <https://render.com> → **New → Web Service** → connect your repo.
2. Render auto-detects `render.yaml`. If asked manually:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `waitress-serve --port=$PORT app:app`
   - **Instance type:** Free

## 3. Set environment variables (Render dashboard → Environment)

| Variable | Value |
|----------|-------|
| `TEACHER_PASSWORD` | a long, random password |
| `GEMINI_API_KEY` | your Gemini key |
| `PINECONE_API_KEY` | your Pinecone key |
| `PINECONE_HOST` | your Pinecone host |
| `FIREBASE_CREDENTIALS_JSON` | the **entire contents** of `firebase_credentials.json`, pasted as one value |
| `FLASK_DEBUG` | `0` (leave off — never enable in production) |

> `FIREBASE_CREDENTIALS_JSON`: open `firebase_credentials.json`, copy everything
> (the whole `{ ... }`), and paste it as the value. The app parses it at startup.

## 4. Deploy

Render builds and starts the service. When it's live you'll get a URL like
`https://teacher-ai.onrender.com`:

- Student chat:   `https://teacher-ai.onrender.com/`
- Knowledge base: `https://teacher-ai.onrender.com/teacherknowledge.html`
- Analytics:      `https://teacher-ai.onrender.com/teacherstats.html`

---

## Notes

- **Cold starts:** the free tier sleeps after ~15 min idle; the first request then
  takes ~30s to wake. Fine for classroom use; upgrade to a paid instance to avoid it.
- **Secrets:** if these keys were ever committed or shared, rotate them (Firebase
  service account, Gemini, Pinecone). `.gitignore` only prevents *future* leaks.
- **Local dev is unchanged:** run `python app.py` (loopback, port 5000). Opening the
  pages via Live Server (port 5500) still talks to the dev server; served by Flask it's
  same-origin. Set `FLASK_DEBUG=1` locally if you want auto-reload.
- **CORS:** not needed in this single-service setup. If you ever host the HTML
  separately, set `ALLOWED_ORIGINS` to that origin.
