# Moving the tutor onto a local model (Ollama)

Status: **plan only.** Nothing here is built. The blocker is a hosting decision
(Phase 0), and until that is made, writing the code would be guessing.

## What this changes, and what it does not

The goal is stated simply: *keep everything, change the AI model.* That holds for
the application — Cloud Run, Flask, Firestore, Firebase Auth, Pinecone, every
route, every prompt and the whole front end stay exactly as they are. Two things
cannot come along for the ride, and both are worth knowing before starting:

**Embeddings stay on Gemini.** `embed()` (`app.py:378`) writes 768-dimension
vectors with `gemini-embedding-001`, and every course's Pinecone namespace is
full of them. A different embedding model produces vectors that are not
comparable to those — not worse, *incompatible*. Switching it would leave every
existing course retrieving nothing until each teacher re-uploaded every document.
So generation moves and retrieval does not, which means a Gemini key is still
required after this work. It reduces the Gemini dependency; it does not remove it.

**The model needs somewhere to run.** Ollama needs a resident process holding
several gigabytes of weights, ideally on a GPU. The Chronos container has no GPU
and Cloud Run reclaims it between requests, so the model cannot live inside the
app. This is the one piece of "keep everything" that isn't available, and Phase 0
is about choosing how to pay for it.

## Phase 0 — Decide where the model runs (blocking)

| Option | Latency | Cost shape | Trade |
|---|---|---|---|
| A second Cloud Run service with an L4 GPU, running the `ollama` image | good when warm, tens of seconds cold | per-second, scales to zero | Closest to the current setup: same project, same deploy story. The catch is the cold start — the first student of the morning waits for a 7B model to load. A minimum instance removes that and costs money while idle. |
| A GPU VM kept running | consistently good | flat, always on | No cold start, predictable. You own patching and uptime. |
| Your own machine, development only | irrelevant | free | Proves the abstraction and lets you measure models. Production stays on Gemini. Lowest risk, and a reasonable first step whatever is chosen later. |

Two requirements hold in every option:

- **The endpoint must be authenticated.** An Ollama URL reachable from the
  internet is an open LLM, and the bill for it is yours. Cloud Run
  service-to-service IAM, or a shared secret header plus an IP allowlist.
- **It must have a request timeout**, the way `GEMINI_TIMEOUT_MS`
  (`app.py:301`) bounds the current client. A loaded GPU is a far likelier source
  of a request that never returns than a hosted API is.

## Phase 1 — Measure before committing

Before any refactor, settle whether a local model is actually good enough. The
bar is `gemini-2.5-flash-lite`: fast, and reliable at both tool calls and JSON.

A throwaway bench script, ten prompts captured from real usage, run against
`qwen2.5:7b-instruct` and `llama3.1:8b` (both support tool calling), measuring:

1. **Tool-call reliability** — does "quiz me on photosynthesis" produce a
   well-formed `create_practice_activity` call? This is the feature that was just
   fixed by moving to function calling; a model that is shaky here undoes it.
2. **JSON reliability** — `/tools/run` and `categorize_conversations` both parse
   the reply. Ollama's `format: "json"` option enforces this more strictly than
   today's prompt instruction does, so this may come out *better* than Gemini.
3. **Time to full response**, warm, at a realistic prompt length. The system
   instruction plus retrieved context plus history is not a short prompt.

If tool calls are unreliable on the candidates, stop here and say so — the
toolkit matters more than the provider.

## Phase 2 — The provider seam

One new module, `llm.py`:

```python
generate(system, contents, tools=None, temperature=None) -> {"text": str, "tool_call": dict|None}
```

with a `gemini` backend (today's code, moved) and an `ollama` backend, chosen by
an `LLM_PROVIDER` env var. The env var is the point: switching back mid-incident
is a redeploy, not a revert.

Four call sites move behind it, and they are already easy to move because
`response_text()` and `tool_call_from_response()` (`app.py`, near
`TOOL_FUNCTION_NAME`) isolate every assumption about Gemini's response shape:

| Site | Purpose | What the backend must support |
|---|---|---|
| `/chat` | the tutor turn | generation + function calling |
| `/tools/run` | builds the practice activity | strict JSON |
| `categorize_conversations` | topic labels for `/stats` | strict JSON array |
| `summarize_tutoring_state` | rolling conversation summary | plain text |

Ollama's OpenAI-compatible `/v1/chat/completions` handles tools with the schema
the code already builds in `practice_activity_tool()`, so the tool declaration
itself does not need rewriting — only the call and the response-reading.

## Phase 3 — Roll out behind the switch

Deploy with `LLM_PROVIDER=gemini` so nothing changes, then flip one course's
traffic and watch. What to watch, in order: tool calls still firing (the quiz
chips), `/tools/run` still returning parseable JSON, `/stats` topics still
sensible, and p95 response time. Keep Gemini configured and working the whole
time — the fallback is only useful if it is never allowed to rot.

## Explicitly unchanged

`embed()`, Pinecone and its namespaces, Firestore, Firebase Auth, every route,
every prompt, the front end, and the Cloud Run deployment of the Flask app.
