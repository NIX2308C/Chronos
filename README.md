# Chronos

An AI tutor that answers students only from material the teacher provides. Teachers create **classes**, fill each with their own rules and course docs, and share a join code. Students join a class and ask questions; the tutor responds using just that class's material — so answers stay on-curriculum instead of wandering off into whatever the model knows.

## How it works

Teachers sign in, create one or more classes, and add knowledge to each (PDFs, Word docs, or typed rules). Each class is an isolated knowledge base: its material is embedded and stored in its own Pinecone **namespace**, so classes never bleed into each other. A student joins a class with its code; when they ask something, Chronos pulls the most relevant pieces of *that class's* knowledge and hands them to Gemini, which writes an answer grounded only in those pieces. Every exchange is saved per student, and teachers see per-class analytics.

## Accounts & classes

- **Auth** is Firebase Email/Password. Teachers and students each have their own account and stay signed in across pages (no re-login when switching panels).
- Registering as a **teacher** requires the `TEACHER_SIGNUP_CODE`; everyone else is a student. Roles are stored in Firestore (`Users/{uid}.role`).
- **Teachers** create classes (each gets a shareable join code), manage that class's rules, and view its analytics.
- **Students** must join at least one class (via code) before they can use the tutor. They can join several and switch between them; conversations are cloud-synced per account and scoped to the class they were started in.

## Stack

- Flask backend (`app.py`), served with Waitress
- Gemini for answers and embeddings
- Pinecone for vector search (one namespace per class)
- Firebase Authentication (email/password) + Firestore (users, classes, chat logs)
- Static HTML pages styled with Tailwind (via CDN); shared auth in `auth.js`

## Pages

- `/` — landing page
- `/login.html` — sign in / sign up (student or teacher)
- `/student.html` — the student tutor (requires being in a class)
- `/teacherknowledge.html` — create classes and manage their knowledge (teacher only)
- `/teacherstats.html` — per-class analytics on student questions (teacher only)

## Running it locally

Install the dependencies:

```bash
pip install -r requirements.txt
```

Add a `.env` file with your keys:

```env
TEACHER_SIGNUP_CODE=your-strong-code     # required — gates teacher registration; app won't start without it
GEMINI_API_KEY=...
PINECONE_API_KEY=...
PINECONE_HOST=...
FIREBASE_WEB_API_KEY=...                  # Firebase console → Project settings → Web app → apiKey
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_AUTH_DOMAIN=your-project-id.firebaseapp.com
```

You must also enable **Email/Password** sign-in in the Firebase console (Authentication → Sign-in method). A few optional overrides exist too (`ALLOWED_ORIGINS`, `MAX_UPLOAD_MB`, `CHAT_RATE_LIMIT`, `FLASK_DEBUG`). Firebase admin credentials are read from `firebase_credentials.json` locally, or the `FIREBASE_CREDENTIALS_JSON` env var when deployed.

Then start it:

```bash
waitress-serve --port=5000 app:app
```

and open http://localhost:5000.

## Deploying

There's a `render.yaml` (and a `Procfile`) set up for Render. Put the secrets in the Render dashboard rather than committing them. The Werkzeug debugger stays off unless you explicitly set `FLASK_DEBUG=1`.

## Endpoints

All endpoints below the auth layer expect a Firebase ID token in the `Authorization: Bearer <token>` header (the front-end attaches this automatically).

- **Auth/classes:** `/auth/config`, `/auth/register`, `/auth/me`, `/classes` (GET list, POST create), `/classes/join`, `DELETE /classes/<id>`.
- **Student (any signed-in user, in the class):** `/chat`, `/chats`, `/chats/<id>/messages`, `DELETE /chats/<id>` — all class-scoped.
- **Teacher (owner of the class):** `/ingest`, `/upload`, `/rules`, `/delete_rule`, `/stats` — all take a `class_id`.
- `/health` is a plain health check.

Legacy rules from before classes existed are automatically migrated into the first class a teacher creates.
