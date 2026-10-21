"""One seam between Chronos and the model that generates its text and embeddings.

Chat goes to a llama.cpp `llama-server` (Gemma 4 E4B) through its OpenAI-compatible
`/v1/chat/completions`, behind a Cloudflare tunnel. Server side only: the key never
reaches browser or Android code.

    LLM_BASE_URL   endpoint root including /v1   (default https://chronos-llm.tevproject.com/v1)
    LLM_API_KEY    bearer token, required, read from the environment only
    CHAT_MODEL / TOOL_MODEL   model name sent (default gemma4-e4b)
    EMBED_MODEL    embeddings model (the chat server's brief does not cover /embeddings)
    LLM_TIMEOUT_S  read timeout, at least 120

The request shape app.py builds: `contents` is a string or a list of
{"role": "user"|"model", "parts": [{"text": ...}]} turns. Tools are plain
JSON-schema declarations, but the server's native `tools` is not used: they are
described in the system prompt and the call is parsed out of the reply text
(`TOOL_CALL {"name": ..., "args": {...}}` on the last line, one per turn). A
malformed call is dropped. `json_schema` maps to `response_format`.

The server is stateless and processes one request at a time, so callers send the
whole conversation each time and must not fan out in parallel. Thinking text
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

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("LLM_BASE_URL", "https://chronos-llm.tevproject.com/v1").strip().rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "").strip()
# Cloudflare drops a request with no data for ~100 s, so replies are streamed; the
# client side waits at least 120 s.
TIMEOUT_S = max(float(os.getenv("LLM_TIMEOUT_S", "120")), 120.0)
TRIES = 3

DEFAULT_CHAT_MODEL = "gemma4-e4b"
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
# Must equal the Pinecone index dimension. Sent as `dimensions` only when set.
EMBED_DIMENSIONS = os.getenv("EMBED_DIMENSIONS", "").strip()

# Recommended Gemma sampling; the caller's temperature overrides.
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
MAX_TOKENS = 1024
# The 16,384-token window covers prompt + output. About 3 characters a token with
# headroom for the reply; the oldest turns go first.
MAX_PROMPT_CHARS = int(os.getenv("LLM_MAX_PROMPT_CHARS", "36000"))

TOOL_MARKER = "TOOL_CALL"
THOUGHT_OPEN, THOUGHT_CLOSE = "<|channel>", "<channel|>"

Result = namedtuple("Result", "text tool_call usage")
Chunk = namedtuple("Chunk", "text tool_call usage")


class LLMError(RuntimeError):
    """The endpoint failed or returned something unusable."""


# ---------- prompt building ----------

def _tool_instructions(tools):
    lines = [
        "You can call one tool per reply. To call it, write your normal reply first, then put "
        "this on the very last line and nothing after it:",
        TOOL_MARKER + ' {"name": "<tool name>", "args": {<arguments as JSON>}}',
        "Call a tool only when its description says to. Otherwise just answer.",
        "Tools:",
    ]
    for t in tools:
        lines.append("- %s: %s Arguments (JSON Schema): %s" % (
            t["name"], t.get("description", ""),
            json.dumps(t.get("parameters") or {"type": "object", "properties": {}}, separators=(",", ":"))))
    first = tools[0]
    props = (first.get("parameters") or {}).get("properties") or {}
    example = {k: (v.get("enum") or ["..."])[0] if isinstance(v, dict) else "..." for k, v in props.items()}
    lines.append("Example:\n%s %s" % (TOOL_MARKER, json.dumps({"name": first["name"], "args": example})))
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
    size = sum(len(m["content"]) for m in head + turns)
    while size > MAX_PROMPT_CHARS and len(turns) > 1:
        dropped = turns[:2] if len(turns) > 2 and turns[1]["role"] == "assistant" else turns[:1]
        turns = turns[len(dropped):]
        size -= sum(len(m["content"]) for m in dropped)
    return head + turns


def _body(model, contents, system, tools, temperature, json_schema, stream, max_tokens):
    body = {"model": model, "messages": _messages(contents, system, tools), "stream": stream,
            "max_tokens": max_tokens or MAX_TOKENS, **SAMPLING}
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
        """(remaining visible text, tool_call or None)."""
        tail = self.buf if self.mode == "text" else ""
        self.buf = ""
        return tail, _parse_call(self.tool_text)


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


def _usage(data):
    u = (data or {}).get("usage") or {}
    p, r, t = u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens")
    p = p if isinstance(p, int) else None
    r = r if isinstance(r, int) else None
    t = t if isinstance(t, int) else ((p + r) if p is not None and r is not None else None)
    return {"prompt": p, "reply": r, "total": t}


# ---------- transport ----------

def _open(path, body, key_required=True):
    if key_required and not API_KEY:
        raise LLMError("LLM_API_KEY is not set")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = "Bearer " + API_KEY
    data = json.dumps(body).encode("utf-8")
    for attempt in range(1, TRIES + 1):
        req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT_S)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < TRIES:      # 502/524/530: tunnel or host down
                time.sleep(2 ** (attempt - 1))
                continue
            detail = ""
            try:
                err = json.loads(e.read().decode("utf-8", "replace")).get("error")
                detail = (err.get("message") if isinstance(err, dict) else err) or ""
            except Exception:
                pass
            raise LLMError("Model endpoint returned HTTP %s %s" % (e.code, detail)) from e
        except (urllib.error.URLError, OSError) as e:
            if attempt < TRIES:
                time.sleep(2 ** (attempt - 1))
                continue
            raise LLMError("Could not reach the model endpoint at %s: %s" % (BASE_URL, e)) from e


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
    if not API_KEY:
        raise LLMError("LLM_API_KEY is not set")
    req = urllib.request.Request(BASE_URL + "/models", headers={"Authorization": "Bearer " + API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=min(TIMEOUT_S, 10)) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise LLMError("Model endpoint returned HTTP %s" % e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError("Could not reach the model endpoint at %s: %s" % (BASE_URL, e)) from e
    return True


def generate(model, contents, system=None, tools=None, temperature=None, json_schema=None, max_tokens=None):
    """One complete reply. `json_schema={}` asks for any JSON object."""
    body = _body(model, contents, system, tools, temperature, json_schema, False, max_tokens)
    with _open("/chat/completions", body) as resp:
        data = _read_json(resp)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("The model endpoint returned no choices")
    raw = str((choices[0].get("message") or {}).get("content") or "")
    if json_schema is not None:
        return Result(raw.strip(), None, _usage(data))
    cleaner = _Cleaner()
    text = cleaner.feed(raw)
    tail, call = cleaner.finish()
    return Result((text + tail).strip(), call, _usage(data))


def stream(model, contents, system=None, tools=None, temperature=None, max_tokens=None):
    """Yield Chunk(text, tool_call, usage) as the reply arrives."""
    cleaner, usage = _Cleaner(), None
    body = _body(model, contents, system, tools, temperature, None, True, max_tokens)
    with _open("/chat/completions", body) as resp:
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
                text = (choice.get("delta") or {}).get("content")
                visible = cleaner.feed(str(text)) if text else ""
                if visible:
                    yield Chunk(visible, None, None)
    tail, call = cleaner.finish()
    if tail:
        yield Chunk(tail, None, None)
    yield Chunk("", call, usage)


def embed_batch(texts):
    """Vectors for many texts in one request, in order."""
    body = {"model": EMBED_MODEL, "input": list(texts)}
    if EMBED_DIMENSIONS:
        body["dimensions"] = int(EMBED_DIMENSIONS)
    with _open("/embeddings", body) as resp:
        data = _read_json(resp)
    rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
    if len(rows) != len(texts):
        raise LLMError("The embeddings endpoint returned %d vectors for %d inputs" % (len(rows), len(texts)))
    return [r["embedding"] for r in rows]


def embed(text):
    return embed_batch([text])[0]
