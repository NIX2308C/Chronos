"""One seam between Chronos and whichever model generates its text.

Every generation call in app.py goes through `generate` or `stream` here, so the
provider is a deployment choice (LLM_PROVIDER) rather than something threaded
through the routes. Embeddings are deliberately not part of this: the Pinecone
index holds Gemini vectors, and a different embedding model would make every
course retrieve nothing (see docs/OLLAMA.md).

The request shape is the one app.py already builds for Gemini: `contents` is a
string or a list of {"role": "user"|"model", "parts": [{"text": ...}]} turns.
Tools are plain JSON-schema function declarations and `json_schema` is a plain
JSON Schema, so both backends read the same definitions.

Results are backend-neutral:
    generate(...) -> Result(text, tool_call, usage)
    stream(...)   -> iterator of Chunk(text, tool_call, usage)
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

PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower() or "gemini"
if PROVIDER not in ("gemini", "ollama"):
    raise RuntimeError("LLM_PROVIDER must be 'gemini' or 'ollama', not %r" % PROVIDER)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
# An Ollama endpoint reachable from the internet is an open LLM on your bill, so
# a deployed one sits behind a proxy that checks this bearer token.
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")
# Same reason as GEMINI_TIMEOUT_MS: a request that never returns holds a Waitress
# thread forever. A loaded GPU is a likelier source of one than a hosted API.
OLLAMA_TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "120"))
# How long Ollama keeps the weights resident after a request.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

DEFAULT_CHAT_MODEL = "gemma4:e4b-it-qat" if PROVIDER == "ollama" else "gemini-2.5-flash-lite"

Result = namedtuple("Result", "text tool_call usage")
Chunk = namedtuple("Chunk", "text tool_call usage")


class LLMError(RuntimeError):
    """The provider failed or returned something unusable."""


def _empty_usage():
    return {"prompt": None, "reply": None, "total": None}


# ---------- Gemini ----------

# app.py owns the google-genai client (tests replace `app.client` wholesale), so
# the backend asks for it at call time instead of holding a reference.
_gemini_client = None


def use_gemini_client(getter):
    """Register a zero-argument callable returning the google-genai client."""
    global _gemini_client
    _gemini_client = getter


def _gemini_types():
    from google.genai import types
    return types


def _gemini_config(system, tools, temperature, json_schema):
    types = _gemini_types()
    kw = {}
    if system:
        kw["system_instruction"] = system
    if temperature is not None:
        kw["temperature"] = temperature
    if tools:
        kw["tools"] = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t.get("description", ""),
                                      parameters_json_schema=t.get("parameters") or {"type": "object"})
            for t in tools])]
    if json_schema is not None:
        kw["response_mime_type"] = "application/json"
        if json_schema:
            kw["response_json_schema"] = json_schema
    return types.GenerateContentConfig(**kw) if kw else None


def _gemini_usage(usage):
    def count(name):
        value = getattr(usage, name, None)
        return value if isinstance(value, int) else None
    return {"prompt": count("prompt_token_count"), "reply": count("candidates_token_count"),
            "total": count("total_token_count")}


def _gemini_parts(response):
    for candidate in (getattr(response, "candidates", None) or []):
        for part in (getattr(getattr(candidate, "content", None), "parts", None) or []):
            yield part


def _gemini_text(response):
    """Visible text, tolerating a function-call-only reply (`.text` is None then)."""
    try:
        parts = [p.text for p in _gemini_parts(response) if getattr(p, "text", None)]
        if parts:
            return "".join(parts)
    except Exception:
        logger.exception("Could not read text off the model response.")
    try:
        return response.text or ""
    except Exception:
        return ""


def _gemini_tool_call(response):
    try:
        for part in _gemini_parts(response):
            call = getattr(part, "function_call", None)
            if call and getattr(call, "name", None):
                return {"name": call.name, "args": dict(call.args or {})}
    except Exception:
        # A malformed response should cost the student a quiz, never their answer.
        logger.exception("Could not read a tool call off the model response.")
    return None


def _gemini_generate(model, contents, system, tools, temperature, json_schema):
    config = _gemini_config(system, tools, temperature, json_schema)
    kw = {"model": model, "contents": contents}
    if config is not None:
        kw["config"] = config
    resp = _gemini_client().models.generate_content(**kw)
    return Result(_gemini_text(resp).strip(), _gemini_tool_call(resp),
                  _gemini_usage(getattr(resp, "usage_metadata", None)))


def _gemini_stream(model, contents, system, tools, temperature):
    config = _gemini_config(system, tools, temperature, None)
    kw = {"model": model, "contents": contents}
    if config is not None:
        kw["config"] = config
    for chunk in _gemini_client().models.generate_content_stream(**kw):
        text = "".join(p.text for p in _gemini_parts(chunk) if getattr(p, "text", None))
        usage = getattr(chunk, "usage_metadata", None)
        yield Chunk(text, _gemini_tool_call(chunk), _gemini_usage(usage) if usage else None)


# ---------- Ollama ----------

def _ollama_messages(contents, system):
    """Gemini-shaped contents -> Ollama chat messages."""
    messages = [{"role": "system", "content": system}] if system else []
    if isinstance(contents, str):
        return messages + [{"role": "user", "content": contents}]
    for turn in contents or []:
        text = "".join(str(p.get("text") or "") for p in (turn.get("parts") or []) if isinstance(p, dict))
        role = "assistant" if turn.get("role") in ("model", "assistant") else "user"
        messages.append({"role": role, "content": text})
    return messages


def _ollama_body(model, contents, system, tools, temperature, json_schema, stream):
    body = {"model": model, "messages": _ollama_messages(contents, system), "stream": stream,
            "keep_alive": OLLAMA_KEEP_ALIVE}
    if temperature is not None:
        body["options"] = {"temperature": temperature}
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("parameters") or {"type": "object"}}} for t in tools]
    if json_schema is not None:
        # A schema constrains decoding itself, which a prompt instruction cannot.
        body["format"] = json_schema or "json"
    return body


def _ollama_open(body, path="/api/chat"):
    headers = {"Content-Type": "application/json"}
    if OLLAMA_API_KEY:
        headers["Authorization"] = "Bearer " + OLLAMA_API_KEY
    req = urllib.request.Request(OLLAMA_URL + path, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error") or ""
        except Exception:
            pass
        raise LLMError("Ollama returned HTTP %s %s" % (e.code, detail)) from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError("Could not reach Ollama at %s: %s" % (OLLAMA_URL, e)) from e


def _ollama_tool_call(message):
    for call in (message or {}).get("tool_calls") or []:
        fn = (call or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            # Some templates hand arguments back as a JSON string.
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        if fn.get("name") and isinstance(args, dict):
            return {"name": fn["name"], "args": args}
    return None


def _ollama_usage(data):
    p, r = data.get("prompt_eval_count"), data.get("eval_count")
    p = p if isinstance(p, int) else None
    r = r if isinstance(r, int) else None
    return {"prompt": p, "reply": r, "total": (p + r) if p is not None and r is not None else None}


def _ollama_generate(model, contents, system, tools, temperature, json_schema):
    with _ollama_open(_ollama_body(model, contents, system, tools, temperature, json_schema, False)) as resp:
        try:
            data = json.loads(resp.read().decode("utf-8"))
        except ValueError as e:
            raise LLMError("Ollama returned a non-JSON response") from e
    if data.get("error"):
        raise LLMError("Ollama: %s" % data["error"])
    message = data.get("message") or {}
    return Result(str(message.get("content") or "").strip(), _ollama_tool_call(message), _ollama_usage(data))


def _ollama_stream(model, contents, system, tools, temperature):
    with _ollama_open(_ollama_body(model, contents, system, tools, temperature, None, True)) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                logger.warning("Skipping an unreadable Ollama stream line")
                continue
            if data.get("error"):
                raise LLMError("Ollama: %s" % data["error"])
            message = data.get("message") or {}
            yield Chunk(str(message.get("content") or ""), _ollama_tool_call(message),
                        _ollama_usage(data) if data.get("done") else None)


# ---------- public ----------

def health(model):
    """Raise unless the provider is reachable and has `model`. Generates nothing."""
    if PROVIDER == "ollama":
        with _ollama_open({"model": model}, "/api/show") as resp:
            resp.read()
        return True
    _gemini_client().models.get(model=model)
    return True


def generate(model, contents, system=None, tools=None, temperature=None, json_schema=None):
    """One complete reply. `json_schema={}` asks for any JSON object."""
    if PROVIDER == "ollama":
        return _ollama_generate(model, contents, system, tools, temperature, json_schema)
    return _gemini_generate(model, contents, system, tools, temperature, json_schema)


def stream(model, contents, system=None, tools=None, temperature=None):
    """Yield Chunk(text, tool_call, usage) as the reply arrives."""
    if PROVIDER == "ollama":
        return _ollama_stream(model, contents, system, tools, temperature)
    return _gemini_stream(model, contents, system, tools, temperature)
