# Chronos

An AI tutor that answers students only from material the teacher provides. Teachers upload their course docs or type in rules, students ask questions, and the tutor responds using just that material — so answers stay on-curriculum instead of wandering off into whatever the model knows.

## How it works

A teacher adds knowledge (PDFs, Word docs, or plain text), which gets embedded and stored in a vector index. When a student asks something, Chronos pulls the most relevant pieces of that knowledge by meaning and hands them to Gemini, which writes an answer grounded in only those pieces. Every exchange is logged so teachers can see what's being asked.

## Stack

- Flask backend (`app.py`), served with Waitress
- Gemini for answers and embeddings
- Pinecone for vector search
- Firebase Firestore for storage and chat logs
- Static HTML pages styled with Tailwind (via CDN)

## Pages

- `/` — landing page
- `/student.html` — the student tutor
- `/teacherknowledge.html` — upload and manage knowledge (password-protected)
- `/teacherstats.html` — analytics on student questions (password-protected)

## Running it locally

Install the dependencies:

```bash
pip install -r requirements.txt
```

Add a `.env` file with your keys:

```env
TEACHER_PASSWORD=your-strong-password   # required — the app won't start without it
GEMINI_API_KEY=...
PINECONE_API_KEY=...
PINECONE_HOST=...
```

A few optional overrides exist too (`ALLOWED_ORIGINS`, `MAX_UPLOAD_MB`, `CHAT_RATE_LIMIT`, `FLASK_DEBUG`). Firebase credentials are read from `firebase_credentials.json` locally, or the `FIREBASE_CREDENTIALS_JSON` env var when deployed.

Then start it:

```bash
waitress-serve --port=5000 app:app
```

and open http://localhost:5000.

## Deploying

There's a `render.yaml` (and a `Procfile`) set up for Render. Put the secrets in the Render dashboard rather than committing them. The Werkzeug debugger stays off unless you explicitly set `FLASK_DEBUG=1`.

## Endpoints

`/chat` is the public, rate-limited tutor endpoint. The teacher-only endpoints — `/ingest`, `/upload`, `/rules`, `/delete_rule`, `/stats` — all expect the `TEACHER_PASSWORD` in the request body. `/health` is a plain health check.
