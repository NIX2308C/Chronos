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
- Gemini for answers and embeddings (see `docs/OLLAMA.md` for the plan to move
  answers onto a local model; embeddings stay where they are)
- `profanity.py` for moderation and `student_profile.py` for per-student
  recollection — both deterministic, neither costs a second model call
- Pinecone for vector search (one namespace per course)
- Firebase Authentication (email/password) plus Firestore (users, courses in the legacy `Classes` collection, chat logs)
- Static HTML pages styled with Tailwind (via CDN), with shared auth in `auth.js`

The front-end is a small set of static pages that share one dark design: indigo accents, Space Grotesk and Outfit type, and the same Chronos mark across every screen.

## Repository layout

Everything the Flask app serves as a static file lives in `web/`. The URL paths
are unchanged by that (`/student.html`, `/mobile.css`, `/icons/…`), so the
relative links inside the pages need no knowledge of it — only `WEB_DIR` in
`app.py` does.

```
app.py                  the whole backend
profanity.py            conduct screening
student_profile.py      per-student memory
web/                    everything served to a browser
  *.html                the four pages
  auth.js theme.* transition.* mobile.*
  manifest.json icons/ .well-known/
tests/                  six plain-assert scripts
docs/                   ANDROID.md, OLLAMA.md, OLLAMA_TASK.md, COMMIT_LOG.md
android/                the Trusted Web Activity project
.github/workflows/      builds and releases the APK
```

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
- **Student files** never touch Pinecone. Assignment and rubric text is stored under that student's Firestore account, scoped to one course and one conversation, and supplied only as non-authoritative review context until the student removes it or deletes the conversation. The kind a student picks is checked against the file: one cheap classification call on the first `STUDENT_DOC_CHECK_CHARS` (default 2,500) characters refuses anything that reads as teaching material rather than their own work or its marking criteria, so a textbook chapter cannot enter the course as an "assignment". It runs last, after the rate limit and the per-conversation cap, and a refusal stores nothing; if the call itself fails the upload is accepted, because a model outage must not stop a student attaching the essay they are being marked on.
- **Current conversation** replays only the latest `HISTORY_TURNS` messages. Before older turns fall out, Chronos maintains a bounded tutoring-state summary on the chat document. It tracks progress, confusion, learning gaps, open questions, and unfinished work, but is explicitly forbidden as a source of facts.
- **Cross-conversation memory** is a bounded, course-scoped list of prior topics and explicit learning signals derived from chat metadata. It is never written to Pinecone.
- **The student profile** is the other half of that memory: how a student writes
  and what they keep coming back to, so the tutor pitches its answers at them
  rather than at an average. It is arithmetic, not a model call —
  `student_profile.py` turns each message into a few counters and renders the
  accumulated totals into two plain sentences. It lives at
  `Users/{uid}/Profiles/{class_id}`, where **the class id is the document id**:
  that is what makes course isolation structural rather than a filter someone has
  to remember, and `load_class_profile` re-asserts it on read anyway. Nothing is
  said at all until `PROFILE_MIN_MSGS` messages have been seen, blocked messages
  and bare acknowledgements are never counted, and the wording only ever
  describes how to explain something — never what the student is. Set
  `PROFILE_ENABLED=0` to turn the whole thing off.
- **System and teacher policy** comes from compact Base Rules and custom teacher rules. Custom rules are always added to the tutor prompt, never retrieved as facts. Prompt secrecy and jailbreak resistance are permanent system protections, not teacher toggles. Optional practice, visual and study-material tools are disabled by default. Course material itself is never optional: retrieved excerpts go to teachers only, with no course setting that can loosen it, so a student gets the answer and the "grounded" signal but never the material behind them.
- **Interactive tools** use two stages. A student can ask for one directly — practice chips sit under the newest answer, showing only the activities the course has enabled — or the tutor can request one itself by calling the `create_practice_activity` function declared on the chat request. Either way the client then shows a short “Creating…” card while a separate constrained Gemini call receives the relevant course material only and returns validated JSON for the quiz, flashcards, concept map, or review sheet. The server re-checks the requested type against the course settings on the way in, so the chips cannot reach a disabled toolkit.
- **Profanity** is caught before any of the above happens. `profanity.py`
  normalizes a message (accents, leetspeak, padding, letters spaced out) and
  then matches stems, so `f*ck`, `sh1t`, `fuuuck`, `f u c k` and `motherfucker`
  are all the same thing to it. A match short-circuits `/chat`: no embedding, no
  Pinecone query, no model call, and a fixed reply the student still sees in
  their history. The exchange is dropped from history replay and the rolling
  summary, so refusing to read it once doesn't just delay it by a turn. It
  reaches the teacher under **Behavioral concerns** and deliberately nowhere
  else — not Recent questions, Most repeated, the topic list, or Knowledge gaps,
  where an undetected swear used to land looking like uncovered course material.
  Detection errs towards letting mild words through: `dick`, `prick` and `hell`
  are not flagged, because Moby Dick is on reading lists and you prick a finger
  in biology, and blocking is outright — a false positive refuses real work.

The prompt orders these layers deliberately: system/base rules, retrieved teacher material, tutoring memory, and student work. Student text is always labelled untrusted and cannot promote itself into teacher-approved knowledge.

A few optional overrides exist too:

- `ALLOWED_ORIGINS` (comma-separated CORS allowlist; the default is local dev only, so set this in production)
- `MAX_UPLOAD_MB` (default 10) — the size on the wire
- `MAX_EXTRACT_BYTES` (default 200 MB) and `MAX_EXTRACT_CHARS` (default 2,000,000) — the size *after* decompression. A `.docx` is a zip and a `.pdf` holds compressed streams, so a small upload can inflate enormously; these bound what actually reaches memory. Over the character cap the file is indexed up to the limit and the response says so
- `CHAT_RATE_LIMIT` and `CHAT_RATE_WINDOW` (messages allowed per window, in seconds)
- `TOOL_RATE_LIMIT` and `TOOL_RATE_WINDOW` (learning activities per window; default 12 per minute). Separate from the chat budget on purpose: an activity almost always follows a chat turn, so sharing one bucket charged a student twice for a single interaction and throttled the activity half first
- `JOIN_RATE_LIMIT` and `JOIN_RATE_WINDOW` (course-code attempts per window; default 10 per 5 minutes)
- `REGISTER_RATE_LIMIT` and `REGISTER_RATE_WINDOW` (teacher-code attempts per window; default 5 per 15 minutes)
- `HISTORY_TURNS` (how many past turns the tutor remembers, default 20)
- `MAX_MESSAGES_RETURNED` (cap on messages returned for one conversation, default 500)
- `ROLE_CACHE_TTL` (seconds a user's role is cached in-process, default 60)
- `TRUST_PROXY_HOPS` (default 0; set to 1 on Cloud Run so client IPs in the logs are real)
- `CHAT_MODEL` (the Gemini model, default `gemini-2.5-flash-lite`)
- `TOOL_MODEL` (the model that builds learning activities; defaults to `CHAT_MODEL`)
- `FLASK_DEBUG`

Rate limits are keyed by Firebase uid, not IP — behind a load balancer every request shares one IP, so an IP-keyed limit would throttle a whole course as though it were a single student. Teacher registration is the one exception, and it's keyed by IP: the attacker there isn't a signed-in student but anyone who can make a Firebase account, which is free and unlimited, so a per-account budget would reset on every guess.

Retrieval is tunable as well. `RETRIEVAL_TOP_K` (default 5) sets how many knowledge chunks each answer draws on, and `RETRIEVAL_MIN_SCORE` (default 0.5, cosine) drops weakly related chunks. Unanswered questions become **material gaps**; explicit confusion becomes a separate learning signal, and conduct concerns are reported separately. Firebase admin credentials are read from `firebase_credentials.json` locally, or the `FIREBASE_CREDENTIALS_JSON` env var when deployed.

Then start it:

```bash
waitress-serve --port=5000 app:app
```

and open http://localhost:5000.

## Tests

```bash
python tests/test_profanity.py && python tests/test_security.py && \
python tests/test_stats_grouping.py && python tests/test_student_context.py && \
python tests/test_student_profile.py && python tests/test_quiz_answers.py
```

Six plain-`assert` scripts, no test runner. They stub every collaborator that
would reach Firestore, Pinecone or Gemini, so they run offline in about a second
and need no real keys — but `app.py` still refuses to import without a valid
`TEACHER_SIGNUP_CODE`, so a `.env` with a throwaway one (and dummy values for the
rest) has to exist. They cover the auth gate, the teacher signup code and the
throttles in front of it, the `.docx` decompression cap, that course material is
never sent to a student, the analytics grouping, the shape of the tutor's
assembled prompt, and profanity detection — including the half of that which
matters most, that ordinary classroom English (`class`, `assess`, `cockpit`,
`Scunthorpe`) is never flagged. They also cover the student profile: that its
counters are additive (they are stored as Firestore increments, so a drift here
would be silent and permanent), that it says nothing before it has evidence, that
it never leaks a raw count or a score into something a teacher reads, and — the
one the whole feature turns on — that a profile built in one course is
unreachable from another. `test_quiz_answers.py` covers the quiz answer key: that
every shape a model actually emits for it (a letter, a quoted digit, a renamed
field, the answer's own text) resolves to the right option, that one it cannot
resolve drops the question instead of guessing, and that the correct answer is no
longer always option A. `test_profanity.py` and `test_student_profile.py` import
their module alone and need no environment at all. Run them before committing; they are fast enough that there is
no excuse not to.

## Planning documents

Three things are written down but not built. Each says what it is waiting on:

- `docs/ANDROID.md` — shipping the app on Android as a Trusted Web Activity. Phase A
  (making the pages usable on a phone) is the blocker and is pure web work.
- `docs/OLLAMA.md` — moving answer generation to a local model. The decision record:
  what can move, what cannot, and the hosting choice that blocks the rest.
- `docs/OLLAMA_TASK.md` — the implementation brief for whoever (or whatever) does
  that work: exact call sites, the traps, and what "done" means. Written for an
  AI coding agent picking it up cold. Read `docs/OLLAMA.md` first.

`docs/COMMIT_LOG.md` is the informal running history of what changed and why.

## Deploying

Chronos runs on **Google Cloud Run**, built from the `Dockerfile` in this repo and deployed continuously from GitHub — pushing to the default branch triggers a build and rollout. Cloud Run injects `$PORT` (the Dockerfile defaults it to 8080) and the container serves with Waitress.

Configuration goes in the Cloud Run service, not in the repo:

- **Secrets** — `TEACHER_SIGNUP_CODE`, `GEMINI_API_KEY`, `PINECONE_API_KEY`, `FIREBASE_CREDENTIALS_JSON` (the full contents of `firebase_credentials.json`). Use Secret Manager and expose them to the service as environment variables rather than plain env vars, so they aren't readable from the service description.
- **Plain env vars** — `FLASK_DEBUG=0`, `TRUST_PROXY_HOPS=1` (Cloud Run puts exactly one proxy in front of you, so trusting that single hop gives real client IPs without letting anyone forge `X-Forwarded-For`), `ALLOWED_ORIGINS` set to your real front-end origin, and the `FIREBASE_*` web config values.

### Status page deployment access

`/status` is public and reports only coarse service health. Its developer-only deployment panel needs `CLOUD_STATUS_PROJECT_ID`, `CLOUD_STATUS_REGION`, `CLOUD_STATUS_SERVICE`, and `CLOUD_STATUS_BUILD_TRIGGER_ID` set on the Cloud Run service. Grant its runtime service account `roles/cloudbuild.builds.viewer` and `roles/run.viewer`, and enable the Cloud Build and Cloud Run Admin APIs. The panel is visible only to verified Firebase accounts listed in `DEV_EMAILS`; it uses the runtime identity and never sends Google credentials to the browser.

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
- `/status/public` is a public, coarse health snapshot; `/status/deployment` is developer-only Cloud Build/Run state.

Legacy rules from before courses existed are automatically migrated into the first course a teacher creates.
