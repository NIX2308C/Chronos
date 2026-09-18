# Chronos

An AI tutor that answers students only from material the teacher provides. Teachers create **courses**, fill each with their own rules and course docs, and share a join code. Students join a course and ask questions, and the tutor responds using just that course's material. Answers stay on the curriculum instead of wandering off into whatever the model happens to know.

## How it works

Teachers sign in, create one or more courses, and add course material to each (PDFs, Word docs, or text files). Each course is an isolated knowledge base: its material is embedded and stored in its own Pinecone **namespace**, so courses never bleed into each other. Custom teacher rules are stored as direct prompt policy instead of vectors. A student joins a course with its code, and when they ask something, Chronos pulls the most relevant pieces of *that course's* knowledge and hands them to Gemini, which writes an answer grounded only in those pieces. Every exchange is saved per student, and teachers see per-course analytics.

## Accounts and courses

- **Auth** is Firebase Email/Password. Teachers and students each have their own account and stay signed in across pages, so there's no re-login when switching panels.
- Registering as a **teacher** requires the `TEACHER_SIGNUP_CODE`. Everyone else is a student. Roles live in Firestore (`Users/{uid}.role`).
- Signing in and registering are two separate steps: the browser creates the Firebase account, then `/auth/register` assigns the role. An account with one but not the other is a half-finished signup — it can reach nothing except `/auth/register`, and a wrong teacher code deletes it server-side instead of leaving it stranded.
- **Teachers** create courses (each gets a shareable join code), manage that course's rules, and view its analytics.
- **Students** must join at least one course (via code) before they can use the tutor. They can join several and switch between them. Conversations are cloud-synced per account and scoped to the course they were started in.

## Stack

- Flask backend (`app.py`), served with Waitress
- Gemini for answers and embeddings
- Pinecone for vector search (one namespace per course)
- Firebase Authentication (email/password) plus Firestore (users, courses in the legacy `Classes` collection, chat logs)
- Static HTML pages styled with Tailwind (via CDN), with shared auth in `auth.js`

The front-end is a small set of static pages that share one dark design: indigo accents, Space Grotesk and Outfit type, and the same Chronos mark across every screen.

## Pages

- `/` is the sign-in page (there is no separate landing page; `/landing.html`
  redirects here so old links still work).
- `/login.html` handles sign in and sign up, for students and teachers. A user
  who is already signed in is sent straight on to their own panel.
- `/student.html` is the student tutor (you need to be in a course to use it).
- `/teacherknowledge.html` is where teachers create courses and manage their knowledge (teacher only).
- `/teacherstats.html` shows per-course analytics on student questions (teacher only).

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

## AI context and data boundaries

Audit note: before the current separation, Pinecone contained teacher-entered rules as well as teacher uploads. Student assignments/rubrics were kept in Firestore and injected whole, but they were scoped only to a course, so they followed the student into every conversation in that course. Long chats replayed the latest 20 messages and had no rolling summary; the older cross-chat “memory” held only opening topics and retrieval misses. The behavior below removes prompt policy from retrieval and replaces those weak spots.

- **Teacher course knowledge** is the only content embedded into Pinecone: teacher-uploaded document chunks use the course's namespace; retrieval sends at most the configured top matches and character budget to Gemini. Existing legacy typed-rule vectors are moved to custom policy and deleted from the namespace the next time a teacher opens that course.
- **Student files** never touch Pinecone. Assignment and rubric text is stored under that student's Firestore account, scoped to one course and one conversation, and supplied only as non-authoritative review context until the student removes it or deletes the conversation.
- **Current conversation** replays only the latest `HISTORY_TURNS` messages. Before older turns fall out, Chronos maintains a bounded tutoring-state summary on the chat document. It tracks progress, confusion, learning gaps, open questions, and unfinished work, but is explicitly forbidden as a source of facts.
- **Cross-conversation memory** is a bounded, course-scoped list of prior topics and explicit learning signals derived from chat metadata. It is never written to Pinecone.
- **System and teacher policy** comes from compact Base Rules and custom teacher rules. Custom rules are always added to the tutor prompt, never retrieved as facts. Prompt secrecy and jailbreak resistance are permanent system protections, not teacher toggles. Optional practice, visual, study-material, and source-display tools are disabled by default.
- **Interactive tools** use two stages: the tutor writes its visible lead-in and requests an enabled tool with a hidden marker; the client then shows a short “Creating…” card while a separate constrained Gemini call receives relevant teacher excerpts only and returns validated JSON for the quiz, flashcards, concept map, or review sheet.

The prompt orders these layers deliberately: system/base rules, retrieved teacher material, tutoring memory, and student work. Student text is always labelled untrusted and cannot promote itself into teacher-approved knowledge.

A few optional overrides exist too:

- `ALLOWED_ORIGINS` (comma-separated CORS allowlist; the default is local dev only, so set this in production)
- `MAX_UPLOAD_MB` (default 10) — the size on the wire
- `MAX_EXTRACT_BYTES` (default 200 MB) and `MAX_EXTRACT_CHARS` (default 2,000,000) — the size *after* decompression. A `.docx` is a zip and a `.pdf` holds compressed streams, so a small upload can inflate enormously; these bound what actually reaches memory. Over the character cap the file is indexed up to the limit and the response says so
- `CHAT_RATE_LIMIT` and `CHAT_RATE_WINDOW` (messages allowed per window, in seconds)
- `JOIN_RATE_LIMIT` and `JOIN_RATE_WINDOW` (course-code attempts per window; default 10 per 5 minutes)
- `REGISTER_RATE_LIMIT` and `REGISTER_RATE_WINDOW` (teacher-code attempts per window; default 5 per 15 minutes)
- `HISTORY_TURNS` (how many past turns the tutor remembers, default 20)
- `MAX_MESSAGES_RETURNED` (cap on messages returned for one conversation, default 500)
- `ROLE_CACHE_TTL` (seconds a user's role is cached in-process, default 60)
- `TRUST_PROXY_HOPS` (default 0; set to 1 on Cloud Run so client IPs in the logs are real)
- `CHAT_MODEL` (the Gemini model, default `gemini-2.5-flash-lite`)
- `FLASK_DEBUG`

Rate limits are keyed by Firebase uid, not IP — behind a load balancer every request shares one IP, so an IP-keyed limit would throttle a whole course as though it were a single student. Teacher registration is the one exception, and it's keyed by IP: the attacker there isn't a signed-in student but anyone who can make a Firebase account, which is free and unlimited, so a per-account budget would reset on every guess.

Retrieval is tunable as well. `RETRIEVAL_TOP_K` (default 5) sets how many knowledge chunks each answer draws on, and `RETRIEVAL_MIN_SCORE` (default 0.5, cosine) drops weakly related chunks. Unanswered questions become **material gaps**; explicit confusion becomes a separate learning signal, and conduct concerns are reported separately. Firebase admin credentials are read from `firebase_credentials.json` locally, or the `FIREBASE_CREDENTIALS_JSON` env var when deployed.

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

- **Auth and courses:** `/auth/config`, `/auth/register`, `/auth/me`, `/classes` (legacy API name; GET list, POST create), `/classes/join`, `DELETE /classes/<id>`.
- **Student (any signed-in user in the course):** `/chat`, `/chats`, `/chats/<id>/messages`, `DELETE /chats/<id>`, all course-scoped.
- **Teacher (owner of the course):** `/ingest`, `/upload`, `/rules`, `/delete_rule`, `/stats`, and `/course-settings`; these retain the legacy `class_id` field.
- `/health` is a plain health check.

Legacy rules from before courses existed are automatically migrated into the first course a teacher creates.
