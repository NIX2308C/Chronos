# Implementation brief: move generation to Ollama

**Audience:** an AI coding agent (Claude Code, Codex, or similar) picking this up
cold, plus whoever reviews the result. `OLLAMA.md` is the *why* and the decision
record; this file is the *what to type*. Read both before starting.

**Prerequisite — do not start without it.** Phase 0 of `OLLAMA.md` is a hosting
decision only the repo owner can make, and Phase 1 is a measurement that can
veto the whole thing. If nobody has told you where Ollama runs and that the
candidate model passes the tool-call bench, stop and ask. Everything below
assumes a reachable, authenticated Ollama endpoint exists.

---

## The one-paragraph version

Generation moves behind a new `llm.py` with two backends chosen by an
`LLM_PROVIDER` env var; Gemini stays the default and stays working.
`embed()` does **not** move. Four call sites change. The whole change should be
reversible by flipping one env var and redeploying.

## Scope

**In scope — four call sites, all in `app.py`:**

| Function / route | What it needs from the model |
|---|---|
| `chat()` — the tutor turn | text **+ function calling** (`create_practice_activity`) |
| `/tools/run` | strict JSON (the practice activity) |
| `categorize_conversations()` | strict JSON array |
| `summarize_tutoring_state()` | plain text |

**Explicitly out of scope. Do not touch these, even if they look adjacent:**

- `embed()` and `embed_batch()`. Pinecone holds 768-dim `gemini-embedding-001`
  vectors for every course. A different embedding model does not produce worse
  vectors, it produces incompatible ones, and every course would retrieve
  nothing until each teacher re-uploaded every document. **A Gemini API key is
  still required after this change.** If a task description tells you to remove
  the Gemini dependency entirely, it is wrong — say so rather than deleting
  `GEMINI_API_KEY`.
- Pinecone, Firestore, Firebase Auth, every route's signature, every prompt's
  wording, the front end, `profanity.py`, the rate limiter, `firestore.rules`.
- Prompt text. If a local model needs different wording to behave, that is a
  finding to report, not a silent edit — the prompts encode the grounding rules
  this app exists to enforce.

---

## Step 1 — `llm.py`

One module, one public function:

```python
def generate(system=None, contents=None, prompt=None, tools=None,
             temperature=None, json_mode=False) -> dict
```

Returns `{"text": str, "tool_call": dict | None}`. `tool_call` is
`{"name": str, "args": dict}` or `None`. **Never raise for "the model declined
to call a tool"** — that is `None`, not an error. Transport failures should
raise, because every call site already sits inside a `try` that turns an
exception into a clean error for the user.

Two backends behind it, selected by `LLM_PROVIDER` (`gemini` | `ollama`,
defaulting to `gemini`). New env knobs, documented in `README.md` alongside the
existing ones:

```
LLM_PROVIDER      gemini
OLLAMA_URL        http://localhost:11434
OLLAMA_MODEL      qwen2.5:7b-instruct
OLLAMA_TIMEOUT_MS 120000
```

`OLLAMA_TIMEOUT_MS` is not optional. `GEMINI_TIMEOUT_MS` (`app.py`, near the
`genai.Client` construction) exists because google-genai defaults to no timeout;
a loaded GPU is a *likelier* source of a request that never returns than a hosted
API is. Match that pattern.

### Neutral message shape

`load_history()` currently emits Gemini's shape:
`{"role": "user"|"model", "parts": [{"text": ...}]}`. Change it to emit
`{"role": "user"|"assistant", "text": ...}` and let each backend convert. The
Gemini backend rebuilds `parts`; the Ollama backend passes `messages` through
with the system prompt prepended as `{"role": "system", ...}`.

Check `test_security.py` still passes after this — it asserts on
`load_history()` output via `json.dumps`, deliberately shape-agnostic, so it
should survive. If it doesn't, you changed more than the shape.

### Neutral tool declaration

`practice_activity_tool(settings)` currently returns a `types.Tool` — a
Gemini object — built from a JSON-Schema-ish dict. Change it to return the plain
dict and have each backend wrap it:

- **Gemini:** `types.Tool(function_declarations=[decl])`, as today.
- **Ollama:** `{"type": "function", "function": {...}}` on the OpenAI-compatible
  `/v1/chat/completions` endpoint. Lowercase the JSON-Schema types (`"OBJECT"` →
  `"object"`, `"STRING"` → `"string"`) — Gemini accepts uppercase, OpenAI-shaped
  APIs generally do not. This is the single most likely cause of "the model never
  calls the tool" and it fails silently.

Keep `validate_tool_request()` exactly as it is and keep calling it on whatever
comes back. **It is a security boundary, not a formatting step:** it re-checks
the requested type against the course's enabled toolkits, which is what stops a
client (or a confused model) forcing an activity a teacher switched off.

### JSON mode

`/tools/run` and `categorize_conversations()` both `json.loads` the reply. Pass
`format: "json"` (Ollama) for those two. Keep `_parse_tool_result`'s code-fence
stripping regardless — it costs nothing and still guards the Gemini path.

> That regex was broken for two releases (`\\s` in a raw string matches a literal
> backslash, so fences were never stripped and nearly every reply 502'd). Do not
> "tidy" it back. There is no test pinning it; if you touch it, add one.

---

## Step 2 — the call sites

Replace each `client.models.generate_content(...)` with `llm.generate(...)`.
Concretely:

- **`chat()`** — currently builds `types.GenerateContentConfig(system_instruction=…,
  temperature=0.35, tools=[activity_tool] if activity_tool else None)` and then
  reads the result through `response_text()` and `tool_call_from_response()`.
  Those two helpers exist precisely to isolate Gemini's response shape; fold
  their logic into the Gemini backend and have the call site use
  `result["text"]` / `result["tool_call"]` directly. Preserve the fallback where
  a function-call-only reply gets a synthesized sentence, or the activity renders
  under an empty tutor message.
- **`/tools/run`** — `temperature=0.2`, `json_mode=True`. It currently passes
  `response.text` straight into `_parse_tool_result`; pass `result["text"]`.
- **`categorize_conversations()`** — `json_mode=True`. Keep the
  `[:40]` truncation on each category: those strings are model output derived
  from student text and land in a teacher's dashboard.
- **`summarize_tutoring_state()`** — plain text, no tools.

`CHAT_MODEL` / `TOOL_MODEL` stay meaningful for the Gemini backend. Don't delete
them.

---

## Step 3 — tests

`test_security.py` fakes the model by assigning `A.client`. After this refactor
that stub is dead and the test will either fail or, worse, silently start making
real network calls. **Update it to stub the new seam** (`A.llm.generate`, or
whatever you name it) returning `{"text": "an answer", "tool_call": None}`.

Then all four suites must pass offline, with `LLM_PROVIDER` unset:

```bash
python test_profanity.py && python test_security.py && \
python test_stats_grouping.py && python test_student_context.py
```

`app.py` refuses to import without a valid `TEACHER_SIGNUP_CODE`, so a throwaway
`.env` is needed just to import it. **Never commit that file** — `.env` is
gitignored and `.dockerignore`d, and it must stay that way.

Add one new test: `llm.generate` with the Ollama backend, against a stubbed HTTP
layer, returns a parsed `tool_call` from a realistic Ollama tool-call response
body. That is the part with no coverage and the part most likely to be wrong.

---

## Step 4 — roll out

Deploy with `LLM_PROVIDER=gemini` first and confirm nothing changed. Then flip a
single course's traffic and watch, in this order:

1. Quiz chips still fire from prose ("quiz me on photosynthesis") — this is the
   feature function calling was introduced to fix; a regression here is a stop.
2. `/tools/run` still returns parseable JSON (no 502s).
3. `/stats` topic labels still look like topics, not `Uncategorized`.
4. p95 response time against the flash-lite baseline.

Keep Gemini configured and exercised throughout. A fallback nobody runs is not a
fallback.

---

## Done means

- [ ] `LLM_PROVIDER=gemini` behaves identically to today, verified by the suite.
- [ ] `LLM_PROVIDER=ollama` answers, calls the practice tool, and returns
      parseable JSON from both JSON sites.
- [ ] `embed()` untouched; existing courses still retrieve.
- [ ] `OLLAMA_TIMEOUT_MS` enforced; a hung endpoint fails cleanly rather than
      hanging a request.
- [ ] The Ollama endpoint is authenticated. An open Ollama URL is an open LLM
      billed to the owner.
- [ ] New env vars documented in `README.md`.
- [ ] All four suites pass offline, no new dependency beyond an HTTP client
      (`httpx` already arrives with google-genai).
- [ ] No secret, key or `.env` committed.

## Report back

State plainly: measured latency against the flash-lite baseline, tool-call
reliability over at least ten prose requests, and anything you had to change
outside this brief. If the local model cannot call the tool reliably, **say so
and stop** — the toolkit matters more than the provider, and shipping a tutor
that silently stopped producing quizzes would undo the work this replaces.
