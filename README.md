# Chronos

An AI tutor that answers students only from material the teacher provides. Teachers create **classes**, fill each with their own rules and course docs, and share a join code. Students join a class and ask questions, and the tutor responds using just that class's material. Answers stay on the curriculum instead of wandering off into whatever the model happens to know.

## How it works

Teachers sign in, create one or more classes, and add knowledge to each (PDFs, Word docs, or typed rules). Each class is an isolated knowledge base: its material is embedded and stored in its own Pinecone **namespace**, so classes never bleed into each other. A student joins a class with its code, and when they ask something, Chronos pulls the most relevant pieces of *that class's* knowledge and hands them to Gemini, which writes an answer grounded only in those pieces. Every exchange is saved per student, and teachers see per-class analytics.

## Accounts and classes

- **Auth** is Firebase Email/Password. Teachers and students each have their own account and stay signed in across pages, so there's no re-login when switching panels.
- Registering as a **teacher** requires the `TEACHER_SIGNUP_CODE`. Everyone else is a student. Roles live in Firestore (`Users/{uid}.role`).
- Signing in and registering are two separate steps: the browser creates the Firebase account, then `/auth/register` assigns the role. An account with one but not the other is a half-finished signup — it can reach nothing except `/auth/register`, and a wrong teacher code deletes it server-side instead of leaving it stranded.
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

- `ALLOWED_ORIGINS` (comma-separated CORS allowlist; the default is local dev only, so set this in production)
- `MAX_UPLOAD_MB` (default 10) — the size on the wire
- `MAX_EXTRACT_BYTES` (default 200 MB) and `MAX_EXTRACT_CHARS` (default 2,000,000) — the size *after* decompression. A `.docx` is a zip and a `.pdf` holds compressed streams, so a small upload can inflate enormously; these bound what actually reaches memory. Over the character cap the file is indexed up to the limit and the response says so
- `CHAT_RATE_LIMIT` and `CHAT_RATE_WINDOW` (messages allowed per window, in seconds)
- `JOIN_RATE_LIMIT` and `JOIN_RATE_WINDOW` (class-code attempts per window; default 10 per 5 minutes)
- `REGISTER_RATE_LIMIT` and `REGISTER_RATE_WINDOW` (teacher-code attempts per window; default 5 per 15 minutes)
- `HISTORY_TURNS` (how many past turns the tutor remembers, default 20)
- `MAX_MESSAGES_RETURNED` (cap on messages returned for one conversation, default 500)
- `ROLE_CACHE_TTL` (seconds a user's role is cached in-process, default 60)
- `TRUST_PROXY_HOPS` (default 0; set to 1 on Cloud Run so client IPs in the logs are real)
- `CHAT_MODEL` (the Gemini model, default `gemini-2.5-flash-lite`)
- `FLASK_DEBUG`

Rate limits are keyed by Firebase uid, not IP — behind a load balancer every request shares one IP, so an IP-keyed limit would throttle a whole class as though it were a single student. Teacher registration is the one exception, and it's keyed by IP: the attacker there isn't a signed-in student but anyone who can make a Firebase account, which is free and unlimited, so a per-account budget would reset on every guess.

Retrieval is tunable as well. `RETRIEVAL_TOP_K` (default 5) sets how many knowledge chunks each answer draws on, and `RETRIEVAL_MIN_SCORE` (default 0.5, cosine) drops weakly related chunks, so off-topic questions get a truthful "not in my knowledge base" reply instead of being answered from the least-bad matches. Those unanswered questions then show up as **Knowledge Gaps** in the analytics. Firebase admin credentials are read from `firebase_credentials.json` locally, or the `FIREBASE_CREDENTIALS_JSON` env var when deployed.

Then start it:

```bash
waitress-serve --port=5000 app:app
```

and open http://localhost:5000.

## Deploying

Chronos runs on **Google Cloud Run**, built from the `Dockerfile` in this repo and deployed continuously from GitHub — pushing to the default branch triggers a build and rollout. Cloud Run injects `$PORT` (the Dockerfile defaults it to 8080) and the container serves with Waitress.

Configuration goes in the Cloud Run service, not in the repo:

- **Secrets** — `TEACHER_SIGNUP_CODE`, `GEMINI_API_KEY`, `PINECONE_API_KEY`, `FIREBASE_CREDENTIALS_JSON` (the full contents of `firebase_credentials.json`). Use Secret Manager and expose them to the service as environment variables rather than plain env vars, so they aren't readable from the service description.
- **Plain env vars** — `FLASK_DEBUG=0`, `TRUST_PROXY_HOPS=1` (Cloud Run puts exactly one proxy in front of you, so trusting that single hop gives real client IPs without letting anyone forge `X-Forwarded-For`), `ALLOWED_ORIGINS` set to your real front-end origin, and the `FIREBASE_*` web config values.

The Werkzeug debugger stays off unless you explicitly set `FLASK_DEBUG=1`. If `ALLOWED_ORIGINS` is left on the local-dev default, or `TRUST_PROXY_HOPS` is still 0, the app logs a warning at startup — the first means your front-end will be CORS-blocked, the second means the teacher-code rate limit sees the load balancer's IP for every caller and throttles globally instead of per client.

**Firestore rules** live in `firestore.rules` and deny all client access (`allow read, write: if false`). That is deliberate and load-bearing: nothing in the browser talks to Firestore, so every read and write goes through `app.py` via the Admin SDK, which bypasses rules. Relaxing them to the usual `if request.auth != null` would let any signed-in student write their own `Users/{uid}.role` and make themselves a teacher, bypassing `TEACHER_SIGNUP_CODE` entirely. Deploy with `firebase deploy --only firestore:rules`.

**A note on memory:** the Google, Pinecone, and Firebase SDKs are heavy. Just importing them eats a few hundred MB, so there isn't much room to spare on Cloud Run's 512 MiB default instance. To avoid blowing past that on big files, uploads are processed in batches: the document is read, chunked, embedded, and pushed to Pinecone a little at a time instead of all at once, so memory stays roughly flat no matter how large the file is. `MAX_UPLOAD_MB` also defaults to 10. If you're still hitting out-of-memory errors when adding documents, drop that number lower or raise the service's memory limit.

Because Cloud Run scales to zero and can run several instances at once, note that the rate limiter is per-process in-memory — it resets on a cold start and isn't shared between instances. It's a courtesy throttle, not a hard guarantee; a real limit needs shared state or Cloud Armor.

## Endpoints

All endpoints below the auth layer expect a Firebase ID token in the `Authorization: Bearer <token>` header (the front-end attaches this automatically).

- **Auth and classes:** `/auth/config`, `/auth/register`, `/auth/me`, `/classes` (GET list, POST create), `/classes/join`, `DELETE /classes/<id>`.
- **Student (any signed-in user in the class):** `/chat`, `/chats`, `/chats/<id>/messages`, `DELETE /chats/<id>`, all class-scoped.
- **Teacher (owner of the class):** `/ingest`, `/upload`, `/rules`, `/delete_rule`, `/stats`, all take a `class_id`.
- `/health` is a plain health check.

Legacy rules from before classes existed are automatically migrated into the first class a teacher creates.
