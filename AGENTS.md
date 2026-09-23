# Chronos agent map

Read this first, then open only the files relevant to the task. Source code is the authority; some older planning notes and README passages describe earlier states.

## Current app

Chronos is a course grounded AI tutor. Teachers create courses, upload source material, set rules and options, and inspect student analytics. Students join by code and chat within a course. The Flask service owns authorization, storage, retrieval, model calls, and static web serving. The browser app is complete; the native Android app (Kotlin/Compose, no WebView) mirrors it for both roles: student chat, learning activities, attachments, teacher material/rules/toolkits/analytics, settings and status. Deliberately not ported: desktop-only behaviour (hover, keyboard shortcuts for quiz options, the audio unlock gesture), per-chat scroll memory, chat prefetch, quiz progress dots, the large debug tools (composer strip, Copy JSON, client timings), and the developer extras in Settings (simulated slow network, clear caches, session JSON, deployment auto-refresh).

## Where to go

| Task | Start here |
| --- | --- |
| Routes, configuration, auth, API contracts | `app.py` (search `@app.route`); `web/auth.js`; `android/.../net/Api.kt` |
| Tutor answer, retrieval, memory, streaming | `app.py`: `chat`, `build_system_instruction`, `load_history`, `load_class_memory`, `load_student_docs`; `web/student.html`: `send`, `readChatResponse`; `android/.../net/ChatStream.kt` |
| Learning activities | `app.py`: `run_tool`, `_tool_prompt`, `_parse_tool_result`; `web/student.html`: `runTool`, `buildLearningTool` |
| Teacher material and settings | `web/teacherknowledge.html`; `app.py`: `manage_course_settings`, `ingest`, `upload`, `list_rules`, `delete_rule` |
| Teacher analytics | `web/teacherstats.html`; `app.py`: `stats`, `roster`, `student_profile_view`, `categorize_conversations` |
| Student files and profile | `app.py`: `add_student_file`, `load_student_docs`; `student_profile.py` |
| Moderation | `profanity.py`; early handling in `app.py:chat` |
| Web presentation and navigation | Each page keeps much of its JS/CSS inline in `web/*.html`; shared `web/theme.*` (tokens, dark/system theme, reduced motion, text size), `mobile.*`, `transition.*` (native crossfade between pages + thin in-view loading line, no wipe), `settings.*` (self-contained Settings panel used by every app page) |
| Session hint, instant login redirect | `web/auth.js` (`chronos-hint` in localStorage, `Chronos.hint()`, `whenSignedIn`), head snippet in `web/login.html`; hint is never a grant, cleared on sign-out/bounce |
| User preferences, personalities, dev accounts | `app.py`: `PERSONALITIES`, `normalize_preferences`, `preference_directive`, `/me/preferences`, `is_dev_user` (`DEV_EMAILS` env, default `test@gmail.com`, email match only), `/auth/me` returns `is_dev`; debug payload built in `chat` only when `is_dev_user` and `debug:true`; UI in `web/student.html` (`setDebug`, `buildDebugPanel`) and `web/settings.js` |
| Course outline (knowledge-base awareness) | `app.py`: `summarize_document`, `save_manifest_entry`, `load_course_manifest`, `manifest_outline`, `rebuild_manifest`; stored at `Classes/{id}.manifest.docs`, built at `/upload`, backfilled from `/rules`, injected by `build_system_instruction` |
| Native Android | `android/app/src/main/java/com/chronos/tutor/`: `ui/nav/ChronosNav.kt` (routes), `ui/student/` (chat, `ToolWidgets.kt`), `ui/teacher/`, `ui/settings/`, `ui/login/`, `data/` (one repository per area, pure JSON parsers unit-tested in `src/test/`), `net/`; build config in `android/app/build.gradle.kts` (release is always R8-minified, debug-key signed when CI has no keystore). Launch opens on the last known identity (`Prefs.lastMe`, a hint like `chronos-hint`, never a grant) and `RootViewModel` revalidates via `/auth/me` in the background. When a Flask response shape changes, update the matching `parse*` in `data/` too |
| Tests and deployment | `tests/test_*.py`, Android `src/test/`; `Dockerfile`, `.github/workflows/android.yml`, `firestore.rules`; public `/status` and developer-only `/status/deployment` read Cloud Build/Run via runtime ADC |

## Data and trust boundaries

- Firebase Auth issues ID tokens. Flask verifies bearer tokens and reads `Users/{uid}.role`; teacher signup requires `TEACHER_SIGNUP_CODE`. Every protected route checks role and course membership or ownership. Browser and Android clients call Flask, never Firestore directly. Keep `firestore.rules` client access denied.
- Firestore uses legacy `Classes` collection names and `class_id` API fields. Courses have `Members`; user data lives under `Users/{uid}` with `Chats/{chatId}/Messages`, `Profiles/{classId}`, and `Files`. Preserve class scoping when querying chats, memory, files, rules, and analytics.
- Teacher uploaded course documents are chunked, embedded with Gemini, and stored in the Pinecone index `teacherchronostwo`, namespace = class ID. Teacher custom rules are Firestore prompt policy, not retrieval facts. Student assignments/rubrics stay in Firestore and enter only their conversation as untrusted review context; never index them.
- The tutor never hard-refuses on empty retrieval: the model always runs, with a "no material matched" rule plus the course outline (a map, not a source of facts). Small talk skips retrieval (`_SMALL_TALK_RE`). Teacher "Additional instruction" was retired and folds into a custom rule.
- `/chat` retrieves course chunks, builds the bounded prompt, streams SSE `delta` then `done` (or `error`), and records the exchange. The final `done` payload contains metadata the clients use. `/tools/run` is a separate generation call with course settings checked again. Preserve the source and privacy restrictions when changing either path.
- `profanity.py` short circuits blocked messages before embedding/retrieval/model calls. `student_profile.py` uses deterministic counters and is scoped per class. Analytics distinguishes material gaps, confusion, and conduct concerns.

## Working locally

- Python dependencies: `pip install -r requirements.txt`. The real `.env` secrets are in the cloud, so local end-to-end testing of `app.py` will not work. With cloud credentials, run `waitress-serve --port=5000 app:app`.
- Offline Python checks are plain scripts: `python tests/test_profanity.py`, `test_security.py`, `test_stats_grouping.py`, `test_student_context.py`, `test_student_profile.py`, `test_quiz_answers.py`, `test_tutor_flow.py`. Backend import tests need a throwaway `TEACHER_SIGNUP_CODE` of at least 12 characters, dummy `GEMINI_API_KEY`/`PINECONE_API_KEY`, and a syntactically valid (fake) `FIREBASE_CREDENTIALS_JSON`; external services are stubbed. Other local tests should work. Run relevant scripts after edits.
- Android uses Gradle Kotlin DSL and Compose. From `android/`, run `.\gradlew.bat testDebugUnitTest` for local unit tests. Firebase build properties `fbApiKey`, `fbProjectId`, `fbAppId` are supplied externally; backend host defaults to `chronos.tevproject.com` and can be overridden with `-PchronosHost=...`. See `android/app/build.gradle.kts`.
- `docs/OLLAMA.md` and `docs/OLLAMA_TASK.md` are a proposed migration, not the current model implementation. `docs/ANDROID.md` and `docs/COMMIT_LOG.md` are historical context; inspect the current Android source for actual capabilities. README is useful for concepts and deployment but may lag the native app.

## Efficient navigation

Use `rg -n "@app.route|def name" app.py` to find backend entry points, and `rg -n "function name|async function name" web -g '*.html'` for browser behavior. Inspect only a narrow section around the matching function. For cross client work, check the Flask response shape and both browser and Android consumers. Keep this map updated when architecture or entry points change.

When a requested change substantially affects the architecture, workflows, or key entry points, update this file briefly so the map stays current. Keep additions concise.

## Local Gitea workflow

Only when explicitly asked to commit and push to Gitea from the local machine, use `vibe` instead of `git`: `vibe commit -m "message"`, then `vibe push`. This does not apply to cloud GitHub workflows.
