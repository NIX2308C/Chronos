# Chronos

An AI tutor that answers students only from material the teacher provides. Teachers create **classes**, fill each with their own rules and course docs, and share a join code. Students join a class and ask questions, and the tutor responds using just that class's material. Answers stay on the curriculum instead of wandering off into whatever the model happens to know.

## How it works

Teachers sign in, create one or more classes, and add knowledge to each (PDFs, Word docs, or typed rules). Each class is an isolated knowledge base: its material is embedded and stored in its own Pinecone **namespace**, so classes never bleed into each other. A student joins a class with its code, and when they ask something, Chronos pulls the most relevant pieces of *that class's* knowledge and hands them to Gemini, which writes an answer grounded only in those pieces. Every exchange is saved per student, and teachers see per-class analytics.

## Accounts and classes

- **Auth** is Firebase Email/Password. Teachers and students each have their own account and stay signed in across pages, so there's no re-login when switching panels.
- Registering as a **teacher** requires the `TEACHER_SIGNUP_CODE`. Everyone else is a student. Roles live in Firestore (`Users/{uid}.role`).
- **Teachers** create classes (each gets a shareable join code), manage that class's rules, and view its analytics.
- **Students** must join at least one class (via code) before they can use the tutor. They can join several and switch between them. Conversations are cloud-synced per account and scoped to the class they were started in.

## Stack

- Flask backend (`app.py`), served with Waitress
- Gemini for answers and embeddings
- Pinecone for vector search (one namespace per class)
- Firebase Authentication (email/password) plus Firestore (users, classes, chat logs)
- Static HTML pages styled with Tailwind (via CDN), with shared auth in `auth.js`

The front-end is a small set of static pages that share one dark design: indigo accents, Space Grotesk and Outfit type, and the same Chronos mark across every screen.

## Pages

- `/` is the landing page.
- `/login.html` handles sign in and sign up, for students and teachers.
- `/student.html` is the student tutor (you need to be in a class to use it).
- `/teacherknowledge.html` is where teachers create classes and manage their knowledge (teacher only).
- `/teacherstats.html` shows per-class analytics on student questions (teacher only).

## Running it locally

Install the dependencies:

```bash
pip install -r requirements.txt
```

Add a `.env` file with your keys:

```env
TEACHER_SIGNUP_CODE=your-strong-code     # required: gates teacher registration; the app won't start without it
GEMINI_API_KEY=...
PINECONE_API_KEY=...
FIREBASE_WEB_API_KEY=...                  # Firebase console > Project settings > Web app > apiKey
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_AUTH_DOMAIN=your-project-id.firebaseapp.com
```

The Pinecone index name is set in `app.py` (`INDEX_NAME`), so the API key is all you need in the environment. You also need to enable **Email/Password** sign-in in the Firebase console (Authentication > Sign-in method).

A few optional overrides exist too:

- `ALLOWED_ORIGINS` (comma-separated CORS allowlist)
- `MAX_UPLOAD_MB` (default 10)
- `CHAT_RATE_LIMIT` and `CHAT_RATE_WINDOW` (messages allowed per window, in seconds)
- `HISTORY_TURNS` (how many past turns the tutor remembers, default 20)
- `CHAT_MODEL` (the Gemini model, default `gemini-2.5-flash-lite`)
- `FLASK_DEBUG`

Retrieval is tunable as well. `RETRIEVAL_TOP_K` (default 5) sets how many knowledge chunks each answer draws on, and `RETRIEVAL_MIN_SCORE` (default 0.5, cosine) drops weakly related chunks, so off-topic questions get a truthful "not in my knowledge base" reply instead of being answered from the least-bad matches. Those unanswered questions then show up as **Knowledge Gaps** in the analytics. Firebase admin credentials are read from `firebase_credentials.json` locally, or the `FIREBASE_CREDENTIALS_JSON` env var when deployed.

Then start it:

```bash
waitress-serve --port=5000 app:app
```

and open http://localhost:5000.

## Deploying

There's a `render.yaml` (and a `Procfile`) set up for Render. Put the secrets in the Render dashboard rather than committing them. The Werkzeug debugger stays off unless you explicitly set `FLASK_DEBUG=1`.

**A note on memory:** the Google, Pinecone, and Firebase SDKs are heavy. Just importing them eats a few hundred MB, so there isn't much room to spare on a 512 MB box (Render's free and Starter plans). To avoid blowing past that on big files, uploads are processed in batches: the document is read, chunked, embedded, and pushed to Pinecone a little at a time instead of all at once, so memory stays roughly flat no matter how large the file is. `MAX_UPLOAD_MB` also defaults to 10. If you're still hitting out-of-memory errors when adding documents, drop that number lower or move up to a 2 GB instance.

## Endpoints

All endpoints below the auth layer expect a Firebase ID token in the `Authorization: Bearer <token>` header (the front-end attaches this automatically).

- **Auth and classes:** `/auth/config`, `/auth/register`, `/auth/me`, `/classes` (GET list, POST create), `/classes/join`, `DELETE /classes/<id>`.
- **Student (any signed-in user in the class):** `/chat`, `/chats`, `/chats/<id>/messages`, `DELETE /chats/<id>`, all class-scoped.
- **Teacher (owner of the class):** `/ingest`, `/upload`, `/rules`, `/delete_rule`, `/stats`, all take a `class_id`.
- `/health` is a plain health check.

Legacy rules from before classes existed are automatically migrated into the first class a teacher creates.
