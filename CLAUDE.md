# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Chronos: an AI tutor that answers students only from material their teacher provides. Teachers create classes, add course material/rules to each, and share a join code; students join a class and ask questions, and answers are grounded only in that class's material (RAG over a per-class Pinecone namespace), each with a citation back to the source. Off-topic questions get a truthful "not in my knowledge base" reply instead of a hallucinated one, and those show up as "Knowledge Gaps" in teacher analytics.

## Running it locally

```bash
pip install -r requirements.txt
waitress-serve --port=5000 app:app
```

Requires a `.env` file (see `README.md` for the full list of vars). `TEACHER_SIGNUP_CODE` is mandatory and gates teacher registration — the app **refuses to start** if it's unset or a known-weak default (see `app.py`, near the top). Firebase admin credentials come from `firebase_credentials.json` locally, or `FIREBASE_CREDENTIALS_JSON` when deployed. There is no test suite and no build/lint step — this is a single Flask file plus static HTML pages.

## Architecture

**Backend** is entirely `app.py` (Flask + Waitress). No blueprints/modules — routes, auth helpers, and retrieval/generation logic all live in this one file. `ingestion.py` is a standalone legacy scratch script (not imported by `app.py`) for manually pushing text into Pinecone; the real ingestion path is the `/ingest` and `/upload` routes in `app.py`.

**Auth & authorization layers** (all in `app.py`):
- `verify_user()` validates the Firebase ID token from `Authorization: Bearer <token>`.
- `require_auth` / `require_teacher` are route decorators for "must be signed in" / "must be a teacher".
- `class_owned_by(class_id, uid)` gates teacher-only actions on a class (ingest, upload, rules, stats).
- `user_in_class(uid, class_id, role)` gates student actions (chat) — a student must have joined the class.
- Roles live in Firestore at `Users/{uid}.role`; chats live at `Users/{uid}/Chats`.

**Data isolation**: each class gets its own Pinecone namespace, so retrieval never crosses class boundaries. This namespace-per-class model is the core design constraint — any change to ingestion or retrieval needs to preserve it.

**Request flow for `/chat`**: verify token → check `user_in_class` → embed the question (Gemini) → query the class's Pinecone namespace, dropping matches below `RETRIEVAL_MIN_SCORE` → if nothing survives, return the "not in my knowledge base" response (and log a knowledge gap) → otherwise hand the surviving chunks + chat history (`HISTORY_TURNS` back) to Gemini (`CHAT_MODEL`) to write a grounded answer → persist the exchange under the user's chat.

**Uploads are streamed/batched**, not loaded fully into memory: documents are read, chunked, embedded, and pushed to Pinecone incrementally. This is a deliberate memory constraint, not incidental — the Google/Pinecone/Firebase SDKs alone eat a few hundred MB, and this app is meant to run on 512MB instances (Render free/Starter). Don't refactor uploads back into a single load-into-memory pass. `MAX_UPLOAD_MB` (default 10) caps this further.

**Frontend** is static HTML + Tailwind (via CDN), no build step, no framework. Each page (`landing.html`, `login.html`, `student.html`, `teacherknowledge.html`, `teacherstats.html`) is self-contained with inline `<script>` logic, sharing only `auth.js` (wraps Firebase Auth into a global `Chronos` object: `Chronos.login`, `.signup`, `.me`, `.requireRole`, `.listClasses`, `.onUser`, `.ready`, etc.) and `theme.css`/`theme.js` for the shared design system. When editing a page's markup/classes, check whether it drives Tailwind via a `tailwind.config` `<script>` block with semantic color tokens (student.html does this, Material-Design style) — restyling should remap the token *values*, not hand-edit the generated class names, or JS that reads those classes will break.

**Legacy migration**: rules created before the "classes" concept existed are auto-migrated into the first class a teacher creates.

## Endpoints

Everything except `/health` and the pre-auth `/auth/*` routes expects a Firebase ID token in `Authorization: Bearer <token>`.

- Auth/classes: `/auth/config`, `/auth/register`, `/auth/me`, `/classes` (GET/POST), `/classes/join`, `DELETE /classes/<id>`
- Student-scoped (any member of the class): `/chat`, `/chats`, `/chats/<id>/messages`, `DELETE /chats/<id>`
- Teacher-scoped (owner of the class): `/ingest`, `/upload`, `/rules`, `/delete_rule`, `/stats`
- `/health` — plain health check, no auth
