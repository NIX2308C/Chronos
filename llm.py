"""One seam between Chronos and whichever model generates its text and embeddings.

Chronos speaks the OpenAI-compatible wire format (`/chat/completions` and
`/embeddings`), so pointing it somewhere else is a config change, not a code
change. That covers OpenAI itself, Ollama (`http://host:11434/v1`), LM Studio,
vLLM, llama.cpp's server, OpenRouter, or any of those reached through a
forwarded port or SSH tunnel. See "Connecting a model" in README.md.

    LLM_BASE_URL   endpoint root including /v1   (placeholder: http://localhost:11434/v1)
    LLM_API_KEY    bearer token; leave empty for a local server that needs none
    CHAT_MODEL / TOOL_MODEL / EMBED_MODEL   model names the endpoint serves
    LLM_TIMEOUT_S  per-request timeout

The request shape app.py builds: `contents` is a string or a list of
{"role": "user"|"model", "parts": [{"text": ...}]} turns. Tools are plain
JSON-schema function declarations and `json_schema` is a plain JSON Schema.

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
import urllib.error
import urllib.request
from collections import namedtuple

logger = logging.getLogger(__name__)

# ---- The placeholders: change these (env vars) to point at a real endpoint. ----
BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").strip().rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "").strip()
# A request that never returns holds a Waitress thread forever, so it is bounded.
TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "120"))

DEFAULT_CHAT_MODEL = "llama3.1"
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
# Must equal the Pinecone index dimension. Sent as `dimensions` only when set,
# because only some servers (OpenAI v3 models) accept that parameter.
EMBED_DIMENSIONS = os.getenv("EMBED_DIMENSIONS", "").strip()

Result = namedtuple("Result", "text tool_call usage")
Chunk = namedtuple("Chunk", "text tool_call usage")


class LLMError(RuntimeError):
    """The endpoint failed or returned something unusable."""


# ---------- wire helpers ----------

def _messages(contents, system):
    """Chronos turns -> OpenAI chat messages."""
    messages = [{"role": "system", "content": system}] if system else []
    if isinstance(contents, str):
        return messages + [{"role": "user", "content": contents}]
    for turn in contents or []:
        text = "".join(str(p.get("text") or "") for p in (turn.get("parts") or []) if isinstance(p, dict))
        role = "assistant" if turn.get("role") in ("model", "assistant") else "user"
        messages.append({"role": role, "content": text})
    return messages


def _body(model, contents, system, tools, temperature, json_schema, stream):
    body = {"model": model, "messages": _messages(contents, system), "stream": stream}
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("parameters") or {"type": "object", "properties": {}}}} for t in tools]
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


def _open(path, body):
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = "Bearer " + API_KEY
    req = urllib.request.Request(BASE_URL + path, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT_S)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            err = json.loads(e.read().decode("utf-8", "replace")).get("error")
            detail = (err.get("message") if isinstance(err, dict) else err) or ""
        except Exception:
            pass
        raise LLMError("Model endpoint returned HTTP %s %s" % (e.code, detail)) from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError("Could not reach the model endpoint at %s: %s" % (BASE_URL, e)) from e


def _tool_call(message):
    for call in (message or {}).get("tool_calls") or []:
        fn = (call or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            # The spec says arguments is a JSON string; some servers send an object.
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = None
        if fn.get("name") and isinstance(args, dict):
            return {"name": fn["name"], "args": args}
    return None


def _usage(data):
    u = (data or {}).get("usage") or {}
    p, r, t = u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens")
    p = p if isinstance(p, int) else None
    r = r if isinstance(r, int) else None
    t = t if isinstance(t, int) else ((p + r) if p is not None and r is not None else None)
    return {"prompt": p, "reply": r, "total": t}


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
    """Raise unless the endpoint is reachable. Generates nothing."""
    headers = {"Authorization": "Bearer " + API_KEY} if API_KEY else {}
    req = urllib.request.Request(BASE_URL + "/models", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=min(TIMEOUT_S, 10)) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        if e.code not in (404, 405):    # some servers do not implement /models
            raise LLMError("Model endpoint returned HTTP %s" % e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError("Could not reach the model endpoint at %s: %s" % (BASE_URL, e)) from e
    return True


def generate(model, contents, system=None, tools=None, temperature=None, json_schema=None):
    """One complete reply. `json_schema={}` asks for any JSON object."""
    with _open("/chat/completions", _body(model, contents, system, tools, temperature, json_schema, False)) as resp:
        data = _read_json(resp)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("The model endpoint returned no choices")
    message = choices[0].get("message") or {}
    return Result(str(message.get("content") or "").strip(), _tool_call(message), _usage(data))


def stream(model, contents, system=None, tools=None, temperature=None):
    """Yield Chunk(text, tool_call, usage) as the reply arrives."""
    calls = {}      # tool-call deltas arrive in fragments, keyed by index
    usage = None
    with _open("/chat/completions", _body(model, contents, system, tools, temperature, None, True)) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
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
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") if isinstance(fn.get("arguments"), str) else (
                        json.dumps(fn["arguments"]) if fn.get("arguments") else "")
                text = delta.get("content")
                if text:
                    yield Chunk(str(text), None, None)
    tool = _tool_call({"tool_calls": [{"function": c} for _, c in sorted(calls.items())]})
    yield Chunk("", tool, usage)


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
