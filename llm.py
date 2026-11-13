"""One seam between Chronos and the model that generates its text and embeddings.

Chat goes to one of two backends (LLM_PROVIDER), server side only: the keys never
reach browser or Android code.

  gemini (chosen when GEMINI_API_KEY is set): Google's native Gemini API.
    GEMINI_API_KEY      AI Studio key, read from the environment only
    GEMINI_MODEL        default model (gemini-3.5-flash-lite); CHAT_MODEL / TOOL_MODEL override it
    GEMINI_RPM / GEMINI_RPD   free-tier caps per model per process (default 9 a minute,
                        240 a day); past them calls raise QuotaExceeded with a student-safe message
  openrouter: OpenRouter's OpenAI-compatible `/api/v1/chat/completions` (or any such endpoint).
    LLM_BASE_URL        chat endpoint root (default https://openrouter.ai/api/v1)
    OPENROUTER_API_KEY  bearer token for chat
    OPENROUTER_MODEL    comma-separated models tried in order (default free NVIDIA Nemotron slugs); also the automatic
                        fallback when Gemini hits its caps or errors (LLM_FALLBACK=0 disables)
  Either way, HTTP 429 from the provider also raises QuotaExceeded.
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
import re
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque, namedtuple
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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

# Which chat backend: "gemini" (Google's native Gemini API) or "openrouter" (any
# OpenAI-compatible endpoint). Setting GEMINI_API_KEY selects Gemini unless LLM_PROVIDER says otherwise.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
PROVIDER = (os.getenv("LLM_PROVIDER", "").strip().lower()
            or ("gemini" if GEMINI_API_KEY else "openrouter"))
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL",
                            "https://generativelanguage.googleapis.com/v1beta").strip().rstrip("/")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
# Free-tier guard. Google's free tier has per-project limits per model (requests a minute and a
# day, reset at midnight Pacific). These caps stop Chronos before it reaches them and tell the
# student the tutor is busy, instead of sending requests that would fail. They are counted per
# server process, so with several server processes each gets its own budget. They are a
# backstop: the guarantee of never paying is a Google project with no billing account. 0 = no cap.
GEMINI_RPM = int(os.getenv("GEMINI_RPM", "9"))
GEMINI_RPD = int(os.getenv("GEMINI_RPD", "240"))
QUOTA_WAIT_S = 20                 # a request waits this long for a per-minute slot before giving up
BUSY_MINUTE = "The tutor is busy right now. Please try again in a minute."
BUSY_DAY = "The tutor has reached its free daily limit. It resets at midnight Pacific time."

# OpenRouter model: the primary when LLM_PROVIDER=openrouter, and the automatic fallback when Gemini is
# over its free-tier caps or errors. Fallback is on whenever OPENROUTER_API_KEY is set (FALLBACK=0 turns it off).
# Comma-separated, tried in order: free slugs get retired without notice, so a dead or rate-limited
# first choice just moves on to the next.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free,"
                             "nvidia/nemotron-3.5-lightning:free,"
                             "nvidia/nemotron-3-ultra-550b-a55b:free,"
                             "nvidia/nemotron-3-nano-30b-a3b:free").strip()
OPENROUTER_MODELS = [m.strip() for m in OPENROUTER_MODEL.split(",") if m.strip()]
FALLBACK = os.getenv("LLM_FALLBACK", "1").strip().lower() not in ("0", "off", "false", "no")
DEFAULT_CHAT_MODEL = GEMINI_MODEL if PROVIDER == "gemini" else (OPENROUTER_MODELS or [""])[0]
EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", "https://chronos-embed.tevproject.com/v1").strip().rstrip("/")
# Used only if GET /models on the embedding server cannot be read; normally the id it returns wins.
EMBED_MODEL = "embeddinggemma"
# Pins the name recorded for the index. The server reports whatever it was launched with (a file
# name, a Hugging Face path, an --alias), so without this a restart that changes the launch command
# looks like a model switch and the startup check refuses to run. Set it once and leave it.
EMBED_MODEL_ID = os.getenv("EMBED_MODEL_ID", "").strip()
# Must equal the Pinecone index dimension. Vectors from different models never share an index.
EMBED_DIM = 768
EMBED_BATCH = 16                  # inputs per /embeddings request (16-32 is safe)
EMBED_UNAVAILABLE = ("Course search is temporarily unavailable (the embedding server is "
                     "unreachable). Please try again in a few minutes.")
_embed_model = None

# Default sampling; the caller's temperature overrides.
# Mild penalties curb the model looping on its own earlier phrasing across a long chat.
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 64,
            "frequency_penalty": float(os.getenv("LLM_FREQUENCY_PENALTY", "0.3")),
            "presence_penalty": float(os.getenv("LLM_PRESENCE_PENALTY", "0.2"))}
# Reasoning tokens count against max_tokens, so this leaves room for both.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16384"))
# This model requires reasoning by default. Non-streaming calls exclude it; streams
# receive it on a separate field (never in the reply) so the UI can show a thinking phase.
# LLM_REASONING_EFFORT (low/medium/high) optionally bounds how much it thinks.
# `think=False` asks the endpoint to switch it off; if the model refuses (it may make
# reasoning mandatory) the request is retried once with it on and the answer remembered.
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
_can_disable_thinking = None     # None = not tried yet
# Prompt budget (tokens, estimated as chars/3; the oldest turns go first). Sized for a
# hosted model with a large window; lower it if the model's window is small.
PROMPT_TOKENS = int(os.getenv("LLM_PROMPT_TOKENS", "150000"))
MESSAGE_OVERHEAD_TOKENS = 50      # chat-template overhead per message

# Cloudflare rejects urllib's default "Python-urllib/x.y" agent with a 403 (error 1010).
USER_AGENT = "Chronos/1.0"

TOOL_MARKER = "TOOL_CALL"
THOUGHT_OPEN, THOUGHT_CLOSE = "<|channel>", "<channel|>"
# Models often ignore TOOL_CALL and write the call in the format they were trained on. Every
# common one is accepted and treated as a call, never as text: <x_function_call>/<function_calls>/
# <tool_call> wrappers, <invoke name=..><parameter name=..>, <function=..><parameter=..>, and
# Hermes-style <tool_call>{"name":..,"arguments":{..}}</tool_call>.
_XML_OPEN_RE = re.compile(r"<(?:[a-z0-9_]*_)?(?:function_calls?|tool_calls?)>|<invoke\s|<function=")
_XML_PARTIAL_RE = re.compile(r"<[a-z0-9_=/ ]{0,40}$")

# `tool_call` is the first call, `calls` every call the reply made (a reply may end with several).
Result = namedtuple("Result", "text tool_call usage calls", defaults=((),))
Chunk = namedtuple("Chunk", "text tool_call usage thinking calls", defaults=(False, ()))


class LLMError(RuntimeError):
    """The endpoint failed or returned something unusable."""


class ContextTooLong(LLMError):
    """HTTP 400: prompt + reply do not fit the context window."""


class EmbeddingsUnavailable(LLMError):
    """The embedding server is down, or returned vectors we must not store or search with."""


class QuotaExceeded(LLMError):
    """The model's rate limit (ours or the provider's HTTP 429) is used up. The message is student-safe."""


# ---------- free-tier quota ----------

try:
    _PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:                 # no tz database in the image
    _PACIFIC = timezone(timedelta(hours=-8))
_quota_lock = threading.Lock()
_quota_minute = {}                # model -> deque of request times in the last 60 s
_quota_day = {}                   # model -> (Pacific date, requests that day)


def _take_quota(model):
    """Count one request against the caps, waiting briefly for a per-minute slot. Raises QuotaExceeded."""
    for _ in range(3):
        with _quota_lock:
            now = time.time()
            today = datetime.fromtimestamp(now, _PACIFIC).date()
            minute = _quota_minute.setdefault(model, deque())
            while minute and now - minute[0] >= 60:
                minute.popleft()
            day, used = _quota_day.get(model, (today, 0))
            used = used if day == today else 0
            if GEMINI_RPD > 0 and used >= GEMINI_RPD:
                logger.warning("Daily request cap reached for %s (%d).", model, GEMINI_RPD)
                raise QuotaExceeded(BUSY_DAY)
            if GEMINI_RPM <= 0 or len(minute) < GEMINI_RPM:
                minute.append(now)
                _quota_day[model] = (today, used + 1)
                return
            wait = 60 - (now - minute[0])
        if wait > QUOTA_WAIT_S:
            break
        time.sleep(wait + 0.05)
    logger.warning("Per-minute request cap reached for %s (%d).", model, GEMINI_RPM)
    raise QuotaExceeded(BUSY_MINUTE)


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
                xml = _XML_OPEN_RE.search(self.buf)
                if xml:
                    hits.append((xml.start(), xml.group()))
                if hits:
                    i, marker = min(hits)
                    out.append(self.buf[:i])
                    self.buf = self.buf[i + len(marker):]
                    self.mode = "thought" if marker == THOUGHT_OPEN else "tool"
                    if marker not in (THOUGHT_OPEN, TOOL_MARKER):
                        self.tool_text += marker
                    continue
                keep = max((k for m in (THOUGHT_OPEN, TOOL_MARKER) for k in range(1, len(m))
                            if self.buf.endswith(m[:k])), default=0)
                partial = _XML_PARTIAL_RE.search(self.buf)
                if partial:
                    keep = max(keep, len(self.buf) - partial.start())
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
    if isinstance(call, dict) and call.get("name"):
        args = next((call[k] for k in ("args", "arguments", "parameters") if isinstance(call.get(k), dict)), None)
        if args is not None:
            return {"name": str(call["name"]), "args": args}
    return None


def _parse_calls(text):
    """Every well-formed call in the text that followed the first marker; the rest are dropped."""
    calls = []
    for part in (text or "").split(TOOL_MARKER):
        call = _parse_call(part)
        if call:
            calls.append(call)
    return calls or _parse_xml_calls(text)


_INVOKE_RE = re.compile(r'<invoke\s+name\s*=\s*"([^"]+)"\s*>(.*?)(?=<invoke\s|$)', re.S)
_FUNCTION_RE = re.compile(r'<function=([\w.-]+)>(.*?)(?=<function=|$)', re.S)
_PARAM_RE = re.compile(r'<parameter(?:\s+name\s*=\s*"([^"]+)"|=([\w.-]+))\s*>(.*?)</parameter>', re.S)


def _xml_args(body):
    args = {}
    for quoted, bare, raw in _PARAM_RE.findall(body):
        raw = raw.strip()
        try:
            args[quoted or bare] = json.loads(raw)
        except ValueError:
            args[quoted or bare] = raw
    return args


def _parse_xml_calls(text):
    """Calls in the other common formats (see XML markers above); stray or unclosed tags are ignored."""
    text = text or ""
    calls = [{"name": n.strip(), "args": _xml_args(b)}
             for n, b in _INVOKE_RE.findall(text) + _FUNCTION_RE.findall(text)]
    if calls:
        return calls
    pos = 0    # Hermes style: JSON objects inside the wrapper
    while (pos := text.find("{", pos)) >= 0:
        try:
            _, end = json.JSONDecoder().raw_decode(text[pos:])
        except ValueError:
            pos += 1
            continue
        call = _parse_call(text[pos:pos + end])
        if call:
            calls.append(call)
        pos += end
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
    return _post(base + path, body, {"Authorization": "Bearer " + key}, tries, base)


def _post(url, body, auth, tries=None, label=None, before_each=None):
    """POST JSON with retries on 5xx/network errors. HTTP 429 raises QuotaExceeded, never retried."""
    tries = tries or TRIES
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, **auth}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(1, tries + 1):
        if before_each:
            before_each()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
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
            if e.code == 429:
                logger.warning("Model endpoint rate limit (HTTP 429): %s", detail[:300])
                daily = any(w in detail.lower() for w in ("per day", "perday", "daily"))
                raise QuotaExceeded(BUSY_DAY if daily else BUSY_MINUTE) from e
            raise LLMError("Model endpoint returned HTTP %s %s" % (e.code, detail)) from e
        except (urllib.error.URLError, OSError) as e:
            if attempt < tries:
                time.sleep(2 ** (attempt - 1))
                continue
            raise LLMError("Could not reach the model endpoint at %s: %s" % (label or url, e)) from e


def _read_json(resp):
    try:
        data = json.loads(resp.read().decode("utf-8"))
    except ValueError as e:
        raise LLMError("The model endpoint returned a non-JSON response") from e
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise LLMError("Model endpoint: %s" % (err.get("message") if isinstance(err, dict) else err))
    return data


# ---------- Gemini (native generateContent API) ----------
# The OpenAI-compatible Gemini endpoint rejects the newer "AQ." AI Studio keys, so this
# speaks Google's own API. Tools still use the text protocol above; penalties are not sent
# (several Gemini models reject them).

_warned_model = False


def _gemini_model(model):
    """The Gemini model id; an OpenRouter-style "vendor/name" left in CHAT_MODEL falls back to GEMINI_MODEL."""
    global _warned_model
    m = (model or GEMINI_MODEL).strip()
    m = m[len("models/"):] if m.startswith("models/") else m
    if "/" in m or ":" in m:
        if not _warned_model:
            logger.warning("Model %r is not a Gemini model id; using %s instead.", m, GEMINI_MODEL)
            _warned_model = True
        m = GEMINI_MODEL
    return m


def _gemini_body(contents, system, tools, temperature, json_schema, max_tokens, think, stream):
    msgs = _messages(contents, system, tools)
    body, turns = {}, []
    for m in msgs:
        text = m["content"]
        if m["role"] == "system":
            body["systemInstruction"] = {"parts": [{"text": text}]}
            continue
        if not text.strip():
            continue                       # Gemini rejects empty parts
        role = "model" if m["role"] == "assistant" else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            turns.append({"role": role, "parts": [{"text": text}]})
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "parts": [{"text": "(continuing our conversation)"}]})
    body["contents"] = turns
    cfg = {"temperature": SAMPLING["temperature"] if temperature is None else temperature,
           "topP": SAMPLING["top_p"], "topK": SAMPLING["top_k"],
           "maxOutputTokens": max_tokens or MAX_TOKENS}
    if not think and _can_disable_thinking is not False:
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    elif stream:
        cfg["thinkingConfig"] = {"includeThoughts": True}      # lets the UI show a thinking phase
    if json_schema is not None:
        cfg["responseMimeType"] = "application/json"
        if json_schema:
            cfg["responseJsonSchema"] = json_schema
    body["generationConfig"] = cfg
    return body


def _gemini_open(model, body, stream):
    m = _gemini_model(model)
    url = "%s/models/%s:%s" % (GEMINI_BASE_URL, m, "streamGenerateContent?alt=sse" if stream else "generateContent")
    return _post(url, body, {"x-goog-api-key": GEMINI_API_KEY}, label=GEMINI_BASE_URL,
                 before_each=lambda: _take_quota(m))


def _gemini_parts(data):
    """(visible text, whether thought parts were present) from one response or stream chunk."""
    if not data.get("candidates") and (data.get("promptFeedback") or {}).get("blockReason"):
        raise LLMError("Gemini blocked the prompt: %s" % data["promptFeedback"]["blockReason"])
    text, thought = [], False
    for cand in (data.get("candidates") or [])[:1]:
        for p in (cand.get("content") or {}).get("parts") or []:
            if p.get("thought"):
                thought = True
            elif p.get("text"):
                text.append(str(p["text"]))
    return "".join(text), thought


def _gemini_usage(data):
    u = (data or {}).get("usageMetadata") or {}
    ints = lambda *ks: [u[k] for k in ks if isinstance(u.get(k), int)]
    p = (ints("promptTokenCount") or [None])[0]
    r = sum(ints("candidatesTokenCount", "thoughtsTokenCount")) if ints("candidatesTokenCount", "thoughtsTokenCount") else None
    t = (ints("totalTokenCount") or [None])[0]
    t = t if t is not None else ((p + r) if p is not None and r is not None else None)
    return {"prompt": p, "reply": r, "total": t}


def _gemini_generate(model, contents, system, tools, temperature, json_schema, max_tokens, think):
    body = lambda th: _gemini_body(contents, system, tools, temperature, json_schema, max_tokens, th, False)
    with _with_thinking_fallback(lambda th: _gemini_open(model, body(th), False), think) as resp:
        data = _read_json(resp)
    raw, _ = _gemini_parts(data)
    if json_schema is not None:
        return Result(raw.strip(), None, _gemini_usage(data))
    cleaner = _Cleaner()
    text = cleaner.feed(raw)
    tail, calls = cleaner.finish()
    return Result((text + tail).strip(), calls[0] if calls else None, _gemini_usage(data), calls)


def _gemini_stream(model, contents, system, tools, temperature, max_tokens, think):
    cleaner, usage, thinking = _Cleaner(), None, False
    body = lambda th: _gemini_body(contents, system, tools, temperature, None, max_tokens, th, True)
    with _with_thinking_fallback(lambda th: _gemini_open(model, body(th), True), think) as resp:
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
            if data.get("usageMetadata"):
                usage = _gemini_usage(data)
            text, thought = _gemini_parts(data)
            if thought and not thinking:
                thinking = True
                yield Chunk("", None, None, True)
            visible = cleaner.feed(text) if text else ""
            if visible:
                yield Chunk(visible, None, None)
    tail, calls = cleaner.finish()
    if tail:
        yield Chunk(tail, None, None)
    yield Chunk("", calls[0] if calls else None, usage, False, calls)


# ---------- public ----------

def _health_check(url, headers):
    req = urllib.request.Request(url, headers={**headers, "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=min(TIMEOUT_S, 10)) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise LLMError("Model endpoint returned HTTP %s" % e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError("Could not reach the model endpoint at %s: %s" % (url.split("/models")[0], e)) from e
    return True


def health(model=None):
    """Raise unless the endpoint is reachable and the key is accepted. Generates nothing.
    On Gemini, a working OpenRouter fallback also counts as healthy."""
    if PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            raise LLMError("GEMINI_API_KEY is not set")
        try:
            return _health_check("%s/models/%s" % (GEMINI_BASE_URL, _gemini_model(model)),
                                 {"x-goog-api-key": GEMINI_API_KEY})
        except LLMError:
            if not _can_fall_back():
                raise
            return _health_check(BASE_URL + "/models", {"Authorization": "Bearer " + CHAT_API_KEY})
    if not CHAT_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is not set")
    return _health_check(BASE_URL + "/models", {"Authorization": "Bearer " + CHAT_API_KEY})


def can_disable_thinking():
    """True/False once known from a real request, None until `think=False` has been tried."""
    return _can_disable_thinking


def _with_thinking_fallback(open_with, think):
    """open_with(think) -> response. With think=False, fall back to thinking if the model insists on it."""
    global _can_disable_thinking
    try:
        resp = open_with(think)
    except QuotaExceeded:
        raise
    except LLMError as e:
        msg = str(e).lower()
        if think or _can_disable_thinking is False or not ("reason" in msg or "think" in msg):
            raise
        _can_disable_thinking = False
        logger.warning("This model will not run without reasoning (%s); leaving it on.", e)
        return open_with(True)
    if not think and _can_disable_thinking is None:
        _can_disable_thinking = True
    return resp


def _open_chat(args, think):
    return _with_thinking_fallback(lambda th: _open("/chat/completions", _body(*args, th)), think)


def _or_models(model):
    """One model name, or the whole OPENROUTER_MODEL list when given the list or its first entry as the default."""
    if isinstance(model, (list, tuple)):
        return list(model)
    if PROVIDER != "gemini" and OPENROUTER_MODELS and model == OPENROUTER_MODELS[0]:
        return list(OPENROUTER_MODELS)
    return [model]


def _can_fall_back():
    return PROVIDER == "gemini" and FALLBACK and bool(CHAT_API_KEY)


def generate(model, contents, system=None, tools=None, temperature=None, json_schema=None, max_tokens=None,
             think=True):
    """One complete reply. `json_schema={}` asks for any JSON object.

    On Gemini, a quota stop or provider failure is retried once on OpenRouter (OPENROUTER_MODEL)."""
    if PROVIDER == "gemini":
        try:
            return _gemini_generate(model, contents, system, tools, temperature, json_schema, max_tokens, think)
        except ContextTooLong:
            raise
        except LLMError as e:
            if not _can_fall_back():
                raise
            logger.warning("Gemini failed (%s); falling back to OpenRouter %s.", e, OPENROUTER_MODELS)
            model = OPENROUTER_MODELS
    return _openrouter_generate(model, contents, system, tools, temperature, json_schema, max_tokens, think)


def _openrouter_generate(model, *args):
    last = None
    for m in _or_models(model):
        try:
            return _openrouter_generate_one(m, *args)
        except ContextTooLong:
            raise
        except LLMError as e:
            last = e
            logger.warning("OpenRouter model %s failed (%s).", m, e)
    raise last


def _openrouter_generate_one(model, contents, system, tools, temperature, json_schema, max_tokens, think):
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
    """Yield Chunk(text, tool_call, usage) as the reply arrives.

    On Gemini, a failure before any text has been sent falls back to OpenRouter; after that it propagates."""
    if PROVIDER == "gemini":
        started = False
        try:
            for chunk in _gemini_stream(model, contents, system, tools, temperature, max_tokens, think):
                started = started or bool(chunk.text or chunk.tool_call)
                yield chunk
            return
        except ContextTooLong:
            raise
        except LLMError as e:
            if started or not _can_fall_back():
                raise
            logger.warning("Gemini failed (%s); falling back to OpenRouter %s.", e, OPENROUTER_MODELS)
            model = OPENROUTER_MODELS
    yield from _openrouter_stream(model, contents, system, tools, temperature, max_tokens, think)


def _openrouter_stream(model, *args):
    last = None
    for m in _or_models(model):
        started = False
        try:
            for chunk in _openrouter_stream_one(m, *args):
                started = started or bool(chunk.text or chunk.tool_call)
                yield chunk
            return
        except ContextTooLong:
            raise
        except LLMError as e:
            if started:
                raise
            last = e
            logger.warning("OpenRouter model %s failed (%s).", m, e)
    raise last


def _openrouter_stream_one(model, contents, system, tools, temperature, max_tokens, think):
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
    return (EMBED_MODEL_ID or embed_model()), len(vec)
