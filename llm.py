"""One seam between Chronos and the model that generates its text and embeddings.

Chat goes to OpenRouter's OpenAI-compatible `/api/v1/chat/completions`. Server side
only: the keys never reach browser or Android code.

    LLM_BASE_URL        chat endpoint root (default https://openrouter.ai/api/v1)
    OPENROUTER_API_KEY  bearer token for chat, required, read from the environment only
    CHAT_MODEL / TOOL_MODEL   model name sent (default recursive/flint-v1.0:free)
    EMBED_BASE_URL      embeddings server (default https://chronos-embed.tevproject.com/v1),
                        authenticated with LLM_API_KEY. EmbeddingGemma 300M, 768-d, L2-normalized.
    LLM_TIMEOUT_S  read timeout, at least 120

The request shape app.py builds: `contents` is a string or a list of
{"role": "user"|"model", "parts": [{"text": ...}]} turns. Tools are plain
JSON-schema declarations, but the server's native `tools` is not used: they are
described in the system prompt and the call is parsed out of the reply text
(`TOOL_CALL {"name": ..., "args": {...}}` on the last line, one per turn). A
malformed call is dropped. A tool marked `"alone": True` is called as the whole
reply and its result is sent back for another turn (see app.py's tool loop). `json_schema` maps to `response_format`.

The endpoint is stateless, so callers send the whole conversation each time. Thinking text
(`<|channel>thought ... <channel|>`, `reasoning_content`) is never returned.

Results are backend-neutral:
    generate(...) -> Result(text, tool_call, usage)
    stream(...)   -> iterator of Chunk(text, tool_call, usage)
    embed(text) / embed_batch(texts) -> vector(s)
where tool_call is {"name": str, "args": dict} or None and usage is
{"prompt", "reply", "total"} (ints or None).
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections import namedtuple

from dotenv import load_dotenv

import prompts

# app.py imports this module before its own load_dotenv(), and the settings below are read at import.
load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").strip().rstrip("/")
# Chat (OpenRouter) and embeddings (chronos-embed) are separate services with separate keys.
CHAT_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
API_KEY = os.getenv("LLM_API_KEY", "").strip()      # embedding server
# Cloudflare drops a request with no data for ~100 s, so replies are streamed; the
# client side waits at least 120 s.
TIMEOUT_S = max(float(os.getenv("LLM_TIMEOUT_S", "120")), 120.0)
TRIES = 3

DEFAULT_CHAT_MODEL = "recursive/flint-v1.0:free"
EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", "https://chronos-embed.tevproject.com/v1").strip().rstrip("/")
# Used only if GET /models on the embedding server cannot be read; normally the id it returns wins.
EMBED_MODEL = "embeddinggemma"
# Must equal the Pinecone index dimension. Vectors from different models never share an index.
EMBED_DIM = 768
EMBED_BATCH = 16                  # inputs per /embeddings request (16-32 is safe)
EMBED_UNAVAILABLE = ("Course search is temporarily unavailable (the embedding server is "
                     "unreachable). Please try again in a few minutes.")
_embed_model = None

# Default sampling; the caller's temperature overrides.
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
# Reasoning tokens count against max_tokens, so this leaves room for both.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
# This model requires reasoning by default. Non-streaming calls exclude it; streams
# receive it on a separate field (never in the reply) so the UI can show a thinking phase.
# LLM_REASONING_EFFORT (low/medium/high) optionally bounds how much it thinks.
# `think=False` asks the endpoint to switch it off; if the model refuses (it may make
# reasoning mandatory) the request is retried once with it on and the answer remembered.
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
_can_disable_thinking = None     # None = not tried yet
# Prompt budget (tokens, estimated as chars/3; the oldest turns go first). Sized for a
# hosted model with a large window; lower it if the model's window is small.
PROMPT_TOKENS = int(os.getenv("LLM_PROMPT_TOKENS", "32000"))
MESSAGE_OVERHEAD_TOKENS = 50      # chat-template overhead per message

# Cloudflare rejects urllib's default "Python-urllib/x.y" agent with a 403 (error 1010).
USER_AGENT = "Chronos/1.0"

TOOL_MARKER = "TOOL_CALL"
THOUGHT_OPEN, THOUGHT_CLOSE = "<|channel>", "<channel|>"

# `tool_call` is the first call, `calls` every call the reply made (a reply may end with several).
Result = namedtuple("Result", "text tool_call usage calls", defaults=((),))
Chunk = namedtuple("Chunk", "text tool_call usage thinking calls", defaults=(False, ()))


class LLMError(RuntimeError):
    """The endpoint failed or returned something unusable."""


class ContextTooLong(LLMError):
    """HTTP 400: prompt + reply do not fit the context window."""


class EmbeddingsUnavailable(LLMError):
    """The embedding server is down, or returned vectors we must not store or search with."""


def estimate_tokens(text):
    return len(text or "") // 3 + 1


# ---------- prompt building ----------

def _signature(t):
    params = (t.get("parameters") or {}).get("properties") or {}
    args = []
    for k, v in params.items():
        v = v if isinstance(v, dict) else {}
        args.append("%s: %s" % (k, "|".join(v["enum"]) if v.get("enum") else v.get("type", "string")))
    return "%s(%s)" % (t["name"], ", ".join(args))


def _tool_instructions(tools):
    lines = [prompts.fill("tools/instructions", marker=TOOL_MARKER)]
    for t in tools:
        lines.append("- %s%s: %s" % (_signature(t), " [alone]" if t.get("alone") else "", t.get("description", "")))
    return "\n".join(lines)


def _messages(contents, system, tools=None):
    """Chronos turns -> OpenAI chat messages, trimmed to fit the context window."""
    system = "\n\n".join(s for s in (system, _tool_instructions(tools) if tools else None) if s)
    head = [{"role": "system", "content": system}] if system else []
    if isinstance(contents, str):
        return head + [{"role": "user", "content": contents}]
    turns = []
    for turn in contents or []:
        text = "".join(str(p.get("text") or "") for p in (turn.get("parts") or []) if isinstance(p, dict))
        role = "assistant" if turn.get("role") in ("model", "assistant") else "user"
        turns.append({"role": role, "content": text})
    cost = lambda ms: sum(estimate_tokens(m["content"]) + MESSAGE_OVERHEAD_TOKENS for m in ms)
    size = cost(head + turns)
    while size > PROMPT_TOKENS and len(turns) > 1:
        dropped = turns[:2] if len(turns) > 2 and turns[1]["role"] == "assistant" else turns[:1]
        turns = turns[len(dropped):]
        size -= cost(dropped)
    return head + turns


def _body(model, contents, system, tools, temperature, json_schema, stream, max_tokens, think=True):
    body = {"model": model, "messages": _messages(contents, system, tools), "stream": stream,
            "max_tokens": max_tokens or MAX_TOKENS, **SAMPLING}
    if not think and _can_disable_thinking is not False:
        body["reasoning"] = {"enabled": False}
    else:
        body["reasoning"] = {"exclude": not stream}
        if REASONING_EFFORT in ("low", "medium", "high"):
            body["reasoning"]["effort"] = REASONING_EFFORT
    if temperature is not None:
        body["temperature"] = temperature
    if json_schema is not None:
        if json_schema:
            # A schema constrains decoding itself, which a prompt instruction cannot.
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "chronos_output", "schema": json_schema}}
        else:
            body["response_format"] = {"type": "json_object"}
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


# ---------- reply cleaning ----------

class _Cleaner:
    """Splits raw reply text into visible text, the tool-call text and dropped thinking.

    Fed in arbitrary fragments; holds back only a tail that might be the start of a marker.
    """

    def __init__(self):
        self.buf, self.mode, self.tool_text = "", "text", ""

    def feed(self, text):
        self.buf += text
        out = []
        while self.buf:
            if self.mode == "thought":
                i = self.buf.find(THOUGHT_CLOSE)
                if i < 0:
                    self.buf = self.buf[-(len(THOUGHT_CLOSE) - 1):]
                    break
                self.buf, self.mode = self.buf[i + len(THOUGHT_CLOSE):], "text"
            elif self.mode == "tool":
                self.tool_text += self.buf
                self.buf = ""
            else:
                hits = [(i, m) for m in (THOUGHT_OPEN, TOOL_MARKER) if (i := self.buf.find(m)) >= 0]
                if hits:
                    i, marker = min(hits)
                    out.append(self.buf[:i])
                    self.buf = self.buf[i + len(marker):]
                    self.mode = "thought" if marker == THOUGHT_OPEN else "tool"
                    continue
                keep = max((k for m in (THOUGHT_OPEN, TOOL_MARKER) for k in range(1, len(m))
                            if self.buf.endswith(m[:k])), default=0)
                out.append(self.buf[:len(self.buf) - keep])
                self.buf = self.buf[len(self.buf) - keep:]
                break
        return "".join(out)

    def finish(self):
        """(remaining visible text, list of tool calls in the order written)."""
        tail = self.buf if self.mode == "text" else ""
        self.buf = ""
        return tail, _parse_calls(self.tool_text)


def _parse_call(text):
    text = (text or "").strip()
    if not text.startswith("{"):
        return None
    try:
        call, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    if isinstance(call, dict) and call.get("name") and isinstance(call.get("args"), dict):
        return {"name": str(call["name"]), "args": call["args"]}
    return None


def _parse_calls(text):
    """Every well-formed call in the text that followed the first marker; the rest are dropped."""
    calls = []
    for part in (text or "").split(TOOL_MARKER):
        call = _parse_call(part)
        if call:
            calls.append(call)
    return calls


def _usage(data):
    u = (data or {}).get("usage") or {}
    p, r, t = u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens")
    p = p if isinstance(p, int) else None
    r = r if isinstance(r, int) else None
    t = t if isinstance(t, int) else ((p + r) if p is not None and r is not None else None)
    return {"prompt": p, "reply": r, "total": t}


# ---------- transport ----------

def _open(path, body, base=None, tries=None):
    base = base or BASE_URL
    tries = tries or TRIES
    embedding = base == EMBED_BASE_URL and base != BASE_URL
    key = API_KEY if embedding else CHAT_API_KEY
    if not key:
        raise LLMError(("LLM_API_KEY" if embedding else "OPENROUTER_API_KEY") + " is not set")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "Authorization": "Bearer " + key}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(base + path, data=data, headers=headers, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT_S)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < tries:      # 502/524/530: tunnel or host down
                time.sleep(2 ** (attempt - 1))
                continue
            detail, raw = "", ""
            try:
                raw = e.read().decode("utf-8", "replace")
                err = json.loads(raw).get("error")
                detail = (err.get("message") if isinstance(err, dict) else err) or ""
            except Exception:
                detail = " ".join(raw.split())[:200]      # e.g. a Cloudflare HTML block page
            if e.code == 400 and any(w in detail.lower() for w in ("context", "exceed", "too long", "n_ctx")):
                raise ContextTooLong("Model endpoint: %s" % detail) from e
            raise LLMError("Model endpoint returned HTTP %s %s" % (e.code, detail)) from e
        except (urllib.error.URLError, OSError) as e:
            if attempt < tries:
                time.sleep(2 ** (attempt - 1))
                continue
            raise LLMError("Could not reach the model endpoint at %s: %s" % (base, e)) from e


def _read_json(resp):
    try:
        data = json.loads(resp.read().decode("utf-8"))
    except ValueError as e:
        raise LLMError("The model endpoint returned a non-JSON response") from e
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise LLMError("Model endpoint: %s" % (err.get("message") if isinstance(err, dict) else err))
    return data


# ---------- public ----------

def health(model=None):
    """Raise unless the endpoint is reachable and the key is accepted. Generates nothing."""
    if not CHAT_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is not set")
    req = urllib.request.Request(BASE_URL + "/models", headers={"Authorization": "Bearer " + CHAT_API_KEY,
                                                                    "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=min(TIMEOUT_S, 10)) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise LLMError("Model endpoint returned HTTP %s" % e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError("Could not reach the model endpoint at %s: %s" % (BASE_URL, e)) from e
    return True


def can_disable_thinking():
    """True/False once known from a real request, None until `think=False` has been tried."""
    return _can_disable_thinking


def _open_chat(args, think):
    """Open a chat request. With think=False, fall back to normal reasoning if the model insists on it."""
    global _can_disable_thinking
    try:
        resp = _open("/chat/completions", _body(*args, think))
    except LLMError as e:
        if think or _can_disable_thinking is False or "reason" not in str(e).lower():
            raise
        _can_disable_thinking = False
        logger.warning("This model will not run without reasoning (%s); leaving it on.", e)
        return _open("/chat/completions", _body(*args, True))
    if not think and _can_disable_thinking is None:
        _can_disable_thinking = True
    return resp


def generate(model, contents, system=None, tools=None, temperature=None, json_schema=None, max_tokens=None,
             think=True):
    """One complete reply. `json_schema={}` asks for any JSON object."""
    args = (model, contents, system, tools, temperature, json_schema, False, max_tokens)
    with _open_chat(args, think) as resp:
        data = _read_json(resp)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("The model endpoint returned no choices")
    raw = str((choices[0].get("message") or {}).get("content") or "")
    if json_schema is not None:
        return Result(raw.strip(), None, _usage(data))
    cleaner = _Cleaner()
    text = cleaner.feed(raw)
    tail, calls = cleaner.finish()
    return Result((text + tail).strip(), calls[0] if calls else None, _usage(data), calls)


def stream(model, contents, system=None, tools=None, temperature=None, max_tokens=None, think=True):
    """Yield Chunk(text, tool_call, usage) as the reply arrives."""
    cleaner, usage, thinking = _Cleaner(), None, False
    args = (model, contents, system, tools, temperature, None, True, max_tokens)
    with _open_chat(args, think) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):      # blank separators and ":" keep-alive pings
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except ValueError:
                logger.warning("Skipping an unreadable stream line")
                continue
            if data.get("error"):
                err = data["error"]
                raise LLMError("Model endpoint: %s" % (err.get("message") if isinstance(err, dict) else err))
            if data.get("usage"):
                usage = _usage(data)
            for choice in data.get("choices") or []:
                delta = choice.get("delta") or {}
                if not thinking and (delta.get("reasoning") or delta.get("reasoning_details")):
                    thinking = True
                    yield Chunk("", None, None, True)
                text = delta.get("content")
                visible = cleaner.feed(str(text)) if text else ""
                if visible:
                    yield Chunk(visible, None, None)
    tail, calls = cleaner.finish()
    if tail:
        yield Chunk(tail, None, None)
    yield Chunk("", calls[0] if calls else None, usage, False, calls)


def count_tokens(text):
    """Estimated prompt tokens (OpenRouter has no tokenize endpoint)."""
    return estimate_tokens(text)


# ---------- embeddings ----------
# EmbeddingGemma wants a task prefix on every input, different for questions and
# for the text being searched.

def query_text(text):
    return "task: search result | query: %s" % text


def document_text(content, title=None):
    return "title: %s | text: %s" % (title or "none", content)


def embed_model():
    """The model id the embedding server reports (GET /models), cached."""
    global _embed_model
    if _embed_model:
        return _embed_model
    req = urllib.request.Request(EMBED_BASE_URL + "/models", headers={"Authorization": "Bearer " + API_KEY,
                                                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=min(TIMEOUT_S, 10)) as resp:
            _embed_model = str(json.loads(resp.read().decode("utf-8"))["data"][0]["id"])
    except Exception as e:
        raise EmbeddingsUnavailable(EMBED_UNAVAILABLE) from e
    return _embed_model


def _embed_raw(inputs):
    """Vectors for already-formatted inputs, in order. Raises EmbeddingsUnavailable on any failure."""
    out = []
    try:
        model = embed_model()
        for start in range(0, len(inputs), EMBED_BATCH):
            group = inputs[start:start + EMBED_BATCH]
            with _open("/embeddings", {"model": model, "input": group}, base=EMBED_BASE_URL) as resp:
                data = _read_json(resp)
            rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
            if len(rows) != len(group):
                raise LLMError("The embeddings endpoint returned %d vectors for %d inputs" % (len(rows), len(group)))
            out.extend(r["embedding"] for r in rows)
    except EmbeddingsUnavailable:
        raise
    except LLMError as e:
        logger.error("Embedding request failed: %s", e)
        raise EmbeddingsUnavailable(EMBED_UNAVAILABLE) from e
    for v in out:
        if len(v) != EMBED_DIM:
            logger.critical("Embedding server returned a %d-d vector; the index is %d-d. Refusing to use it.",
                            len(v), EMBED_DIM)
            raise EmbeddingsUnavailable("Course search is misconfigured (embedding size mismatch).")
    return out


def embed(text):
    """A search-query vector."""
    return _embed_raw([query_text(text)])[0]


def embed_documents(chunks, title=None):
    """Vectors for document chunks to store, in order."""
    return _embed_raw([document_text(c, title) for c in chunks])


def verify_embeddings():
    """Embed one test string and require EMBED_DIM floats. Returns (model, dimension)."""
    vec = _embed_raw([query_text("startup check")])[0]
    return embed_model(), len(vec)
