import os
import gc
import re
import hashlib
import json
import time
import hmac
import random
import secrets
import logging
import zipfile
from datetime import datetime, timezone
from functools import wraps
from collections import deque
from threading import Lock, Thread
from dotenv import load_dotenv
from flask import Flask, Response, request, jsonify, send_from_directory, redirect, stream_with_context
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import firebase_admin
from firebase_admin import credentials, firestore, auth as fb_auth
import agent_tools
import llm
import prompts
import profanity
import student_profile
from pinecone import Pinecone
try:
    from google.cloud.devtools import cloudbuild_v1
    from google.cloud import run_v2
except ImportError:  # Kept optional so offline/unit-test imports still work.
    cloudbuild_v1 = None
    run_v2 = None
try:
    import pypdf
except ImportError:
    pypdf = None
try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("teacherai")

PINE_KEY = os.getenv("PINECONE_API_KEY")

# Secret code someone must enter to register a *teacher* account. Replaces the
# old shared TEACHER_PASSWORD: instead of every teacher typing one password on
# every request, they create a real Firebase email/password account once, and
# this code only gates whether that account is granted the teacher role.
TEACHER_SIGNUP_CODE = os.getenv("TEACHER_SIGNUP_CODE", "")

# Firebase Web SDK config served to the browser so the front-end can sign users
# in with email/password. These values are NOT secret (they ship in every
# Firebase web app), but we keep them configurable per-deployment. apiKey is
# required; the rest default from the service-account project id.
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "teacheraifrontend")
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "")
FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", f"{FIREBASE_PROJECT_ID}.firebaseapp.com")

# Run with the interactive debugger ONLY when explicitly enabled. Leaving the
# Werkzeug debugger on in a reachable deployment is a remote-code-execution risk.
DEBUG = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")

# Refuse to start with a missing, short, or well-known default teacher code —
# otherwise anyone could self-register as a teacher and read all chat logs / edit
# the knowledge base. The length floor is the part that catches the realistic
# mistake: a blocklist only ever knows the placeholders someone thought of, and
# "test1234" is not on anybody's list. 12 chars of anything is past the point
# where the REGISTER_RATE_LIMIT throttle is the only thing doing the work.
TEACHER_CODE_MIN_LEN = 12
if not TEACHER_SIGNUP_CODE or TEACHER_SIGNUP_CODE.lower() in ("changeme", "password", "admin", "skibidi"):
    raise SystemExit(
        "Refusing to start: set a strong TEACHER_SIGNUP_CODE in your .env "
        "(it is missing or set to an insecure default)."
    )
if len(TEACHER_SIGNUP_CODE) < TEACHER_CODE_MIN_LEN:
    raise SystemExit(
        f"Refusing to start: TEACHER_SIGNUP_CODE must be at least "
        f"{TEACHER_CODE_MIN_LEN} characters. It is the only thing standing "
        "between a stranger and every class's material and chat logs."
    )

# Browser origins allowed to call this API (CORS). Defaults to the Live Server
# origins; override with a comma-separated ALLOWED_ORIGINS env var if needed.
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500",
    ).split(",") if o.strip()
]

# Reject oversized request bodies (uploads / chat payloads) to limit DoS/memory abuse.
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_MESSAGE_CHARS = 8000
# MAX_UPLOAD_MB bounds what arrives on the wire, not what it becomes. A .docx is
# a zip and a .pdf holds compressed streams, so 10 MB of either can inflate to
# gigabytes — on a 512 MiB instance that's an OOM that takes every class on the
# box down with it. These cap the *decompressed* side: bytes for the archive
# check, characters for the text every format ends up as.
MAX_EXTRACT_BYTES = int(os.getenv("MAX_EXTRACT_BYTES", str(200 * 1024 * 1024)))
MAX_EXTRACT_CHARS = int(os.getenv("MAX_EXTRACT_CHARS", "2000000"))   # ~2 MB of text

# Lightweight rate limits. These are keyed by the caller's Firebase uid, not by
# IP: every rate-limited route here is behind @require_auth, and a uid is both
# harder to rotate than an IP and correct behind a proxy (on Cloud Run every
# request arrives from the front end / load balancer, so an IP key would
# throttle a whole class as if it were one student).
CHAT_RATE_LIMIT = int(os.getenv("CHAT_RATE_LIMIT", "20"))     # requests
CHAT_RATE_WINDOW = int(os.getenv("CHAT_RATE_WINDOW", "60"))   # seconds
# Join codes are the only secret guarding class membership, so guessing attempts
# are throttled hard (a code is 32^6 ≈ 1e9 wide, but only if you can't spray it).
JOIN_RATE_LIMIT = int(os.getenv("JOIN_RATE_LIMIT", "10"))     # requests
JOIN_RATE_WINDOW = int(os.getenv("JOIN_RATE_WINDOW", "300"))  # seconds
# ...and a second budget keyed by IP, for the same reason /auth/register has one:
# a uid costs an attacker nothing (anyone can mint a fresh Firebase account
# against the public web API key), so a per-uid budget alone resets on every
# guess and throttles nothing. This one is deliberately loose — a whole class
# behind one school NAT joins on the same afternoon, and locking them out is a
# worse outcome than a spray that still needs millions of years at this rate.
JOIN_IP_RATE_LIMIT = int(os.getenv("JOIN_IP_RATE_LIMIT", "60"))   # requests per window
# Teacher writes cost money on every call (embeddings, and a model
# generation per /stats). Nothing here is reachable without a teacher account, so
# this is an abuse ceiling on a compromised or careless teacher, not a gate.
TEACHER_RATE_LIMIT = int(os.getenv("TEACHER_RATE_LIMIT", "60"))     # requests
TEACHER_RATE_WINDOW = int(os.getenv("TEACHER_RATE_WINDOW", "60"))   # seconds
# Learning activities get their own budget rather than sharing the chat one. A
# tool almost always follows a chat turn, so a shared bucket charged a student
# two slots for one interaction and 429'd the quiz half of it first.
TOOL_RATE_LIMIT = int(os.getenv("TOOL_RATE_LIMIT", "12"))     # requests
TOOL_RATE_WINDOW = int(os.getenv("TOOL_RATE_WINDOW", "60"))   # seconds
# Teacher registration is gated by one shared code, which makes /auth/register the
# most valuable thing on the app to brute-force: guessing it once yields every
# class's material and every student's chat log. Unlike the limits above, this one
# is keyed by IP — a uid costs an attacker nothing (anyone can mint a fresh
# Firebase account against the public web API key), so a per-uid budget would
# reset on every guess and throttle nothing at all.
REGISTER_RATE_LIMIT = int(os.getenv("REGISTER_RATE_LIMIT", "5"))      # requests
REGISTER_RATE_WINDOW = int(os.getenv("REGISTER_RATE_WINDOW", "900"))  # seconds

# Number of proxies in front of the app whose X-Forwarded-For we trust. 0 (the
# default) means "no proxy": request.remote_addr stays the direct peer and
# spoofed forwarding headers are ignored. Set to 1 on Cloud Run so logs show the
# real client IP. Never set this higher than the number of proxies you actually
# control — each hop you trust is a hop a client can forge.
TRUST_PROXY_HOPS = int(os.getenv("TRUST_PROXY_HOPS", "0"))

# How many past messages to replay into the model so it remembers the conversation.
# Each Q&A is 2 messages, so 30 ≈ the last 15 exchanges. Capped to bound tokens/latency.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "30"))
# Once older turns are about to fall out of the replay window, roll them into a
# separate tutoring-state note. This is intentionally not course knowledge: it
# may guide pacing and follow-ups, but can never supply subject facts.
SUMMARY_BATCH_MESSAGES = int(os.getenv("SUMMARY_BATCH_MESSAGES", "8"))
CONVERSATION_SUMMARY_CHARS = int(os.getenv("CONVERSATION_SUMMARY_CHARS", "2400"))
# Hard cap on how many messages a single conversation will return, so one very
# long chat can't turn into an unbounded Firestore read + response body.
MAX_MESSAGES_RETURNED = int(os.getenv("MAX_MESSAGES_RETURNED", "500"))

# --- what the tutor remembers about a student, and what they hand it ---
# Everything here is keyed by class. That scoping *is* the feature: a student who
# switches class gets a tutor with no recollection of the other one.
MEMORY_CHATS = int(os.getenv("MEMORY_CHATS", "8"))     # earlier conversations recalled
MEMORY_TOPIC_CHARS = 120                               # per remembered question
MEMORY_MAX_GAPS = 5
# ponytail: the memory query filters by class but doesn't order as well (that
# needs a composite index), so a student with more chats in one class than this
# gets the newest of an arbitrary slice. Add the index + order_by if it bites.
MEMORY_QUERY_MAX = 60
# Alongside *what* a student asked, the tutor keeps a running read of *how* they
# write and where they keep getting stuck (student_profile.py). It is one document
# per student per class, keyed by class id, so it is scoped the same way the chat
# query above is — by construction rather than by a filter someone has to remember.
PROFILE_ENABLED = os.getenv("PROFILE_ENABLED", "1") != "0"
ROSTER_MAX = 500          # students listed on the teacher's per-student panel
# A student uploads work to be reviewed, not searched, so their document goes into
# the prompt whole rather than through Pinecone. That makes these context-window
# budgets, not storage ones — far tighter than MAX_EXTRACT_CHARS, and safely under
# Firestore's 1 MiB per-document limit.
STUDENT_DOC_CHARS = int(os.getenv("STUDENT_DOC_CHARS", "8000"))    # per document, ~2,700 tokens
STUDENT_DOCS_MAX = int(os.getenv("STUDENT_DOCS_MAX", "3"))         # per class
STUDENT_CONTEXT_CHARS = int(os.getenv("STUDENT_CONTEXT_CHARS", "20000"))   # all documents together, ~6,500 tokens
STUDENT_DOC_SUMMARY_CHARS = 700        # stored once at upload; stands in for a document that doesn't fit
STUDENT_DOC_KINDS = ("assignment", "rubric")
# How much of an upload the kind check reads. Enough to tell an essay from a
# textbook chapter, small enough that the call costs almost nothing.
STUDENT_DOC_CHECK_CHARS = int(os.getenv("STUDENT_DOC_CHECK_CHARS", "2500"))
STUDENT_UPLOAD_RATE_LIMIT = int(os.getenv("STUDENT_UPLOAD_RATE_LIMIT", "10"))
STUDENT_UPLOAD_RATE_WINDOW = int(os.getenv("STUDENT_UPLOAD_RATE_WINDOW", "300"))

_rate_hits = {}
_rate_lock = Lock()
_rate_last_prune = 0.0

# Role lookups happen on nearly every request; Firestore charges per read and
# adds a round-trip. Roles change ~never, so a short in-process TTL cache removes
# almost all of that traffic while keeping a role change visible within seconds.
ROLE_CACHE_TTL = int(os.getenv("ROLE_CACHE_TTL", "60"))       # seconds
ROLE_CACHE_MAX = 5000                                          # bound the memory
_role_cache = {}
_role_lock = Lock()

INDEX_NAME = "teacherchronostwo"
EMBED_DIM = llm.EMBED_DIM
# Budgets assume a hosted model with a large window (~3 chars a token): system+tool
# rules ~1,800 tokens, student documents up to 6,500, retrieved chunks 4,000, history
# 8,000, prompt total capped at llm.PROMPT_TOKENS. Lower these for a small-window model.
HISTORY_CHARS = int(os.getenv("HISTORY_CHARS", "24000"))
# Chat/generation model. flash-lite is cheaper and has higher throughput than
# 2.5-flash, so it scales better for a class of users. Override via env if needed.
CHAT_MODEL = os.getenv("CHAT_MODEL", llm.DEFAULT_CHAT_MODEL)
# Tool generation is intentionally separate from the tutoring reply. It gets a
# small purpose-built prompt plus retrieved course excerpts, not the whole chat
# system instruction or a student's private context.
TOOL_MODEL = os.getenv("TOOL_MODEL", CHAT_MODEL)

# Retrieval tuning for the tutor. RETRIEVAL_TOP_K is how many knowledge chunks we
# pull per question (more context generally = fuller answers). RETRIEVAL_MIN_SCORE
# is a cosine-similarity floor: chunks below it are dropped as not-really-related,
# so an off-topic question ends up with empty context and a truthful "not in my
# knowledge base" answer instead of being force-fed the least-bad matches. The
# index uses cosine; with embeddings on-topic chunks score ~0.52+ and
# unrelated ones ~0.48–0.51, so 0.5 is a sensible default. Tune per your material.
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "8"))
RETRIEVAL_MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.5"))
RETRIEVAL_CONTEXT_CHARS = int(os.getenv("RETRIEVAL_CONTEXT_CHARS", "12000"))
# Floor for the second-chance query (previous question + this one). Defaults to the
# main floor: unrelated chunks already sit just under it, so going lower would let
# the least-bad matches through as if they were answers.
RETRIEVAL_FALLBACK_SCORE = float(os.getenv("RETRIEVAL_FALLBACK_SCORE", str(RETRIEVAL_MIN_SCORE)))

# Course settings are deliberately few. Optional toolkits default off; the
# safety/grounding rules default on. Stored values are normalized through
# course_settings() before they ever reach a prompt.
COURSE_SETTINGS_DEFAULTS = {
    "grounded_only": True,
    "guide_not_complete": True,
    "state_uncertainty": True,
    "practice_tools": False,
    "visual_tools": False,
    "study_materials": False,
    "reveal_final_answers": False,
    "worked_examples": False,
    "hint_strength": "progressive",
    "additional_instructions": "",
}
COURSE_BOOL_SETTINGS = {
    "grounded_only", "guide_not_complete", "state_uncertainty",
    "practice_tools", "visual_tools", "study_materials",
    "reveal_final_answers", "worked_examples",
}

# Accounts with developer tooling (debug mode, extra personalities). Decided on the
# server from the verified token's email, never from anything the client sends.
DEV_EMAILS = {
    e.strip().lower()
    for e in os.getenv("DEV_EMAILS", "test@gmail.com").split(",") if e.strip()
}

# The status page reads deployment state with the Cloud Run *runtime* service
# account through Application Default Credentials. These are identifiers, not
# secrets; leave them blank outside the deployed service and the developer
# panel will simply report deployment data as unavailable.
CLOUD_STATUS_PROJECT_ID = os.getenv("CLOUD_STATUS_PROJECT_ID", "").strip()
CLOUD_STATUS_REGION = os.getenv("CLOUD_STATUS_REGION", "").strip()
CLOUD_STATUS_SERVICE = os.getenv("CLOUD_STATUS_SERVICE", "").strip()
CLOUD_STATUS_BUILD_TRIGGER_ID = os.getenv("CLOUD_STATUS_BUILD_TRIGGER_ID", "").strip()
STATUS_CHECK_TTL = max(10, int(os.getenv("STATUS_CHECK_TTL", "30")))
_status_cache = {"checked_at": 0.0, "services": None, "refreshing": False}
_status_lock = Lock()


def is_dev_user(user):
    """True when the verified Firebase token belongs to a developer account."""
    email = str((user or {}).get("email") or "").strip().lower()
    return bool(email) and email in DEV_EMAILS


def require_developer(fn):
    """Require a verified Firebase user in the server-owned developer allowlist."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        decoded = verify_user()
        if not decoded:
            return jsonify({"error": "Unauthorized. Please sign in."}), 401
        if not is_dev_user(decoded):
            return jsonify({"error": "Forbidden."}), 403
        request.user = decoded
        request.uid = decoded["uid"]
        return fn(*args, **kwargs)
    return wrapper


def _status_timestamp(value=None):
    """Return an ISO timestamp without ever leaking a provider-specific object."""
    value = value or datetime.now(timezone.utc)
    if hasattr(value, "ToDatetime"):
        value = value.ToDatetime()
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_status_checks():
    """Run bounded, low-cost dependency checks. Errors stay in server logs only."""
    checks = {
        "chronos": lambda: True,
        # Lists models on the configured endpoint without generating any text.
        "ai": lambda: llm.health(CHAT_MODEL),
        "data": lambda: list(db.collection("Classes").limit(1).stream()),
        "materials": lambda: pinecone_index.describe_index_stats(),
    }
    services = {}
    for name, check in checks.items():
        try:
            check()
            services[name] = "operational"
        except Exception:
            # Do not return the dependency name, exception, or credentials to the
            # public endpoint. Logs retain enough context for an operator.
            logger.exception("Status check failed for %s", name)
            services[name] = "degraded"
    return services


def public_status_snapshot():
    """Get a cached public-safe status response without stampeding providers."""
    now = time.time()
    with _status_lock:
        fresh = _status_cache["services"] and now - _status_cache["checked_at"] < STATUS_CHECK_TTL
        if fresh:
            services, checked_at = _status_cache["services"], _status_cache["checked_at"]
        elif _status_cache["refreshing"]:
            services, checked_at = _status_cache["services"], _status_cache["checked_at"]
            if not services:
                return {"status": "checking", "checked_at": None, "services": {
                    key: "checking" for key in ("chronos", "ai", "data", "materials")}}
        else:
            _status_cache["refreshing"] = True
            services = checked_at = None
    if services is None:
        try:
            services = _run_status_checks()
            checked_at = time.time()
            with _status_lock:
                _status_cache.update(services=services, checked_at=checked_at)
        finally:
            with _status_lock:
                _status_cache["refreshing"] = False
    overall = "operational" if all(v == "operational" for v in services.values()) else "degraded"
    return {
        "status": overall,
        "checked_at": _status_timestamp(datetime.fromtimestamp(checked_at, timezone.utc)),
        "services": dict(services),
    }


def deployment_status_snapshot():
    """Read the latest trigger build and serving Cloud Run revision for developers."""
    configured = all((CLOUD_STATUS_PROJECT_ID, CLOUD_STATUS_REGION, CLOUD_STATUS_SERVICE,
                      CLOUD_STATUS_BUILD_TRIGGER_ID, cloudbuild_v1, run_v2))
    if not configured:
        return {"status": "unavailable"}
    try:
        build_client = cloudbuild_v1.CloudBuildClient()
        builds = build_client.list_builds(
            project_id=CLOUD_STATUS_PROJECT_ID,
            filter='trigger_id="%s"' % CLOUD_STATUS_BUILD_TRIGGER_ID,
            page_size=1,
        )
        build = next(iter(builds), None)
        run_client = run_v2.ServicesClient()
        service_name = "projects/%s/locations/%s/services/%s" % (
            CLOUD_STATUS_PROJECT_ID, CLOUD_STATUS_REGION, CLOUD_STATUS_SERVICE)
        service = run_client.get_service(name=service_name)
    except Exception:
        logger.exception("Could not retrieve Cloud deployment status")
        return {"status": "unavailable"}

    # Cloud Build's protobuf enum: QUEUED=1, WORKING=2, SUCCESS=3, then its
    # terminal error states. Keep the browser vocabulary deliberately smaller.
    build_states = {1: "queued", 2: "building", 3: "live", 4: "failed", 5: "failed",
                    6: "failed", 7: "failed", 9: "failed"}
    build_status = "unavailable"
    build_data = None
    if build:
        code = int(build.status)
        build_status = build_states.get(code, "building")
        source = getattr(getattr(build, "source", None), "repo_source", None)
        substitutions = dict(getattr(build, "substitutions", {}) or {})
        revision = (getattr(source, "commit_sha", "") or substitutions.get("COMMIT_SHA") or
                    getattr(source, "branch_name", ""))
        build_data = {
            "state": build_status,
            "started_at": _status_timestamp(getattr(build, "start_time", None)),
            "finished_at": _status_timestamp(getattr(build, "finish_time", None)),
            "revision": revision[:80] if revision else None,
            "console_url": "https://console.cloud.google.com/cloud-build/builds/%s?project=%s" %
                           (build.id, CLOUD_STATUS_PROJECT_ID),
        }
    traffic = list(getattr(service, "traffic", []) or [])
    serving = next((t for t in traffic if getattr(t, "percent", 0) > 0), None)
    revision = getattr(serving, "revision", "") or getattr(service, "latest_ready_revision", "")
    ready = bool(getattr(service, "latest_ready_revision", ""))
    rollout_state = "live" if ready else "deploying"
    status = "failed" if build_status == "failed" else ("deploying" if build_status in ("queued", "building") or not ready else "live")
    return {
        "status": status,
        "build": build_data,
        "service": {
            "state": rollout_state,
            "revision": revision or None,
            "console_url": "https://console.cloud.google.com/run/detail/%s/%s/revisions?project=%s" %
                           (CLOUD_STATUS_REGION, CLOUD_STATUS_SERVICE, CLOUD_STATUS_PROJECT_ID),
        },
    }


# Tutor personalities a student can pick. Style only: the directive is appended
# below the course rules and can never loosen them. `dev` ones are refused for
# everyone else when preferences are saved.
PERSONALITIES = {
    "default": {"label": "Default"},
    "encouraging": {"label": "Encouraging"},
    "concise": {"label": "Concise"},
    "socratic": {"label": "Socratic"},
    "casual": {"label": "Casual"},
    "vibetastic": {"label": "Vibetastic", "dev": True},
}
PREFERENCE_DEFAULTS = {
    "personality": "default",
    "length": "balanced",       # short | balanced | detailed
    "reading_level": "auto",    # auto | simple | standard | advanced
    "language": "",             # free text, e.g. "Spanish"; empty = student's language
    "explain_simply": False,
    "style_note": "",
}
PREFERENCE_CHOICES = {
    "length": ("short", "balanced", "detailed"),
    "reading_level": ("auto", "simple", "standard", "advanced"),
}
STYLE_NOTE_MAX = 300
LANGUAGE_MAX = 40


def normalize_preferences(raw, dev=False):
    """Return a compact, known-safe preferences object (unknown keys dropped)."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(PREFERENCE_DEFAULTS)
    p = PERSONALITIES.get(raw.get("personality"))
    if p and (dev or not p.get("dev")):
        out["personality"] = raw["personality"]
    for key, allowed in PREFERENCE_CHOICES.items():
        if raw.get(key) in allowed:
            out[key] = raw[key]
    if isinstance(raw.get("explain_simply"), bool):
        out["explain_simply"] = raw["explain_simply"]
    for key, cap in (("language", LANGUAGE_MAX), ("style_note", STYLE_NOTE_MAX)):
        if isinstance(raw.get(key), str):
            out[key] = " ".join(raw[key].split())[:cap]
    return out


def preference_directive(prefs, dev=False):
    """The bounded 'style only' prompt section for a student's preferences, or ''."""
    prefs = normalize_preferences(prefs, dev)
    lines = []
    personality = "preferences/personalities/" + prefs["personality"]
    if prompts.has(personality):
        lines.append(prompts.get(personality))
    for name in ("length_" + prefs["length"], "reading_level_" + prefs["reading_level"]):
        if prompts.has("preferences/" + name):
            lines.append(prompts.get("preferences/" + name))
    if prefs["explain_simply"]:
        lines.append(prompts.get("preferences/explain_simply"))
    if prefs["style_note"]:
        # Untrusted student text: quoted, capped, and explicitly demoted.
        lines.append(prompts.fill("preferences/style_note", note=prefs["style_note"].replace('"', "'")))
    return "\n".join("- " + l for l in lines)


# Course "manifest": a short, model-written map of what each uploaded document
# covers. It lets the tutor know what the knowledge base contains without
# retrieval, and is a map only, never a source of facts.
MANIFEST_SAMPLE_CHUNKS = 8
MANIFEST_OUTLINE_CHARS = 1800
MANIFEST_TTL = 30
_manifest_cache = {}
_manifest_lock = Lock()
_manifest_building = set()

# Caps for what /rules hands the teacher's material page. Typed rules and file
# chunks share a namespace, so they're fetched as two separate filtered queries:
# one capped query for both let a few hundred chunks from a single PDF crowd every
# typed rule out of the result, and the page would show a class with no rules.
# Chunks get the larger cap because their count per file is displayed, and they
# come back without their text, which keeps that response small.
RULES_TOP_K = int(os.getenv("RULES_TOP_K", "100"))
DOC_CHUNK_TOP_K = int(os.getenv("DOC_CHUNK_TOP_K", "600"))
DELETE_IDS_MAX = 500        # Pinecone accepts up to 1000 ids per delete call

# Directory this file lives in — used to serve the front-end HTML pages.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Everything the app serves as a static file lives under web/. The URL paths
# below are unchanged by that — only where they are read from on disk — so
# the relative links inside the pages keep working untouched.
WEB_DIR = os.path.join(BASE_DIR, 'web')

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
if TRUST_PROXY_HOPS > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUST_PROXY_HOPS, x_proto=TRUST_PROXY_HOPS)
CORS(app, origins=ALLOWED_ORIGINS)

# Shout if a deployment is still running the dev CORS defaults — that would mean
# the real front-end origin can't call the API (and that nobody set the var).
if not DEBUG and all("127.0.0.1" in o or "localhost" in o for o in ALLOWED_ORIGINS):
    logger.warning(
        "ALLOWED_ORIGINS is still the local-dev default (%s). Set it to your "
        "deployed front-end origin(s).", ",".join(ALLOWED_ORIGINS)
    )

# The teacher-code throttle keys on request.remote_addr. Behind a proxy with
# TRUST_PROXY_HOPS unset, that's the load balancer for *every* caller, so the
# per-IP budget collapses into one global budget: it stops throttling the
# attacker and starts locking legitimate teachers out instead.
if not DEBUG and TRUST_PROXY_HOPS == 0:
    logger.warning(
        "TRUST_PROXY_HOPS is 0. If this is deployed behind a proxy (Cloud Run "
        "puts exactly one in front of you), set it to 1 — otherwise every "
        "request looks like it comes from the load balancer and the teacher "
        "signup-code rate limit applies globally instead of per client."
    )


@app.errorhandler(413)
def too_large(_e):
    """MAX_CONTENT_LENGTH rejects the body before any handler runs; without this
    Flask answers with an HTML error page, which the JSON front-end can't read."""
    return jsonify({"error": f"That file is too large. The limit is {MAX_UPLOAD_MB} MB."}), 413


@app.after_request
def security_headers(resp):
    """Baseline hardening headers on every response.

    No CSP here on purpose: the pages pull Tailwind/Firebase from CDNs and run
    inline <script> blocks, so any policy strict enough to be worth having would
    break them. Adding one means moving that JS into files first.
    """
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")          # clickjacking
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # Cloud Run terminates TLS for you, but nothing stops a browser trying the
    # first request to a custom domain over http — where a Firebase ID token is
    # readable in transit. Only sent when the request already arrived over https,
    # so local http development is unaffected.
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


pc = Pinecone(api_key=PINE_KEY)


class _LazyIndex:
    """Defer Pinecone's index-host lookup to the first real call.

    pc.Index(name) resolves the index host over the network, so importing this
    module needed live Pinecone connectivity and a valid key. That is why the
    three test files here — which stub every collaborator and touch nothing —
    could never actually be run. Attribute access is proxied, so monkeypatching
    `app.pinecone_index` still works exactly as before.
    """

    def __init__(self, name):
        self._name = name
        self._index = None

    def __getattr__(self, attr):
        if self._index is None:
            self._index = pc.Index(self._name)
        return getattr(self._index, attr)


pinecone_index = _LazyIndex(INDEX_NAME)

try:
    firebase_admin.get_app()
except ValueError:
    # In deployment, store the whole service-account JSON in FIREBASE_CREDENTIALS_JSON
    # (hosts inject env vars, not secret files). Fall back to the local file for dev.
    fb_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
    if fb_json:
        cred = credentials.Certificate(json.loads(fb_json))
    else:
        cred = credentials.Certificate("firebase_credentials.json")
    firebase_admin.initialize_app(cred)
db = firestore.client()


# ---------- helpers ----------

def chunk_text(text, size=900, overlap=150):
    text = " ".join(text.split())
    if len(text) <= size:
        return [text] if text else []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            for sep in ('. ', '! ', '? ', ' '):
                idx = text.rfind(sep, start + size // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        # Stop once this chunk reaches the end. Without this the loop never
        # terminates: start = end - overlap can never reach n, so for any text
        # longer than `size` it would spin on the final segment forever, appending
        # the same tail until the process runs out of memory (the upload OOM).
        if end >= n:
            break
        start = end - overlap
    return chunks


def embed(text):
    """Return a 768-dim search-query embedding for the given text.

    Raises llm.EmbeddingsUnavailable when the embedding server is down; callers
    turn that into a clear 503 rather than answering without retrieval."""
    return llm.embed(text)


RETRIEVAL_DOWN = ("Course search is temporarily unavailable. Please try again in a few minutes.")


def retrieval_unavailable(exc):
    logger.error("Retrieval unavailable: %s", exc)
    return jsonify({"error": RETRIEVAL_DOWN, "code": "retrieval_unavailable"}), 503


def check_embedding_index():
    """Startup guard: the embedding server must return EMBED_DIM floats, and an index
    only ever holds vectors from the one model recorded for it. A dimension mismatch or
    a different model raises; an offline server only logs, so chat keeps working."""
    if not llm.API_KEY:
        return
    try:
        model, dim = llm.verify_embeddings()
    except llm.EmbeddingsUnavailable as e:
        if "mismatch" in str(e):
            raise RuntimeError(str(e)) from e
        logger.critical("Embedding server unreachable at startup; retrieval is down until it returns.")
        return
    try:
        ref = db.collection("Meta").document("embedding")
        stored = ref.get()
        if stored.exists:
            rec = stored.to_dict() or {}
            if rec.get("model") != model or rec.get("dimension") != dim or rec.get("index") != INDEX_NAME:
                raise RuntimeError(
                    "Index %s holds %s vectors (%s-d) but the embedding server now serves %s (%s-d). "
                    "Use a new index, or re-embed everything, before switching." % (
                        INDEX_NAME, rec.get("model"), rec.get("dimension"), model, dim))
        else:
            ref.set({"model": model, "dimension": dim, "index": INDEX_NAME,
                     "recorded_at": firestore.SERVER_TIMESTAMP})
    except RuntimeError:
        raise
    except Exception:
        logger.exception("Could not read or record the embedding model for %s.", INDEX_NAME)


check_embedding_index()


def valid_doc_id(doc_id):
    """True if `doc_id` is usable as a single Firestore document id.

    Client-supplied ids land in `.document(id)`, where a '/' would silently turn
    one id into a multi-segment path (or raise). Everything we generate is a
    plain Firestore auto-id, so rejecting anything else costs nothing.
    """
    return (
        isinstance(doc_id, str)
        and 0 < len(doc_id) <= 128
        and "/" not in doc_id
        and doc_id not in (".", "..")
        and not doc_id.startswith("__")
    )


def _bearer_token():
    """Pull the Firebase ID token out of the Authorization: Bearer <token> header."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def verify_user():
    """Verify the request's Firebase ID token and return its decoded claims, or
    None if missing/invalid/expired. Never raises."""
    token = _bearer_token()
    if not token:
        return None
    try:
        return fb_auth.verify_id_token(token)
    except Exception:
        logger.info("Rejected an invalid/expired Firebase ID token.")
        return None


def get_role(uid):
    """Return the stored role ('teacher'/'student') for a user, or None.

    Cached in-process for ROLE_CACHE_TTL seconds. Every authenticated route
    needs the role, so without this each request pays a Firestore read purely to
    re-learn something that changes at most once per account.
    """
    now = time.time()
    with _role_lock:
        hit = _role_cache.get(uid)
        if hit and hit[1] > now:
            return hit[0]

    role = None
    try:
        doc = db.collection("Users").document(uid).get()
        if doc.exists:
            role = (doc.to_dict() or {}).get("role")
    except Exception:
        logger.exception("Could not read user role for %s", uid)
        return None  # don't cache a failed lookup

    with _role_lock:
        if len(_role_cache) >= ROLE_CACHE_MAX:
            _role_cache.clear()  # crude but bounded; the cache refills in seconds
        _role_cache[uid] = (role, now + ROLE_CACHE_TTL)
    return role


def invalidate_role(uid):
    """Drop a cached role so a just-written role takes effect immediately."""
    with _role_lock:
        _role_cache.pop(uid, None)


def require_auth(fn=None, *, allow_roleless=False):
    """Gate: any signed-in Firebase user who has finished registering. Stashes the
    decoded token + uid on `request` so the handler can use them.

    The role check is part of the gate, not a nicety. Signing in and *registering*
    are two separate steps: the browser creates the Firebase account itself
    (anyone can, straight against the public web API key) and only then calls
    /auth/register to be assigned a role. So a token with no role behind it is a
    half-created account — and without this check it fell through every student
    path as a de-facto student, able to join a class and use the tutor.

    /auth/register is the one route that legitimately runs before a role exists;
    it opts out with allow_roleless=True.
    """
    def decorate(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            decoded = verify_user()
            if not decoded:
                return jsonify({"error": "Unauthorized. Please sign in."}), 401
            if not allow_roleless and not get_role(decoded["uid"]):
                return jsonify({"error": "Finish creating your account first."}), 403
            request.user = decoded
            request.uid = decoded["uid"]
            return f(*args, **kwargs)
        return wrapper
    # Usable bare (@require_auth) or called (@require_auth(allow_roleless=True)).
    return decorate(fn) if fn else decorate


def require_teacher(fn):
    """Gate: a signed-in user whose stored role is 'teacher'."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        decoded = verify_user()
        if not decoded:
            return jsonify({"error": "Unauthorized. Please sign in."}), 401
        if get_role(decoded["uid"]) != "teacher":
            return jsonify({"error": "Forbidden. Teacher access only."}), 403
        request.user = decoded
        request.uid = decoded["uid"]
        return fn(*args, **kwargs)
    return wrapper


def server_error(msg, exc, status=500):
    """Log the real error server-side, return a generic message to the client.
    Internal details (stack/keys/paths) are only exposed when DEBUG is on."""
    logger.exception(msg)
    payload = {"error": msg}
    if DEBUG:
        payload["details"] = str(exc)
    return jsonify(payload), status


# ---------- classes ----------
# Each class is an isolated knowledge base: its rules live in a Pinecone
# *namespace* equal to the class id, so a query/upsert/delete only ever touches
# that one class. Legacy (pre-classes) rules sit in the default namespace ("").

def gen_join_code():
    """A short, human-friendly class code, guaranteed unique. Avoids easily
    confused characters (0/O, 1/I).

    Drawn from `secrets`, not `random`: the join code is the only thing standing
    between a stranger and a class's material, and `random`'s Mersenne Twister
    is reconstructable from a handful of observed outputs — a teacher could
    predict every other teacher's codes from their own.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        hit = list(db.collection("Classes").where("join_code", "==", code).limit(1).stream())
        if not hit:
            return code
    # Extremely unlikely fallback — widen rather than risk a collision.
    return "".join(secrets.choice(alphabet) for _ in range(10))


def class_to_dict(doc, include_code=False):
    d = doc.to_dict() or {}
    out = {"id": doc.id, "name": d.get("name") or "Untitled course"}
    # The student page needs this before the first answer comes back, or a
    # returning student sees no way to ask for practice until they type something.
    out["toolkits"] = enabled_tool_types(course_settings(d.get("settings")))
    if include_code:
        out["join_code"] = d.get("join_code")
    return out


def course_settings(raw=None):
    """Return a compact, known-safe course settings object."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(COURSE_SETTINGS_DEFAULTS)
    for key in COURSE_BOOL_SETTINGS:
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    if raw.get("hint_strength") in ("light", "progressive", "strong"):
        out["hint_strength"] = raw["hint_strength"]
    extra = raw.get("additional_instructions")
    if isinstance(extra, str):
        out["additional_instructions"] = extra.strip()[:1500]
    return out


def load_course_settings(class_id):
    if not valid_doc_id(class_id):
        return course_settings()
    snap = db.collection("Classes").document(class_id).get()
    return course_settings((snap.to_dict() or {}).get("settings") if snap.exists else None)


def load_course_name(class_id):
    """The course's display name for the prompt. Never raises: no name just means a generic prompt."""
    try:
        if not valid_doc_id(class_id):
            return ""
        snap = db.collection("Classes").document(class_id).get()
        return str((snap.to_dict() or {}).get("name") or "") if snap.exists else ""
    except Exception:
        logger.exception("Could not read the course name.")
        return ""


def load_custom_rules(class_id):
    """Always-on teacher rules live on the course document, never in Pinecone."""
    if not valid_doc_id(class_id):
        return []
    snap = db.collection("Classes").document(class_id).get()
    raw = (snap.to_dict() or {}).get("custom_rules", []) if snap.exists else []
    return [
        {"id": str(r.get("id") or "")[:128], "text": str(r.get("text") or "").strip()[:1500]}
        for r in raw if isinstance(r, dict) and str(r.get("text") or "").strip()
    ]


_prefs_cache = {}


def load_user_preferences(uid, dev=False):
    """A user's saved tutor preferences, normalized. Never raises: a failed read
    just means the tutor answers in its default voice."""
    now = time.time()
    with _manifest_lock:
        hit = _prefs_cache.get(uid)
    if hit and hit[1] > now:
        raw = hit[0]
    else:
        try:
            snap = db.collection("Users").document(uid).get()
            raw = (snap.to_dict() or {}).get("preferences") if snap.exists else None
        except Exception:
            logger.exception("Could not read preferences for %s", uid)
            return normalize_preferences(None, dev)
        with _manifest_lock:
            if len(_prefs_cache) > 2000:
                _prefs_cache.clear()
            _prefs_cache[uid] = (raw, now + MANIFEST_TTL)
    return normalize_preferences(raw, dev)


def _clean_manifest_entry(raw, fallback_title=""):
    raw = raw if isinstance(raw, dict) else {}
    topics = [" ".join(str(t).split())[:60] for t in (raw.get("topics") or []) if str(t).strip()]
    return {
        "title": " ".join(str(raw.get("title") or fallback_title).split())[:200],
        "summary": " ".join(str(raw.get("summary") or "").split())[:280],
        "topics": topics[:8],
        "chunks": int(raw.get("chunks") or 0),
    }


MANIFEST_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"},
                   "topics": {"type": "array", "items": {"type": "string"}}},
    "required": ["summary", "topics"],
}


def summarize_document(source, chunks):
    """Ask the model for a one-line summary and topic list for one document.

    Failure costs the outline some detail, never the upload: it falls back to the
    file name alone.
    """
    entry = {"title": source, "summary": "", "topics": [], "chunks": len(chunks)}
    try:
        sample = "\n\n".join(chunks[:MANIFEST_SAMPLE_CHUNKS])[:7000]
        prompt = prompts.fill("background/catalog_document", source=source, excerpt=sample)
        text = llm.generate(TOOL_MODEL, prompt, temperature=0.1, json_schema=MANIFEST_SCHEMA, think=False).text
        parsed = json.loads(text[text.find("{"):text.rfind("}") + 1])
        entry.update(_clean_manifest_entry(parsed, source))
        entry["title"] = source
        entry["chunks"] = len(chunks)
    except Exception:
        logger.exception("Could not summarize %s for the course outline.", source)
    return entry


def save_manifest_entry(class_id, base_id, entry):
    entry = dict(entry, added=firestore.SERVER_TIMESTAMP)
    db.collection("Classes").document(class_id).set({"manifest": {"docs": {base_id: entry}}}, merge=True)
    with _manifest_lock:
        _manifest_cache.pop(class_id, None)


def remove_manifest_docs(class_id, vector_ids):
    """Drop outline entries for documents whose chunks were just deleted."""
    bases = {str(v).rsplit("_", 1)[0] for v in vector_ids if str(v).startswith("file_")}
    if not bases:
        return
    try:
        db.collection("Classes").document(class_id).update(
            {"manifest.docs.%s" % b: firestore.DELETE_FIELD for b in bases})
    except Exception:
        logger.exception("Could not update the course outline after a delete.")
    with _manifest_lock:
        _manifest_cache.pop(class_id, None)


def load_course_manifest(class_id):
    """The outline entries for a course, oldest first. Never raises."""
    if not valid_doc_id(class_id):
        return []
    now = time.time()
    with _manifest_lock:
        hit = _manifest_cache.get(class_id)
    if hit and hit[1] > now:
        return hit[0]
    try:
        snap = db.collection("Classes").document(class_id).get()
        raw = ((snap.to_dict() or {}).get("manifest") or {}).get("docs") if snap.exists else None
    except Exception:
        logger.exception("Could not read the course outline for %s", class_id)
        return []
    docs = []
    for base_id, entry in (raw or {}).items():
        if isinstance(entry, dict):
            clean = _clean_manifest_entry(entry, "Document")
            clean["id"] = str(base_id)
            clean["added"] = _ts_seconds(entry.get("added"))
            docs.append(clean)
    docs.sort(key=lambda d: d["added"])
    with _manifest_lock:
        if len(_manifest_cache) > 500:
            _manifest_cache.clear()
        _manifest_cache[class_id] = (docs, now + MANIFEST_TTL)
    return docs


def manifest_topics(docs, limit=40):
    seen, out = set(), []
    for d in docs:
        for t in d.get("topics") or []:
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out[:limit]


def manifest_outline(docs):
    """Bounded prompt section listing what the course material covers, or ''."""
    if not docs:
        return ""
    lines = []
    topics = manifest_topics(docs)
    if topics:
        lines.append("Topics: " + ", ".join(topics))
    lines.append("Documents:")
    used = sum(len(l) for l in lines)
    for i, d in enumerate(docs):
        line = "- " + d["title"] + (": " + d["summary"] if d["summary"] else "")
        if used + len(line) > MANIFEST_OUTLINE_CHARS:
            lines.append("- (+%d more documents)" % (len(docs) - i))
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def rebuild_manifest(class_id):
    """Backfill the outline for a course uploaded before outlines existed."""
    try:
        count = class_vector_count(class_id)
        if not count:
            return
        resp = pinecone_index.query(
            vector=[0.0] * EMBED_DIM, top_k=min(count, 1000), include_metadata=True,
            namespace=class_id, filter={"source": {"$exists": True}})
        groups = {}
        for m in resp["matches"]:
            meta = m.get("metadata") or {}
            base = str(m["id"]).rsplit("_", 1)[0]
            groups.setdefault(base, {"source": meta.get("source") or "Document", "chunks": []})
            groups[base]["chunks"].append((meta.get("chunk") or 0, str(meta.get("text") or "")))
        for base, g in groups.items():
            ordered = [t for _, t in sorted(g["chunks"], key=lambda c: c[0])]
            save_manifest_entry(class_id, base, summarize_document(g["source"], ordered))
    except Exception:
        logger.exception("Could not rebuild the course outline for %s", class_id)
    finally:
        _manifest_building.discard(class_id)


def mutate_custom_rules(class_id, change):
    """Apply `change` to a course's custom rules inside a transaction.

    `change` takes the current rules as an {id: rule} dict and returns the dict
    to store. All three callers read the whole array, adjust it and write it back,
    and the field is one array on one document — so without this, two teacher
    tabs saving at once silently drop one side's edit. `change` must be pure and
    idempotent: a contended transaction runs it again.
    """
    if not valid_doc_id(class_id):
        return []
    ref = db.collection("Classes").document(class_id)

    @firestore.transactional
    def apply(transaction):
        snap = ref.get(transaction=transaction)
        raw = (snap.to_dict() or {}).get("custom_rules", []) if snap.exists else []
        current = {}
        for r in raw:
            if isinstance(r, dict) and str(r.get("text") or "").strip():
                rid = str(r.get("id") or "")[:128]
                current[rid] = {"id": rid, "text": str(r.get("text") or "").strip()[:1500]}
        cleaned = [
            {"id": str(r.get("id") or secrets.token_urlsafe(9))[:128],
             "text": str(r.get("text") or "").strip()[:1500]}
            for r in change(current).values()
            if isinstance(r, dict) and str(r.get("text") or "").strip()
        ]
        transaction.set(ref, {"custom_rules": cleaned}, merge=True)
        return cleaned

    return apply(db.transaction())


def get_user_classes(uid, role):
    """Classes the user can act in. Teachers see the classes they own (with join
    codes); students see the classes they've joined."""
    if role == "teacher":
        docs = db.collection("Classes").where("teacher_uid", "==", uid).stream()
        return [class_to_dict(d, include_code=True) for d in docs]
    # student: ids stored on the user doc
    user = db.collection("Users").document(uid).get()
    ids = (user.to_dict() or {}).get("class_ids", []) if user.exists else []
    ids = [cid for cid in ids if valid_doc_id(cid)]
    if not ids:
        return []
    # One multi-get instead of a round-trip per class. get_all doesn't promise
    # input order, so sort back to the order the student joined in.
    order = {cid: i for i, cid in enumerate(ids)}
    docs = db.get_all([db.collection("Classes").document(cid) for cid in ids])
    found = [d for d in docs if d.exists]
    found.sort(key=lambda d: order.get(d.id, 0))
    return [class_to_dict(d) for d in found]


def class_owned_by(class_id, uid):
    """True if `uid` is the teacher who owns `class_id`."""
    if not valid_doc_id(class_id):
        return False
    doc = db.collection("Classes").document(class_id).get()
    return doc.exists and (doc.to_dict() or {}).get("teacher_uid") == uid


def class_vector_count(class_id):
    """How many rule vectors a class currently has (its Pinecone namespace)."""
    try:
        ns = pinecone_index.describe_index_stats().namespaces.get(class_id)
        return getattr(ns, "vector_count", 0) if ns else 0
    except Exception:
        return 0


def user_in_class(uid, class_id, role):
    """Authorization for class-scoped operations: a teacher must own the class,
    a student must be an enrolled member."""
    if not valid_doc_id(class_id):
        return False
    if role == "teacher":
        return class_owned_by(class_id, uid)
    member = db.collection("Classes").document(class_id).collection("Members").document(uid).get()
    return member.exists


def migrate_default_rules_to(class_id):
    """One-time move of legacy global rules (Pinecone default namespace) into the
    given class namespace, so nothing is lost when classes are introduced.
    Guarded by a Firestore flag so it only ever runs once."""
    flag_ref = db.collection("Meta").document("migration")
    flag = flag_ref.get()
    if flag.exists and (flag.to_dict() or {}).get("legacy_rules_moved"):
        return 0
    try:
        stats = pinecone_index.describe_index_stats()
        default_ns = stats.namespaces.get("")
        count = getattr(default_ns, "vector_count", 0) if default_ns else 0
        moved = 0
        if count:
            zero = [0.0] * EMBED_DIM
            resp = pinecone_index.query(
                vector=zero, top_k=min(count, 1000),
                include_metadata=True, include_values=True, namespace="",
            )
            vectors = [
                {"id": m["id"], "values": m["values"], "metadata": m.get("metadata", {})}
                for m in resp["matches"]
            ]
            if vectors:
                pinecone_index.upsert(vectors=vectors, namespace=class_id)
                pinecone_index.delete(ids=[v["id"] for v in vectors], namespace="")
                moved = len(vectors)
    except Exception:
        logger.exception("Legacy rule migration failed; continuing without it.")
        moved = 0
    flag_ref.set({"legacy_rules_moved": True, "moved_at": firestore.SERVER_TIMESTAMP}, merge=True)
    return moved


def load_history(chat_ref, limit=HISTORY_TURNS):
    """Return the most recent stored messages as model 'contents' turns
    (oldest first) so the model can see the conversation so far.

    Roles map student->'user', teacher->'model'. Any leading model turns are
    dropped because chat models expect the conversation to start with a user turn.
    """
    try:
        docs = list(
            chat_ref.order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
    except Exception:
        # Never let a history read failure break the actual answer.
        logger.exception("Could not load chat history; answering without it.")
        return []

    docs.reverse()  # back into chronological order
    contents = []
    for d in docs:
        m = d.to_dict()
        text = m.get("content")
        if not text:
            continue
        # A blocked exchange stays in the student's history but never returns to
        # the model. Otherwise refusing to read the abuse once only delays it by
        # a turn: the next question replays the whole conversation, swear included.
        if m.get("blocked"):
            continue
        role = "user" if m.get("role") == "student" else "model"
        if not contents and role == "model":
            continue  # skip any leading model turn
        contents.append({"role": role, "parts": [{"text": text}]})
    return trim_history(contents)


def trim_history(contents, limit=HISTORY_CHARS):
    """Drop the oldest turns until the replay fits its share of the context window."""
    contents = list(contents)
    size = sum(len(c["parts"][0]["text"]) for c in contents)
    while contents and (size > limit or contents[0]["role"] != "user"):
        size -= len(contents.pop(0)["parts"][0]["text"])
    return contents


# ---------- what the tutor remembers, and what the student hands it ----------
# Both blocks below are scoped to a single class, and every query feeding them
# filters on class_id. Nothing has to be cleared when a student switches class,
# because nothing was ever fetched from the other one.

def build_memory_block(chats, exclude_chat_id=None, limit=MEMORY_CHATS):
    """Render what the tutor recalls about a student, from one class's chats.

    `chats` are that student's chat documents (as dicts) for a single class. This
    reuses the summary /chat already denormalizes onto every chat doc (see
    summarize_exchange), so recollection costs one bounded query and no extra
    model call — nothing here re-reads anybody's messages.

    Pure, so the wording and the ordering are testable without Firestore.
    """
    rows = [c for c in chats if c.get("id") != exclude_chat_id]
    rows.sort(key=lambda c: c.get("last_active") or 0, reverse=True)
    rows = rows[:limit]
    if not rows:
        return ""

    topics, gaps = [], []
    for c in rows:
        # `title` is the fallback for chats predating summaries: it was cut from
        # the opening question, so it is the same thing, just shorter.
        opening = (c.get("opening") or c.get("title") or "").strip()[:MEMORY_TOPIC_CHARS]
        if opening and opening not in topics:
            topics.append(opening)
        for g in (c.get("learning_gaps") or []):
            g = (g or "").strip()[:MEMORY_TOPIC_CHARS]
            if g and g not in gaps:
                gaps.append(g)

    if not topics and not gaps:
        return ""

    lines = []
    if topics:
        lines.append("- They have asked about: " + "; ".join(topics))
    if gaps:
        lines.append("- They showed uncertainty or confusion about: "
                     + "; ".join(gaps[:MEMORY_MAX_GAPS]))
    lines.append("- Earlier conversations in this course: %d" % len(rows))
    return "\n".join(lines)


def load_class_memory(uid, class_id, exclude_chat_id=None, profile=None):
    """Fetch this student's chats *for one class* and render the memory block.

    `profile` is that student's running profile for the same class (see
    load_class_profile). It is passed in rather than fetched here so /chat reads
    it once and can also use it when updating the counters.
    """
    if not class_id:
        return ""
    try:
        docs = list(
            _user_chats(uid).where("class_id", "==", class_id)
            .limit(MEMORY_QUERY_MAX).stream()
        )
    except Exception:
        # Recollection is a nicety. Never let it cost the student an answer.
        logger.exception("Could not load class memory; answering without it.")
        return ""
    chats = [dict(d.to_dict() or {}, id=d.id) for d in docs]
    for c in chats:
        c["last_active"] = _ts_seconds(c.get("last_active"))
    block = build_memory_block(chats, exclude_chat_id=exclude_chat_id)
    note = student_profile.describe(profile)
    return "\n\n".join(p for p in (block, note) if p)


def load_class_profile(uid, class_id):
    """This student's running profile for one class, or {} if there isn't one."""
    if not PROFILE_ENABLED or not class_id:
        return {}
    try:
        snap = _user_profile(uid, class_id).get()
    except Exception:
        # Same posture as the memory query above: knowing how a student writes is
        # a nicety, and it never costs them an answer.
        logger.exception("Could not load student profile; answering without it.")
        return {}
    prof = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
    stored = prof.get("class_id")
    if prof and stored and stored != class_id:
        # Unreachable by construction — the class id *is* the document id. It is
        # asserted anyway because this is the one bug in this feature that would
        # be silent: a profile from another class would read as perfectly normal
        # prose in the prompt.
        logger.error("Profile %s/%s carries class_id %r; refusing to use it.",
                     uid, class_id, stored)
        return {}
    return prof


def profile_update(class_id, question, summary, prev_profile):
    """Firestore increments describing what this one message adds to the profile.

    Increments rather than a read-modify-write, so two messages sent in quick
    succession can't lose each other's counts.
    """
    delta = student_profile.measure(question)
    if not delta:
        return {}
    update = {k: firestore.Increment(v) for k, v in delta.items()}
    # Written every time so the tripwire in load_class_profile has something to
    # check, and so a profile carries its own provenance.
    update["class_id"] = class_id
    update["updated_at"] = firestore.SERVER_TIMESTAMP

    # Only the newest entry belongs to this exchange; the rest were counted when
    # they were asked.
    for gap in (summary.get("learning_gaps") or [])[-1:]:
        key = student_profile.gap_key(gap)
        counts = prev_profile.get("gap_counts") or {}
        if key and (key in counts or len(counts) < student_profile.PROFILE_MAX_GAPS):
            update["gap_counts." + key] = firestore.Increment(1)
    return update


def build_docs_block(docs):
    """Render the student's own uploaded work for review.

    Each document goes in whole and unchunked: this is the thing being marked,
    not a corpus to search, and retrieving five fragments of an essay to judge
    the whole of it gives feedback on paragraphs nobody asked about. Rubrics come
    first, so the model reads the criteria before the work it applies them to.
    """
    ordered = ([d for d in docs if d.get("kind") == "rubric"]
               + [d for d in docs if d.get("kind") != "rubric"])
    items = [[d, (d.get("text") or "").strip(), False] for d in ordered]
    items = [i for i in items if i[1]]
    # The context window is small: when the documents together exceed their share,
    # the longest ones are replaced by the short summary stored at upload.
    while sum(len(i[1]) for i in items) > STUDENT_CONTEXT_CHARS:
        swappable = [i for i in items if not i[2] and (i[0].get("summary") or "").strip()]
        if not swappable:
            break
        pick = max(swappable, key=lambda i: len(i[1]))
        pick[1], pick[2] = pick[0]["summary"].strip(), True
    parts, used = [], 0
    for d, text, summarized in items:
        room = STUDENT_CONTEXT_CHARS - used
        if room <= 0:
            break
        text = text[:room]
        used += len(text)
        kind = d.get("kind") if d.get("kind") in STUDENT_DOC_KINDS else "file"
        parts.append("--- %s: %s%s ---\n%s" % (kind, d.get("name") or "untitled",
                                               " (summary; the document is longer)" if summarized else "", text))
    if not parts:
        return ""
    return "\n\n".join(parts)


def load_student_docs(uid, class_id, chat_id):
    """This student's uploads for one conversation, text included."""
    if not class_id or not chat_id:
        return []
    try:
        docs = list(
            _user_files(uid).where("class_id", "==", class_id)
            .where("chat_id", "==", chat_id).limit(STUDENT_DOCS_MAX).stream()
        )
    except Exception:
        # Two equality filters need a composite index on the user's Files
        # subcollection. Without one this raises on every message and the student
        # silently gets feedback on an essay the tutor never saw, so say plainly
        # what to go and create.
        logger.exception(
            "Could not load student uploads for class=%s chat=%s; answering without "
            "them. If this is FAILED_PRECONDITION, Firestore needs a composite index "
            "on Users/{uid}/Files over (class_id, chat_id) — the error carries a "
            "link that creates it.", class_id, chat_id,
        )
        return []
    return [d.to_dict() or {} for d in docs]


def summarize_tutoring_state(existing, transcript):
    """Generate a bounded, non-authoritative tutoring-state note."""
    prompt = prompts.fill("background/summarize_tutoring_state", existing=existing or "(none)", transcript=transcript)
    return llm.generate(CHAT_MODEL, prompt, think=False).text[:CONVERSATION_SUMMARY_CHARS]


def summarize_student_doc(text):
    """A short stored stand-in for a student document, made once at upload."""
    prompt = prompts.fill("background/summarize_student_doc", text=str(text or "")[:STUDENT_DOC_CHARS])
    try:
        return llm.generate(CHAT_MODEL, prompt, temperature=0.2, max_tokens=1200, think=False).text[:STUDENT_DOC_SUMMARY_CHARS]
    except Exception:
        logger.exception("Could not summarize a student upload; storing it without a summary.")
        return ""


def classify_student_upload(text):
    """Say whether an upload reads as one student's work, a rubric, or material.

    The kind a student picks in the UI is a label, not a fact: the tutor is not a
    research assistant over whatever anyone uploads, and a student attaching a
    textbook chapter as an "assignment" is adding course material by the back
    door. Returns "assignment", "rubric", "material", or None when the check
    could not run.
    """
    sample = str(text or "").strip()[:STUDENT_DOC_CHECK_CHARS]
    if not sample:
        return None
    prompt = prompts.fill("background/classify_upload", document=sample)
    try:
        text = llm.generate(CHAT_MODEL, prompt, temperature=0, think=False).text
        word = re.sub(r"[^a-z]", "", text.lower())
        return word if word in ("assignment", "rubric", "material") else None
    except Exception:
        # Optional like the tutoring-state summary: a model outage must not stop a
        # student attaching the essay they are being marked on.
        logger.exception("Could not classify a student upload; accepting it.")
        return None


def refresh_conversation_summary(msgs_ref, prev):
    """Summarize the next batch of turns that is about to leave model history.

    The cursor is a message count rather than Firestore pagination state. At each
    batch boundary, the oldest slice immediately outside the replay window is the
    next unsummarized slice. A failed summary is optional and never blocks chat.
    """
    message_count = int(prev.get("message_count") or 0)
    summarized = int(prev.get("summary_through") or 0)
    available = max(0, message_count - HISTORY_TURNS)
    if available < summarized + SUMMARY_BATCH_MESSAGES:
        return prev.get("conversation_summary") or "", {}
    try:
        docs = list(
            msgs_ref.order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(HISTORY_TURNS + SUMMARY_BATCH_MESSAGES).stream()
        )
        docs.reverse()
        older = docs[:SUMMARY_BATCH_MESSAGES]
        transcript = "\n".join(
            ("Student" if (d.to_dict() or {}).get("role") == "student" else "Tutor")
            + ": " + str((d.to_dict() or {}).get("content") or "")[:1200]
            # Same reason as load_history: a blocked turn is not part of what the
            # tutor is meant to remember about this conversation.
            for d in older if not (d.to_dict() or {}).get("blocked")
        )
        if not transcript.strip():
            return prev.get("conversation_summary") or "", {}
        existing = (prev.get("conversation_summary") or "")[:CONVERSATION_SUMMARY_CHARS]
        summary = summarize_tutoring_state(existing, transcript)
        if not summary:
            return existing, {}
        through = summarized + SUMMARY_BATCH_MESSAGES
        return summary, {"conversation_summary": summary, "summary_through": through}
    except Exception:
        logger.exception("Could not refresh conversation summary; continuing without it.")
        return prev.get("conversation_summary") or "", {}


def _source_context(matches, min_score=None):
    """Build bounded, deduplicated teacher context plus safe source descriptors."""
    floor = RETRIEVAL_MIN_SCORE if min_score is None else min_score
    sources, blocks, seen, used = [], [], set(), 0
    for match in matches:
        meta = match.get("metadata") or {}
        text = " ".join(str(meta.get("text") or "").split())
        if not text or text in seen or match.get("score", 0) < floor:
            continue
        seen.add(text)
        room = RETRIEVAL_CONTEXT_CHARS - used
        if room <= 0:
            break
        excerpt = text[:room]
        used += len(excerpt)
        label = meta.get("source") or "Teacher note"
        chunk = meta.get("chunk")
        source = {"label": str(label)[:200], "excerpt": excerpt}
        if isinstance(chunk, int):
            source["section"] = chunk + 1
        sources.append(source)
        blocks.append(render_source(len(sources), source))
    return "\n\n".join(blocks), sources


def render_source(number, source):
    suffix = f", section {source['section']}" if source.get("section") else ""
    return f"[Source {number}: {source['label']}{suffix}]\n{source['excerpt']}"


# Messages that are conversation, not a question about the course: greetings,
# thanks, goodbyes, and "who are you / what can you do". They skip retrieval, and
# never count as a gap in the course material.
_SMALL_TALK_RE = re.compile(
    r"(?:(?:hi|hello|hey|howdy|yo|hiya|sup|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+(?:there|again|everyone|all|chronos|tutor))?"
    r"|(?:thanks|thank\s+you|thx|ty|cheers)(?:\s+(?:so\s+much|a\s+lot|again|chronos|tutor))?"
    r"|ok|okay|got\s+it|cool|great|nice|bye|goodbye|see\s+you|"
    r"how\s+are\s+you(?:\s+(?:today|doing))?|who\s+are\s+you|what\s+are\s+you|"
    r"what\s+can\s+you\s+do|what\s+do\s+you\s+do|how\s+does\s+this\s+work)"
    r"[.!?\s]*", re.IGNORECASE)

# Answer to a message that never reaches the model. Said plainly and once: this
# is a school, the student is told what happened and what to do instead, and the
# turn is over. It is deliberately not a lecture.
BLOCKED_REPLY = (
    "I'm not going to answer that. Keep it civil and ask me about the course — "
    "your teacher can see the messages in here."
)
# Stands in for the opening question when a conversation is started with abuse,
# so the chat still has a name in the student's sidebar without that name being
# the abuse itself.
BLOCKED_TITLE = "Flagged message"
# Shown when a student writes into a chat the tutor ended with end_conversation.
ENDED_REPLY = "This conversation has ended. Start a new chat to keep going."

# Which course setting switches each activity type on.
TOOL_SETTING_FOR_TYPE = {
    "quiz": "practice_tools",
    "flashcards": "practice_tools",
    "concept_map": "visual_tools",
    "review_sheet": "study_materials",
}
TOOL_FUNCTION_NAME = "create_practice_activity"
# Lookup-tool turns allowed per reply before the model has to answer.
MAX_TOOL_ROUNDS = 3


def _add_usage(total, new):
    """Sum token usage across the rounds of one reply."""
    if not new:
        return total
    if not total:
        return dict(new)
    return {k: (None if total.get(k) is None or new.get(k) is None else total[k] + new[k])
            for k in ("prompt", "reply", "total")}


def enabled_tool_types(settings):
    """The activity types this course has switched on, in a stable order."""
    return [t for t, key in TOOL_SETTING_FOR_TYPE.items() if settings.get(key)]


def validate_tool_request(raw, settings):
    """Normalize a tool request into {type, topic}, or None if it isn't allowed.

    One gate for both callers. The model asks for an activity through a function
    call, and the browser asks for one directly when a student taps a practice
    chip — so the type is re-checked against the course's own settings here
    rather than trusted from either side.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip().lower()
    if kind not in enabled_tool_types(settings):
        return None
    topic = str(raw.get("topic") or "").strip()[:300]
    if not topic:
        return None
    return {"type": kind, "topic": topic}


def explicit_tool_request(message, settings, history=None):
    """Keep direct practice requests reliable if the model omits its tool call."""
    patterns = (
        ("quiz", r"^(?:please\s+)?(?:quiz|test)\s+me(?:\s+(?:on|about)\s+(.+?))?[.!? ]*$|^(?:please\s+)?(?:make|create)\s+(?:me\s+)?a\s+quiz(?:\s+(?:on|about)\s+(.+?))?[.!? ]*$"),
        ("flashcards", r"^(?:please\s+)?(?:make|create)\s+(?:me\s+)?flashcards?(?:\s+(?:on|about|for)\s+(.+?))?[.!? ]*$"),
    )
    for kind, pattern in patterns:
        if kind not in enabled_tool_types(settings):
            continue
        match = re.fullmatch(pattern, message.strip(), re.IGNORECASE)
        if not match:
            continue
        topic = next((group for group in match.groups() if group), None)
        if not topic:
            for turn in reversed(history or []):
                if turn.get("role") == "user":
                    topic = (turn.get("parts") or [{}])[0].get("text")
                    if topic:
                        break
        return validate_tool_request({"type": kind, "topic": topic or "this course"}, settings)
    return None


def practice_activity_tool(settings):
    """The function declaration covering this course's enabled activities, or None.

    This replaces a hidden <chronos-tool> marker the model was asked to append to
    its prose. A lightweight chat model forgets a formatting convention like that
    often enough that "quiz me" usually produced an ordinary answer instead, and
    the regex fallback that used to paper over it only ever recognised a handful
    of phrasings for two of the four activity types. A declared function is part
    of the request contract rather than a request to remember something.

    Plain JSON Schema, so llm.py hands the same declaration to the endpoint.
    The call only names the activity; /tools/run builds it in a second, small
    request whose output is schema-constrained (see ACTIVITY_SCHEMAS).
    """
    kinds = enabled_tool_types(settings)
    if not kinds:
        return None
    return {
        "name": TOOL_FUNCTION_NAME,
        "description": prompts.get("tools/%s/description" % TOOL_FUNCTION_NAME),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": kinds,
                         "description": prompts.get("tools/%s/type" % TOOL_FUNCTION_NAME)},
                "topic": {"type": "string",
                          "description": prompts.get("tools/%s/topic" % TOOL_FUNCTION_NAME)},
            },
            "required": ["type", "topic"],
        },
    }


def activity_from_call(call, settings):
    """A validated {type, topic} from an llm.py tool call, or None."""
    if not call or call.get("name") != TOOL_FUNCTION_NAME:
        return None
    return validate_tool_request(call.get("args"), settings)


# What each teacher-selectable hint strength means. "Progressive" is the default.
HINT_RULES = {k: prompts.get("tutor/tutoring/hints/" + k) for k in ("light", "progressive", "strong")}

# Shown to a student who may be in danger; set per deployment (e.g. "988 (call or text)").
CRISIS_LINE = os.getenv("CRISIS_LINE", "").strip()[:80] or "a local crisis line"

# The tutor prompt is assembled from the tutor/ sections of prompts.txt (see build_system_instruction). Order
# matters: sources, tutoring, boundaries, safety, style. Teacher toggles swap lines in
# sources and tutoring only; the rest is not configurable per course.


def _section(title, body):
    return "%s\n%s" % (title, body) if body else ""


def build_system_instruction(context_block, memory_block="", docs_block="", settings=None,
                             conversation_summary="", custom_rules=None, outline="",
                             preferences_block="", material_in_message=False, course_name="",
                             language="", parts=None):
    """Assemble the tutor's system prompt. If `parts` is a list, it receives
    (label, text) per section, for the developer debug panel.

    The order is load-bearing. Teacher material is the only source of facts, and
    everything after it is labelled context, so a student's uploaded assignment
    can't promote itself into course content (or smuggle in instructions) by
    sharing a prompt with it.

    With `material_in_message` the retrieved excerpts ride at the top of the
    current user message instead of here, which keeps this prompt identical from
    turn to turn so the server's prompt cache holds.
    """
    settings = course_settings(settings)
    grounded = settings["grounded_only"]
    name = " ".join(course_name.replace('"', "'").split())[:80]
    P = prompts.get
    sources = [
        P("tutor/sources/header"),
        P("tutor/sources/grounded" if grounded else "tutor/sources/open"),
        prompts.fill("tutor/sources/not_covered_" + ("grounded" if grounded else "open"),
                     search=agent_tools.SEARCH_COURSE, web=agent_tools.WEB_SEARCH),
    ]
    if settings["state_uncertainty"]:
        sources[-1] += " " + P("tutor/sources/state_uncertainty")
    sources.append(P("tutor/sources/check_calculations"))

    tutoring = [
        P("tutor/tutoring/header"),
        P("tutor/tutoring/guide_not_complete" if settings["guide_not_complete"] else "tutor/tutoring/complete_allowed"),
        P("tutor/tutoring/reveal_answers" if settings["reveal_final_answers"] else "tutor/tutoring/hold_answers"),
        "- " + HINT_RULES[settings["hint_strength"]],
        P("tutor/tutoring/worked_examples" if settings["worked_examples"] else "tutor/tutoring/no_worked_examples"),
        P("tutor/tutoring/general"),
    ]
    tail = "\n\n".join((P("tutor/boundaries"), prompts.fill("tutor/safety", crisis_line=CRISIS_LINE), P("tutor/style")))

    rules = [str(r)[:1200] for r in (custom_rules or []) if str(r).strip()]
    types = enabled_tool_types(settings)
    sections = [
        prompts.fill("tutor/core", course='"%s"' % name if name else "one school course"),
        "\n".join(sources),
        "\n".join(tutoring) + "\n\n" + tail,
    ]
    if rules:
        sections.append(_section(P("tutor/teacher_rules"), "\n".join("- " + r for r in rules)))
    if types:
        sections.append(prompts.fill("tutor/activities", function=TOOL_FUNCTION_NAME, types=", ".join(types)))
    if outline:
        sections.append(_section(P("tutor/outline"), outline))
    if context_block and not material_in_message:
        sections.append(_section(P("tutor/material"), context_block))
    if preferences_block:
        sections.append(_section(P("tutor/preferences"), preferences_block))
    if memory_block:
        sections.append(_section(P("tutor/memory"), memory_block))
    if conversation_summary:
        sections.append(_section(P("tutor/summary"), conversation_summary))
    if docs_block:
        sections.append(
            P("tutor/upload") + "\n<upload>" + docs_block.replace("</upload", "<​/upload") + "</upload>")
    if language:
        sections.append(prompts.fill("tutor/language", language=language))
    if parts is not None:
        labels = (("You are", "Persona"), ("SOURCES", "Sources"), ("TUTORING", "Tutoring, boundaries, safety, style"),
                  ("TEACHER RULES", "Teacher rules"), ("ACTIVITIES", "Activities"), ("COURSE OUTLINE", "Course outline"),
                  ("TEACHER MATERIAL", "Teacher material"), ("STUDENT PREF", "Student preferences"),
                  ("ABOUT THIS", "Memory"), ("EARLIER", "Chat summary"), ("<upload>", "Student upload"),
                  ("Write every", "Language"))
        parts.extend((next(l for p, l in labels if t.startswith(p)), t) for t in sections)
    return "\n\n".join(sections)


def rate_limited(key, limit=CHAT_RATE_LIMIT, window=CHAT_RATE_WINDOW):
    """Sliding-window in-memory rate limit for an arbitrary key (we use uids).

    Per-process only: it resets on restart and is not shared across workers or
    instances, so it's a courtesy throttle, not a hard guarantee. A real limit at
    multi-instance scale needs shared state (Redis) or the platform's own WAF.
    """
    global _rate_last_prune
    now = time.time()
    with _rate_lock:
        dq = _rate_hits.setdefault(key, deque())
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()

        # Sweep keys that have gone quiet. Without this the dict grows by one
        # entry per distinct caller forever — a slow leak on a 512MB instance.
        # The idle horizon is deliberately far longer than any window in use, so
        # pruning can never cut a key's window short and hand back free requests.
        if now - _rate_last_prune > 300:
            _rate_last_prune = now
            stale = now - 3600
            for k in [k for k, v in _rate_hits.items() if k != key and (not v or v[-1] <= stale)]:
                del _rate_hits[k]

        if len(dq) >= limit:
            return True
        dq.append(now)
        return False


# ---------- static pages ----------

@app.route('/')
def page_home():
    """The app opens on the sign-in page — there is no marketing landing page.
    /landing.html stays as a redirect so old links and bookmarks don't 404."""
    return redirect('/login', code=302)


def legacy_page_redirect(path):
    """Permanently preserve old .html bookmarks, including their query string."""
    query = request.query_string.decode('utf-8')
    return redirect(path + (f'?{query}' if query else ''), code=308)


@app.route('/landing.html')
def legacy_landing():
    return legacy_page_redirect('/login')


@app.route('/student')
def page_student():
    return send_from_directory(WEB_DIR, 'student.html')


@app.route('/student.html')
def legacy_student():
    return legacy_page_redirect('/student')


@app.route('/login')
def page_login():
    return send_from_directory(WEB_DIR, 'login.html')


@app.route('/login.html')
def legacy_login():
    return legacy_page_redirect('/login')


@app.route('/status')
def page_status():
    return send_from_directory(WEB_DIR, 'status.html')


@app.route('/auth.js')
def auth_js():
    return send_from_directory(WEB_DIR, 'auth.js', max_age=3600)


@app.route('/teacher')
def page_knowledge():
    return send_from_directory(WEB_DIR, 'teacherknowledge.html')


@app.route('/teacherknowledge.html')
def legacy_knowledge():
    return legacy_page_redirect('/teacher')


@app.route('/teacher-stats')
def page_stats():
    return send_from_directory(WEB_DIR, 'teacherstats.html')


@app.route('/teacherstats.html')
def legacy_stats():
    return legacy_page_redirect('/teacher-stats')


@app.route('/ink.css')
def ink_css():
    return send_from_directory(WEB_DIR, 'ink.css', max_age=3600)


@app.route('/ink.js')
def ink_js():
    return send_from_directory(WEB_DIR, 'ink.js', max_age=3600)


@app.route('/theme.css')
def theme_css():
    return send_from_directory(WEB_DIR, 'theme.css', max_age=3600)


@app.route('/theme.js')
def theme_js():
    return send_from_directory(WEB_DIR, 'theme.js', max_age=3600)


@app.route('/transition.css')
def transition_css():
    return send_from_directory(WEB_DIR, 'transition.css', max_age=3600)


@app.route('/transition.js')
def transition_js():
    return send_from_directory(WEB_DIR, 'transition.js', max_age=3600)


@app.route('/settings.css')
def settings_css():
    return send_from_directory(WEB_DIR, 'settings.css', max_age=3600)


@app.route('/settings.js')
def settings_js():
    return send_from_directory(WEB_DIR, 'settings.js', max_age=3600)


@app.route('/mobile.css')
def mobile_css():
    return send_from_directory(WEB_DIR, 'mobile.css', max_age=3600)


@app.route('/mobile.js')
def mobile_js():
    return send_from_directory(WEB_DIR, 'mobile.js', max_age=3600)


@app.route('/manifest.json')
def page_manifest():
    return send_from_directory(WEB_DIR, 'manifest.json', max_age=3600)


@app.route('/icons/<path:name>')
def page_icon(name):
    # send_from_directory refuses to escape the directory it is given, so the
    # <path:> converter can't be walked back up into the app root.
    return send_from_directory(os.path.join(WEB_DIR, 'icons'), name, max_age=86400)


@app.route('/audio/<name>')
def page_audio(name):
    if name not in {
        'toolkit_using.mp3', 'toolkit_done.mp3', 'toolkit_fail.mp3',
        'quiz_correct.mp3', 'quiz_incorrect.mp3', 'high_score.mp3', 'low_score.mp3',
    }:
        return jsonify({'error': 'Audio not found.'}), 404
    return send_from_directory(os.path.join(WEB_DIR, 'audio'), name, max_age=86400)


@app.route('/favicon.ico')
def favicon():
    """Browsers ask for this whether or not a page links it, so answer rather
    than log a 404 on every cold load."""
    return send_from_directory(os.path.join(WEB_DIR, 'icons'), 'favicon.ico',
                               max_age=86400)


@app.route('/.well-known/assetlinks.json')
def assetlinks():
    """Digital Asset Links, for the Android app in android/.

    This is what tells Android the APK and this site are the same party; until
    the fingerprint in the file is real, the installed app shows a browser URL
    bar. Served with no caching to speak of, because a stale copy after a
    signing-key change is very hard to diagnose.
    """
    return send_from_directory(os.path.join(WEB_DIR, '.well-known'),
                               'assetlinks.json',
                               mimetype='application/json', max_age=300)


# ---------- auth ----------

@app.route('/auth/config', methods=['GET'])
def auth_config():
    """Public Firebase Web SDK config the browser needs to sign users in.
    apiKey is not a secret (it ships in every Firebase web app)."""
    response = jsonify({
        "apiKey": FIREBASE_WEB_API_KEY,
        "authDomain": FIREBASE_AUTH_DOMAIN,
        "projectId": FIREBASE_PROJECT_ID,
    })
    response.cache_control.public = True
    response.cache_control.max_age = 3600
    return response


def discard_unregistered_user(uid):
    """Delete a Firebase account that never completed registration.

    The browser has to create the Firebase account *before* it can prove it knows
    the teacher code, so a wrong code leaves a real, usable account behind with no
    role. auth.js tries to undo that itself, but a client-side rollback is a
    courtesy and not a guarantee — a closed tab, a lost connection, or a redirect
    firing mid-request skips it, and the account survives.

    Only ever touches an account with no Users document, so an existing student
    who fumbles the teacher code keeps the account they already had.
    """
    try:
        if db.collection("Users").document(uid).get().exists:
            return
        fb_auth.delete_user(uid)
        logger.info("Deleted half-created account %s (registration never completed).", uid)
    except Exception:
        logger.exception("Could not clean up half-created account %s", uid)


@app.route('/auth/register', methods=['POST'])
@require_auth(allow_roleless=True)
def auth_register():
    """Finish account setup after the browser has created a Firebase account.
    Records the user's role in Firestore. Becoming a teacher requires the
    correct teacher signup code; everyone else is a student.
    Body: { role: 'teacher'|'student', teacher_code? }
    """
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in ("teacher", "student"):
        return jsonify({"error": "role must be 'teacher' or 'student'"}), 400

    if role == "teacher":
        # Throttle before comparing: this is the only place the teacher code can
        # be tested, so an unmetered comparison here is an open brute-force oracle.
        if rate_limited(f"register:{request.remote_addr}", REGISTER_RATE_LIMIT, REGISTER_RATE_WINDOW):
            return jsonify({"error": "Too many attempts. Please wait a few minutes."}), 429
        supplied = str(data.get("teacher_code") or "")
        # Compare as bytes: compare_digest rejects non-ASCII *str* with a TypeError,
        # and `supplied` is attacker-controlled — as text, a code with an accent in
        # it turned a wrong-code 403 into an unhandled 500.
        if not hmac.compare_digest(supplied.encode("utf-8"), TEACHER_SIGNUP_CODE.encode("utf-8")):
            logger.warning("Rejected teacher signup code from %s (uid %s).", request.remote_addr, request.uid)
            discard_unregistered_user(request.uid)
            return jsonify({"error": "Wrong teacher code."}), 403

    uid = request.uid
    user_ref = db.collection("Users").document(uid)
    existing = user_ref.get()
    # Don't let an existing student silently re-register as a teacher without the
    # code (the code check above already guards the teacher path); preserve role
    # on repeat student calls so we don't clobber a teacher back down to student.
    if existing.exists and role == "student":
        current = (existing.to_dict() or {}).get("role")
        if current == "teacher":
            role = "teacher"

    user_ref.set({
        "email": request.user.get("email"),
        "role": role,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    invalidate_role(uid)  # the very next request must see the role we just wrote
    return jsonify({"uid": uid, "email": request.user.get("email"), "role": role})


@app.route('/auth/me', methods=['GET'])
@require_auth
def auth_me():
    """Return the signed-in user's identity and role."""
    return jsonify({
        "uid": request.uid,
        "email": request.user.get("email"),
        "role": get_role(request.uid),
        "is_dev": is_dev_user(request.user),
    })


@app.route('/me/preferences', methods=['GET', 'POST'])
@require_auth
def user_preferences():
    """Read or update the caller's tutor preferences (style only, per user)."""
    dev = is_dev_user(request.user)
    ref = db.collection("Users").document(request.uid)
    try:
        snap = ref.get()
        stored = (snap.to_dict() or {}).get("preferences") if snap.exists else None
        current = normalize_preferences(stored, dev)
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            supplied = data.get("preferences")
            if not isinstance(supplied, dict):
                return jsonify({"error": "preferences must be an object"}), 400
            merged = normalize_preferences(dict(current, **supplied), dev)
            if any(profanity.is_profane(merged[k]) for k in ("style_note", "language")):
                return jsonify({"error": "Keep the style note civil."}), 400
            ref.set({"preferences": merged}, merge=True)
            with _manifest_lock:
                _prefs_cache.pop(request.uid, None)
            current = merged
        return jsonify({
            "preferences": current,
            "personalities": [{"id": k, "label": v["label"]} for k, v in PERSONALITIES.items()
                              if dev or not v.get("dev")],
        })
    except Exception as e:
        return server_error("Could not load your preferences.", e)


# ---------- classes ----------

@app.route('/classes', methods=['GET'])
@require_auth
def list_classes():
    """List the classes the caller can act in (teacher: owned + join codes;
    student: joined)."""
    role = get_role(request.uid)
    return jsonify({"classes": get_user_classes(request.uid, role), "role": role})


@app.route('/classes', methods=['POST'])
@require_teacher
def create_class():
    """Create a class owned by the calling teacher. The teacher's FIRST class
    also absorbs any legacy global rules. Body: { name }"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return jsonify({"error": "Course name is required"}), 400
    try:
        is_first = not list(
            db.collection("Classes").where("teacher_uid", "==", request.uid).limit(1).stream()
        )
        doc = db.collection("Classes").document()
        doc.set({
            "name": name,
            "join_code": gen_join_code(),
            "teacher_uid": request.uid,
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        migrated = migrate_default_rules_to(doc.id) if is_first else 0
        result = class_to_dict(doc.get(), include_code=True)
        result["migrated_rules"] = migrated
        return jsonify(result)
    except Exception as e:
        return server_error("Could not create the course.", e)


@app.route('/classes/join', methods=['POST'])
@require_auth
def join_class():
    """Join a class by its code. Body: { join_code }"""
    # Throttle before touching Firestore: this is the one endpoint where a
    # wrong answer is still informative (it tells you a code doesn't exist), so
    # it's the one an attacker would spray to find live classes.
    #
    # Both keys, not either: the uid budget stops one signed-in student grinding
    # codes, and the IP budget is what actually costs a determined attacker
    # something — fresh Firebase accounts are free, so a per-uid limit alone
    # resets on every guess. (With TRUST_PROXY_HOPS unset behind a proxy the IP
    # key collapses to the load balancer for everyone, which is why the deploy
    # warning at the top of this file exists.)
    if (rate_limited(f"join:{request.uid}", JOIN_RATE_LIMIT, JOIN_RATE_WINDOW)
            or rate_limited(f"joinip:{request.remote_addr}", JOIN_IP_RATE_LIMIT, JOIN_RATE_WINDOW)):
        return jsonify({"error": "Too many join attempts. Please wait a few minutes."}), 429

    data = request.get_json(silent=True) or {}
    code = (data.get("join_code") or "").strip().upper()
    if not code:
        return jsonify({"error": "A join code is required"}), 400
    try:
        hit = list(db.collection("Classes").where("join_code", "==", code).limit(1).stream())
        if not hit:
            return jsonify({"error": "No course found for that code."}), 404
        cls = hit[0]
        cls.reference.collection("Members").document(request.uid).set({
            "email": request.user.get("email"),
            "joined_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        db.collection("Users").document(request.uid).set(
            {"class_ids": firestore.ArrayUnion([cls.id])}, merge=True
        )
        return jsonify(class_to_dict(cls))
    except Exception as e:
        return server_error("Could not join that course.", e)


@app.route('/classes/<class_id>', methods=['DELETE'])
@require_teacher
def delete_class(class_id):
    """Delete a class the teacher owns: its rules (Pinecone namespace), member
    records, and the class document."""
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Course not found"}), 404
    try:
        try:
            pinecone_index.delete(delete_all=True, namespace=class_id)
        except Exception:
            # Namespace may not exist yet (no rules added) — that's fine.
            logger.info("No Pinecone namespace to clear for class %s", class_id)
        cls_ref = db.collection("Classes").document(class_id)
        # Batch the member cleanup instead of a round-trip per member, and drop
        # the class from each student's own list so it doesn't linger there as a
        # class they can't see and can't leave.
        batch, ops = db.batch(), 0
        for member in cls_ref.collection("Members").stream():
            batch.delete(member.reference)
            batch.set(
                db.collection("Users").document(member.id),
                {"class_ids": firestore.ArrayRemove([class_id])},
                merge=True,
            )
            ops += 2
            if ops >= 400:                      # Firestore caps a batch at 500 writes
                batch.commit()
                batch, ops = db.batch(), 0
        if ops:
            batch.commit()
        cls_ref.delete()
        return jsonify({"status": "deleted", "id": class_id})
    except Exception as e:
        return server_error("Could not delete the course.", e)


@app.route('/course-settings', methods=['GET', 'POST'])
@require_teacher
def manage_course_settings():
    """Read or update the small policy surface for one teacher-owned course."""
    data = request.args if request.method == 'GET' else (request.get_json(silent=True) or {})
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403
    ref = db.collection("Classes").document(class_id)
    snap = ref.get()
    current = course_settings((snap.to_dict() or {}).get("settings") if snap.exists else None)
    # "Additional instruction" was a second, smaller copy of custom teacher rules.
    # Fold any saved text into a real rule once, then clear it.
    extra = current.get("additional_instructions")
    if extra:
        rid = "custom_migrated_%s" % secrets.token_hex(4)
        mutate_custom_rules(class_id, lambda rules: rules if any(
            r.get("text") == extra for r in rules.values()) else {**rules, rid: {"id": rid, "text": extra}})
        current = dict(current, additional_instructions="")
        ref.set({"settings": current}, merge=True)
    if request.method == 'GET':
        return jsonify({"settings": current})

    supplied = data.get("settings")
    if not isinstance(supplied, dict):
        return jsonify({"error": "settings must be an object"}), 400
    supplied = {k: v for k, v in supplied.items() if k != "additional_instructions"}
    merged = course_settings(dict(current, **supplied))
    ref.set({"settings": merged, "settings_updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
    return jsonify({"settings": merged})


# ---------- student: chat (cloud-synced per user) ----------

def _user_chats(uid):
    return db.collection("Users").document(uid).collection("Chats")


def _user_profile(uid, class_id):
    """The running read of one student in one class.

    The class id is the *document id*, not a field to filter on. That is the whole
    isolation argument: there is no query here to get wrong and no `where` clause
    for a later refactor to drop, and a different class is simply a different
    document that this call never touches.
    """
    return db.collection("Users").document(uid).collection("Profiles").document(class_id)


@app.route('/chats', methods=['GET'])
@require_auth
def list_chats():
    """List the signed-in student's saved conversations, newest first."""
    try:
        docs = list(
            _user_chats(request.uid)
            .order_by("last_active", direction=firestore.Query.DESCENDING)
            .stream()
        )
    except Exception:
        # Missing index / no docs yet — fall back to an unordered read.
        docs = list(_user_chats(request.uid).stream())
    chats = []
    for d in docs:
        m = d.to_dict() or {}
        chats.append({
            "id": d.id,
            "title": m.get("title") or "New chat",
            "class_id": m.get("class_id"),
            "last_active": _ts_seconds(m.get("last_active")),
        })
    chats.sort(key=lambda c: c["last_active"], reverse=True)
    return jsonify({"chats": chats})


@app.route('/chats/<chat_id>/messages', methods=['GET'])
@require_auth
def chat_messages(chat_id):
    """Return all messages for one of the user's conversations, oldest first."""
    if not valid_doc_id(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    chat_ref = _user_chats(request.uid).document(chat_id)
    chat_snap = chat_ref.get()
    if not chat_snap.exists:
        return jsonify({"error": "Chat not found"}), 404
    # Bounded so one runaway conversation can't turn into an unbounded read.
    docs = list(
        chat_ref.collection("Messages").order_by("timestamp").limit(MAX_MESSAGES_RETURNED).stream()
    )
    is_teacher = get_role(request.uid) == "teacher"
    # Neither the chat document nor the course settings are read here any more:
    # between them they only decided whether to ship source excerpts, and that is
    # a role check now. Two fewer Firestore reads every time a conversation is
    # opened.
    messages = []
    for d in docs:
        m = d.to_dict() or {}
        rules = m.get("rules") or []
        messages.append({
            "role": m.get("role"),
            "content": m.get("content", ""),
            "display": m.get("display") or None,
            # The retrieved source chunks are teacher-only. The UI already hid
            # them from students, but hiding them in the browser is not hiding
            # them: the same token fetches this route from a terminal. `grounded`
            # carries the one bit the student UI actually needs (whether anything
            # backed the answer) without shipping the material itself.
            "rules": rules if is_teacher else [],
            "grounded": bool(rules),
            # Answered off the student's own upload rather than class material.
            "reviewed": bool(m.get("reviewed")),
            # Course material is teacher-only, with no course toggle to loosen it:
            # excerpts stay stored on the message so a teacher can review the
            # conversation later, and are withheld here for everyone else.
            "sources": (m.get("sources") or []) if is_teacher else [],
            "tool": m.get("tool") if isinstance(m.get("tool"), dict) else None,
            # A refusal, not an answer — the UI marks it so a student rereading
            # the thread can see the tutor declined rather than failed.
            "blocked": bool(m.get("blocked")),
            "issue_kind": m.get("issue_kind") if m.get("issue_kind") in ("academic", "behavioral") else None,
        })
    return jsonify({"messages": messages, "ended": bool((chat_snap.to_dict() or {}).get("ended"))})


@app.route('/chats/<chat_id>', methods=['DELETE'])
@require_auth
def delete_chat(chat_id):
    """Delete one of the user's conversations (and its messages)."""
    if not valid_doc_id(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    chat_ref = _user_chats(request.uid).document(chat_id)
    snap = chat_ref.get()
    if not snap.exists:
        return jsonify({"error": "Chat not found"}), 404
    try:
        # Delete messages in batches, then the chat doc itself.
        msgs = chat_ref.collection("Messages")
        while True:
            batch_docs = list(msgs.limit(400).stream())
            if not batch_docs:
                break
            batch = db.batch()
            for d in batch_docs:
                batch.delete(d.reference)
            batch.commit()
        # Conversation attachments are immediate context, not durable course
        # memory. Deleting the conversation removes that context as well.
        for file_doc in _user_files(request.uid).where("chat_id", "==", chat_id).stream():
            file_doc.reference.delete()
        chat_ref.delete()
        return jsonify({"status": "deleted", "id": chat_id})
    except Exception as e:
        return server_error("Could not delete that conversation.", e)


# ---------- student endpoint ----------

@app.route('/chat', methods=['POST'])
@require_auth
def chat():
    if rate_limited(f"chat:{request.uid}"):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429

    data = request.get_json(silent=True) or {}
    user_message = data.get("message")
    # Conversations now live under the signed-in user. chat_id picks an existing
    # conversation; omit it (or pass a new id) to start a fresh one.
    chat_id = str(data.get("chat_id") or "").strip()[:128]
    if chat_id and not valid_doc_id(chat_id):
        return jsonify({"error": "Invalid chat id"}), 400
    # The tutor only answers from the chosen class's knowledge base, and the user
    # must belong to that class — this is what stops non-members using the app.
    class_id = (data.get("class_id") or "").strip()
    role = get_role(request.uid)
    if not user_in_class(request.uid, class_id, role):
        return jsonify({"error": "Join this course before using the tutor."}), 403

    if not user_message or not isinstance(user_message, str) or not user_message.strip():
        return jsonify({"error": "No message provided"}), 400
    user_message = user_message.strip()
    if len(user_message) > MAX_MESSAGE_CHARS:
        return jsonify({"error": "Message is too long."}), 413
    # A short label the client shows in place of a long generated prompt (a quiz
    # review, say). The model still gets the full message; the label is only for display.
    display = " ".join(str(data.get("display") or "").split())[:120]

    # Profanity never reaches retrieval or the model. Answering it costs tokens
    # to produce something nobody wants, and leaving the reply to the model makes
    # it a matter of the model's mood; this way the response is the same every
    # time and a teacher can rely on it.
    blocked = profanity.scan(user_message)

    # Developer accounts can ask for a debug report alongside the reply. The flag
    # is decided from the verified token here; the client only says "please".
    dev = is_dev_user(request.user)
    debug_on = dev and data.get("debug") is True
    t0 = time.perf_counter()
    dbg = {"timings": {}, "retrieval": {"passes": []}, "path": "model"}

    def lap(name, since):
        dbg["timings"][name] = round((time.perf_counter() - since) * 1000)

    try:
        is_small_talk = bool(_SMALL_TALK_RE.fullmatch(user_message))
        # Small talk needs no course lookup, but the tutor still answers it.
        skip_retrieval = is_small_talk or bool(blocked)
        raw_matches = []
        if skip_retrieval:
            pinecone_resp = {"matches": []}
        else:
            t = time.perf_counter()
            question_embedding = embed(user_message)
            lap("embed_ms", t)
            t = time.perf_counter()
            pinecone_resp = pinecone_index.query(
                vector=question_embedding,
                top_k=RETRIEVAL_TOP_K,
                include_metadata=True,
                namespace=class_id,
                filter={"source": {"$exists": True}},
            )
            lap("pinecone_ms", t)
            raw_matches = list(pinecone_resp['matches'])
            dbg["retrieval"]["passes"].append({"query": user_message[:200], "floor": RETRIEVAL_MIN_SCORE})

        # Keep only chunks similar enough to the question. If nothing clears the
        # bar the tutor still answers (see build_system_instruction): it replies
        # to conversation naturally and, for a subject question the material lacks,
        # says so once and points at the course outline.
        context_block, teacher_sources = _source_context(pinecone_resp['matches'])
        # Retrieved excerpts are factual course context. Custom teacher rules are
        # prompt policy stored in Firestore, never vectors that can be retrieved
        # as if they were course facts.
        teacher_rules = [s["excerpt"] for s in teacher_sources]
        custom_rules = [r["text"] for r in load_custom_rules(class_id)]

        # Resolve (or create) the conversation document for this user.
        chats_col = _user_chats(request.uid)
        chat_doc = chats_col.document(chat_id) if chat_id else chats_col.document()
        chat_id = chat_doc.id
        msgs_ref = chat_doc.collection('Messages')

        # One read serves double duty: "is this a new conversation?" and the
        # running summary we extend below (see STAT_SUMMARY_VERSION).
        snap = chat_doc.get()
        is_new = not snap.exists
        prev = (snap.to_dict() or {}) if snap.exists else {}
        if prev.get("ended"):
            return jsonify({"error": ENDED_REPLY, "code": "conversation_ended"}), 409
        migration_summary_update = {}
        if not is_new and "message_count" not in prev:
            # One-time migration for conversations created before rolling memory.
            # Read is bounded by the same cap as chat display, and the transcript
            # itself has a hard character budget so an old chat cannot explode a
            # summarization request.
            old = list(
                msgs_ref.order_by("timestamp").limit(MAX_MESSAGES_RETURNED).stream()
            )
            prev["message_count"] = len(old)
            older = old[:-HISTORY_TURNS] if len(old) > HISTORY_TURNS else []
            if older:
                transcript_parts, used = [], 0
                for d in older:
                    m = d.to_dict() or {}
                    line = ("Student" if m.get("role") == "student" else "Tutor") + ": " + str(m.get("content") or "")[:1200]
                    if used + len(line) > 20000:
                        break
                    transcript_parts.append(line)
                    used += len(line)
                try:
                    migrated_summary = summarize_tutoring_state("", "\n".join(transcript_parts))
                    if migrated_summary:
                        prev["conversation_summary"] = migrated_summary
                        prev["summary_through"] = len(older)
                        migration_summary_update = {
                            "conversation_summary": migrated_summary,
                            "summary_through": len(older),
                        }
                except Exception:
                    logger.exception("Could not initialize summary for a legacy conversation.")

        # Preserve useful state before the oldest replayed turns disappear. This
        # summary lives on the chat document, never in Pinecone or course material.
        conversation_summary, summary_update = refresh_conversation_summary(msgs_ref, prev)

        # Replay the recent conversation so the AI remembers earlier turns.
        t = time.perf_counter()
        history = [] if blocked else load_history(msgs_ref)
        lap("history_ms", t)

        # A short follow-up ("what about the second one?") embeds to nothing on
        # its own. When the first pass found no material, try once more with the
        # previous question folded in and a slightly lower floor.
        prior = next((str((turn.get("parts") or [{}])[0].get("text") or "")
                      for turn in reversed(history) if turn.get("role") == "user"), "")
        if not skip_retrieval and not context_block and prior:
            t = time.perf_counter()
            retry_query = (prior[:300] + " " + user_message).strip()
            try:
                retry = pinecone_index.query(
                    vector=embed(retry_query), top_k=RETRIEVAL_TOP_K, include_metadata=True,
                    namespace=class_id, filter={"source": {"$exists": True}})
                raw_matches += list(retry['matches'])
                dbg["retrieval"]["passes"].append({"query": retry_query[:200], "floor": RETRIEVAL_FALLBACK_SCORE})
                context_block, teacher_sources = _source_context(retry['matches'], RETRIEVAL_FALLBACK_SCORE)
                teacher_rules = [s["excerpt"] for s in teacher_sources]
            except llm.EmbeddingsUnavailable:
                raise
            except Exception:
                logger.exception("Fallback retrieval failed; continuing without it.")
            lap("fallback_ms", t)

        # Recollection: what this student asked in *this class* before, plus the
        # assignment/rubric they uploaded to it. Both queries filter on class_id,
        # which is what makes switching class start the tutor blank — a student's
        # biology history has no business colouring a history lesson. The current
        # conversation is excluded because `history` above already replays it.
        # Read once: the same profile renders into the prompt below and tells the
        # counter update which sticking points are already being tracked.
        # `skip_retrieval` covers both a blocked message and a bare "thanks" —
        # neither is the student's substantive writing, so neither shapes the
        # picture the tutor forms of them.
        student_prof = {} if skip_retrieval else load_class_profile(request.uid, class_id)
        memory_block = "" if skip_retrieval else load_class_memory(
            request.uid, class_id, exclude_chat_id=chat_id, profile=student_prof)
        docs_block = "" if skip_retrieval else build_docs_block(load_student_docs(request.uid, class_id, chat_id))
        settings = load_course_settings(class_id)
        # Folded in from the retired "Additional instruction" setting, until the
        # teacher's next visit to the settings page turns it into a real rule.
        if settings.get("additional_instructions"):
            custom_rules = custom_rules + [settings["additional_instructions"]]
        manifest_docs = [] if blocked else load_course_manifest(class_id)
        outline = manifest_outline(manifest_docs)
        user_prefs = {} if blocked else load_user_preferences(request.uid, dev)
        preferences_block = "" if blocked else preference_directive(user_prefs, dev)

        # What the tutor's own tools did this turn (see agent_tools): topics it
        # flagged for the teacher, and whether it ended the conversation.
        agent = {"gaps": [], "ended": False, "title": None}

        def finish_exchange(final_answer, tool_request):
            reviewed = bool(docs_block) and not teacher_rules
            issue_kind = _issue_kind(user_message)
            chat_meta = {
                "last_active": firestore.SERVER_TIMESTAMP,
                "class_id": class_id,
                "message_count": int(prev.get("message_count") or 0) + 2,
            }
            chat_meta.update(migration_summary_update)
            chat_meta.update(summary_update)
            title = None
            if is_new:
                shown = display or user_message
                title = BLOCKED_TITLE if blocked else (
                    agent["title"] or shown[:40] + ("…" if len(shown) > 40 else ""))
                chat_meta["title"] = title
                chat_meta["created_at"] = firestore.SERVER_TIMESTAMP
            chat_meta.update(summarize_exchange(prev, is_new, display or user_message, final_answer, agent["gaps"]))
            if agent["ended"]:
                chat_meta["ended"] = True
            chat_doc.set(chat_meta, merge=True)
            if PROFILE_ENABLED and not skip_retrieval:
                try:
                    update = profile_update(class_id, user_message, chat_meta, student_prof)
                    if update:
                        _user_profile(request.uid, class_id).set(update, merge=True)
                except Exception:
                    logger.exception("Could not update student profile; continuing.")
            student_msg = {"role": "student", "content": user_message, "blocked": bool(blocked),
                           "timestamp": firestore.SERVER_TIMESTAMP}
            if display:
                student_msg["display"] = display
            msgs_ref.add(student_msg)
            msgs_ref.add({"role": "teacher", "content": final_answer, "rules": teacher_rules,
                          "sources": teacher_sources, "reviewed": reviewed,
                          "tool_request": tool_request, "gap_topics": agent["gaps"],
                          "blocked": bool(blocked),
                          "issue_kind": issue_kind, "timestamp": firestore.SERVER_TIMESTAMP})
            payload = {
                "response": final_answer,
                "blocked": bool(blocked),
                "rules_used": teacher_rules if role == "teacher" else [],
                "sources": teacher_sources if role == "teacher" else [],
                "grounded": bool(teacher_rules),
                "reviewed": reviewed,
                "tool_request": tool_request,
                "toolkits": enabled_tool_types(settings),
                "ended": agent["ended"],
                "thinking_locked": llm.can_disable_thinking() is False,
                "issue_kind": issue_kind,
                "chat_id": chat_id,
                "title": title,
            }
            if debug_on:
                lap("total_ms", t0)
                dbg["retrieval"]["matches"] = [
                    {"source": (m.get("metadata") or {}).get("source"),
                     "chunk": (m.get("metadata") or {}).get("chunk"),
                     "score": round(float(m.get("score") or 0), 4),
                     "kept": float(m.get("score") or 0) >= RETRIEVAL_MIN_SCORE}
                    for m in raw_matches]
                dbg.update({
                    "model": CHAT_MODEL, "small_talk": is_small_talk, "new_chat": is_new,
                    "agent": {"gaps": agent["gaps"], "ended": agent["ended"]},
                    "history_turns": len(history), "outline_docs": len(manifest_docs),
                    "settings": settings, "custom_rules": len(custom_rules),
                    "limits": {"top_k": RETRIEVAL_TOP_K, "floor": RETRIEVAL_MIN_SCORE,
                               "retry_floor": RETRIEVAL_FALLBACK_SCORE},
                    "excerpts_sent": len(teacher_sources), "has_memory": bool(memory_block),
                    "has_upload": bool(docs_block), "has_summary": bool(conversation_summary),
                    "personality": (user_prefs or {}).get("personality"),
                })
                payload["debug"] = dbg
            return payload

        def event(kind, payload):
            return "data: " + json.dumps({"type": kind, **payload}, ensure_ascii=False) + "\n\n"

        stream_requested = data.get("stream") is True

        tool_request = None
        if blocked:
            dbg["path"] = "blocked"
            final_answer = BLOCKED_REPLY
        else:
            # The model always runs when the message isn't blocked. With no matching
            # material it gets a "nothing matched" rule instead of a canned refusal,
            # so "hello" and "quiz me on X" are handled by judgement rather than by
            # a similarity score; build_system_instruction keeps it from filling any
            # subject gap with facts of its own.
            # The retrieved excerpts ride in the current user message only (never in
            # the system prompt or older turns), so the system prompt stays stable and
            # cacheable. If the server says the prompt is too long, one retry drops
            # the lowest-ranked excerpts and the oldest history.
            def user_turn(n_sources):
                block = "\n\n".join(render_source(i, src) for i, src in enumerate(teacher_sources[:n_sources], 1))
                text = prompts.fill("tutor/user_turn", material=block or prompts.get("tutor/no_material"),
                                    message=user_message)
                return [{"role": "user", "parts": [{"text": text}]}]

            plans = [(len(teacher_sources), history),
                     (len(teacher_sources) // 2, trim_history(history[len(history) // 2:]))]
            prompt_parts = []
            system_instruction = build_system_instruction(
                "", memory_block, docs_block, settings, conversation_summary, custom_rules,
                outline=outline, preferences_block=preferences_block, material_in_message=True,
                course_name=load_course_name(class_id), language=user_prefs.get("language", ""),
                parts=prompt_parts
            )
            t_model = time.perf_counter()

            # Thinking is off unless the student switched it on for this chat.
            think = data.get("think") is True
            tz_name = str(data.get("tz") or "")[:64]
            tools = [t for t in [practice_activity_tool(settings)] if t] + agent_tools.declarations(
                not settings["grounded_only"], first_message=is_new)
            if debug_on:
                dbg["path"] = "smalltalk" if is_small_talk else ("grounded" if teacher_rules else "no_material")
                tools_text = llm._tool_instructions(tools)
                # What the model really receives: this prompt, then the tool list llm.py appends.
                full_text = system_instruction + "\n\n" + tools_text
                dbg["prompt"] = {
                    "chars": len(full_text), "tokens": llm.count_tokens(full_text),
                    "parts": [{"label": l, "chars": len(t), "text": t} for l, t in prompt_parts]
                             + [{"label": "Tool list (added by llm.py)", "chars": len(tools_text), "text": tools_text}],
                    "text": full_text,
                    "user_turn": user_turn(len(teacher_sources))[0]["parts"][0]["text"],
                }
                dbg["tools_available"] = [t["name"] for t in tools]
                dbg["think"], dbg["tz"], dbg["tool_calls"] = think, tz_name, []
            alone = {t["name"] for t in tools if t.get("alone")}
            sided = {t["name"] for t in tools} & agent_tools.SIDE
            gen_args = dict(system=system_instruction, temperature=0.35, think=think)

            def run_side_tool(name, args):
                """End-of-reply tools: they change the chat, and nothing is sent back to the model."""
                if name == agent_tools.MARK_GAP:
                    topic = " ".join(str(args.get("topic") or "").split())[:200]
                    if topic and topic not in agent["gaps"] and len(agent["gaps"]) < 3:
                        agent["gaps"].append(topic)
                elif name == agent_tools.END_CONVERSATION:
                    if not _SAFETY_CONCERN_RE.search(user_message):
                        agent["ended"] = True
                elif name == agent_tools.RENAME_CHAT:
                    title = " ".join(str(args.get("title") or "").split()).strip("\"'“”.")[:50]
                    if title and not profanity.is_profane(title):
                        agent["title"] = title

            def run_agent_tool(name, args):
                """Run one of the tutor's lookup tools and return the text it sees next."""
                if name == agent_tools.SEARCH_COURSE:
                    query = " ".join(str(args.get("query") or user_message).split())[:300]
                    try:
                        found = pinecone_index.query(
                            vector=embed(query), top_k=RETRIEVAL_TOP_K, include_metadata=True,
                            namespace=class_id, filter={"source": {"$exists": True}})
                    except llm.EmbeddingsUnavailable:
                        return "Course search is unavailable right now."
                    dbg["retrieval"]["passes"].append({"query": query[:200], "floor": RETRIEVAL_FALLBACK_SCORE})
                    _, found_sources = _source_context(found["matches"], RETRIEVAL_FALLBACK_SCORE)
                    have = {x["excerpt"] for x in teacher_sources}
                    fresh = [x for x in found_sources if x["excerpt"] not in have]
                    if not fresh:
                        return "Nothing new matched in the course material."
                    start = len(teacher_sources)
                    teacher_sources.extend(fresh)
                    teacher_rules.extend(x["excerpt"] for x in fresh)
                    return "\n\n".join(render_source(start + i, x) for i, x in enumerate(fresh, 1))
                if name == agent_tools.WEB_SEARCH:
                    if settings["grounded_only"]:
                        return "Web search is turned off for this course."
                    return agent_tools.web_search(args.get("query"))
                if name == agent_tools.GET_DATETIME:
                    return agent_tools.current_datetime(tz_name)
                if name == agent_tools.CALCULATE:
                    return agent_tools.calculate(args.get("expression"))
                return "Unknown tool."

            def note_tool(call, kind, result=None, since=None):
                if debug_on:
                    dbg["tool_calls"].append({
                        "name": call["name"], "args": call.get("args"), "kind": kind,
                        "result": None if result is None else str(result)[:400],
                        "ms": None if since is None else round((time.perf_counter() - since) * 1000)})

            def model_chunks(contents, declared):
                if stream_requested:
                    yield from llm.stream(CHAT_MODEL, contents, tools=declared, **gen_args)
                    return
                result = llm.generate(CHAT_MODEL, contents, tools=declared, **gen_args)
                if result.text:
                    yield llm.Chunk(result.text, None, None)
                yield llm.Chunk("", result.tool_call, result.usage, False, result.calls)

            def model_events():
                """Run the model, feeding tool results back until it answers.

                Yields ("text", str), ("status", phase), then ("end", activity_call, usage)."""
                extra, usage, activity, wrote = [], None, None, False
                for rnd in range(MAX_TOOL_ROUNDS + 1):
                    dbg["rounds"] = rnd + 1
                    final_round = rnd == MAX_TOOL_ROUNDS or agent["ended"]
                    declared = [t for t in tools if not (final_round and t.get("alone"))]
                    text_parts, calls, started = [], [], False
                    for attempt, (n, hist) in enumerate(plans):
                        try:
                            for chunk in model_chunks(hist + user_turn(n) + extra, declared or None):
                                if chunk.thinking:
                                    yield ("status", "thinking")
                                if chunk.text:
                                    if wrote and not started:
                                        yield ("text", "\n\n")
                                    started = wrote = True
                                    text_parts.append(chunk.text)
                                    yield ("text", chunk.text)
                                usage = _add_usage(usage, chunk.usage)
                                calls = list(chunk.calls) or ([chunk.tool_call] if chunk.tool_call else calls)
                            break
                        except llm.ContextTooLong:
                            if attempt == len(plans) - 1:
                                raise
                            dbg["context_retries"] = dbg.get("context_retries", 0) + 1
                            logger.warning("Prompt exceeded the context window; retrying with less.")
                    # End-of-reply tools run whatever else the reply did; a reply can make several.
                    for call in calls:
                        if call["name"] in agent_tools.SIDE and call["name"] in sided:
                            run_side_tool(call["name"], call["args"])
                            note_tool(call, "end of reply")
                        elif call["name"] == TOOL_FUNCTION_NAME and not activity:
                            activity = activity_from_call(call, settings)
                            if activity:
                                note_tool(call, "practice activity")
                                yield ("status", "tool")
                    lookup = next((c for c in calls if c["name"] in alone), None)
                    if lookup and not activity and not final_round:
                        if lookup["name"] in agent_tools.VISIBLE:
                            yield ("status", "thinking")
                        t_tool = time.perf_counter()
                        result = run_agent_tool(lookup["name"], lookup["args"])
                        note_tool(lookup, "lookup", result, t_tool)
                        extra += [
                            {"role": "model", "parts": [{"text": "".join(text_parts) + "\nTOOL_CALL "
                                                         + json.dumps(lookup, ensure_ascii=False)}]},
                            {"role": "user", "parts": [{"text": "Tool result (%s):\n%s" % (
                                lookup["name"], agent_tools.clip(result))}]},
                        ]
                        continue
                    break
                yield ("end", activity, usage)

            if stream_requested:
                @stream_with_context
                def stream_answer():
                    try:
                        parts = []
                        streamed_tool = None
                        usage = None
                        for ev in model_events():
                            if ev[0] == "text":
                                if not parts:
                                    lap("first_token_ms", t_model)
                                parts.append(ev[1])
                                yield event("delta", {"text": ev[1]})
                            elif ev[0] == "status":
                                yield event("status", {"phase": ev[1]})
                            else:
                                _, streamed_tool, usage = ev
                        streamed_tool = streamed_tool or explicit_tool_request(user_message, settings, history)
                        final_answer = "".join(parts).strip()
                        lap("model_ms", t_model)
                        dbg["tokens"] = usage or {"prompt": None, "reply": None, "total": None}
                        if streamed_tool and not final_answer:
                            final_answer = "Here's a %s on %s." % (
                                streamed_tool["type"].replace("_", " "), streamed_tool["topic"])
                            yield event("delta", {"text": final_answer})
                        if not final_answer:
                            raise ValueError("Tutor returned an empty reply")
                        yield event("done", {"data": finish_exchange(final_answer, streamed_tool)})
                    except Exception:
                        logger.exception("Could not stream chat response.")
                        yield event("error", {"message": "Could not finish the reply. Please try again."})

                return Response(stream_answer(), mimetype="text/event-stream",
                                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
            parts, tool_request, usage = [], None, None
            for ev in model_events():
                if ev[0] == "text":
                    parts.append(ev[1])
                elif ev[0] == "end":
                    _, tool_request, usage = ev
            final_answer = "".join(parts).strip()
            lap("model_ms", t_model)
            dbg["tokens"] = usage or {"prompt": None, "reply": None, "total": None}
            tool_request = tool_request or explicit_tool_request(user_message, settings, history)
            if tool_request and not final_answer:
                # The model can answer with the function call alone. The activity
                # renders under a tutor message, so that message needs words.
                final_answer = "Here's a %s on %s." % (
                    tool_request["type"].replace("_", " "), tool_request["topic"])
            if not final_answer:
                raise ValueError("Tutor returned an empty reply")

        payload = finish_exchange(final_answer, tool_request)
        if stream_requested:
            def completed_answer():
                yield event("delta", {"text": final_answer})
                yield event("done", {"data": payload})
            return Response(completed_answer(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        return jsonify(payload)

    except llm.EmbeddingsUnavailable as e:
        return retrieval_unavailable(e)
    except Exception as e:
        return server_error("Server issue while answering.", e)


def _obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or list(props)}


# Every activity schema leads with this. Empty means "built"; a sentence means the
# model looked at the material and conversation and chose not to build anything
# (so the lists below it must be allowed to be empty).
_DECLINE = {"decline_reason": {"type": "string"}}


def _str_list(lo, hi):
    return {"type": "array", "items": {"type": "string"}, "minItems": lo, "maxItems": hi}


# The same contracts _parse_tool_result enforces, handed to the model as a JSON
# Schema so decoding itself is constrained (`response_format`;
# the endpoint). The parser stays the final gate either way.
ACTIVITY_SCHEMAS = {
    "quiz": _obj({
        "type": {"type": "string", "enum": ["quiz"]}, **_DECLINE,
        "title": {"type": "string"},
        "questions": {"type": "array", "minItems": 0, "maxItems": 5, "items": _obj({
            "prompt": {"type": "string"},
            "options": _str_list(4, 4),
            "answer": {"type": "integer", "minimum": 0, "maximum": 3},
            "explanation": {"type": "string"},
        })},
    }),
    "flashcards": _obj({
        "type": {"type": "string", "enum": ["flashcards"]}, **_DECLINE,
        "title": {"type": "string"},
        "cards": {"type": "array", "minItems": 0, "maxItems": 8, "items": _obj({
            "front": {"type": "string"}, "back": {"type": "string"}})},
    }),
    "concept_map": _obj({
        "type": {"type": "string", "enum": ["concept_map"]}, **_DECLINE,
        "title": {"type": "string"},
        "nodes": {"type": "array", "minItems": 0, "maxItems": 7, "items": _obj({
            "label": {"type": "string"}, "detail": {"type": "string"}})},
        "links": {"type": "array", "maxItems": 10, "items": _obj({
            "from": {"type": "integer", "minimum": 0, "maximum": 6},
            "to": {"type": "integer", "minimum": 0, "maximum": 6},
            "label": {"type": "string"}})},
    }),
    "review_sheet": _obj({
        "type": {"type": "string", "enum": ["review_sheet"]}, **_DECLINE,
        "title": {"type": "string"},
        "sections": {"type": "array", "minItems": 0, "maxItems": 4, "items": _obj({
            "heading": {"type": "string"}, "points": _str_list(2, 5)})},
    }),
}


TOOL_HISTORY_CHARS = 6000

def _tool_conversation(history, summary="", memory="", preferences=""):
    """The context /chat gives the tutor, rendered as a block for the activity prompt."""
    lines = [("Student: " if t["role"] == "user" else "Tutor: ") + str(t["parts"][0]["text"])[:700]
             for t in trim_history(history, TOOL_HISTORY_CHARS)]
    C = "activities/conversation/"
    blocks = [
        (prompts.get(C + "summary"), summary),
        (prompts.get(C + "recent"), "\n".join(lines)),
        (prompts.get(C + "memory"), memory),
        (prompts.get(C + "preferences"), preferences),
    ]
    return "\n\n".join("%s:\n%s" % (title, body) for title, body in blocks if body)


def _tool_prompt(tool, context_block, custom_rules, conversation="", language=""):
    """A small, isolated generator prompt for a student-facing learning tool.

    The wording lives in the activities/ sections of prompts.txt: one instruction and one JSON shape per
    activity type, plus the guard every type shares."""
    kind = tool["type"]
    policy = " | ".join(custom_rules)[:3000] or "(none)"
    # With grounding off and an empty knowledge base there are no excerpts to
    # confine the model to, and telling it to use only excerpts it wasn't given
    # is how you get a refusal instead of an activity.
    material = "\n\n%s\n%s" % (prompts.get("activities/material"), context_block) if context_block else ""
    return prompts.fill(
        "activities/request",
        framing=prompts.get("activities/framing_" + ("material" if context_block else "general")),
        guard=prompts.get("activities/guard"), topic=tool["topic"],
        instruction=prompts.get("activities/instructions/" + kind),
        shape=prompts.get("activities/shapes/" + kind),
        language=prompts.fill("activities/language", language=language) + "\n" if language else "",
        rules=prompts.get("activities/rules"), policy=policy, material=material,
        conversation="\n\n%s\n%s" % (prompts.get("activities/conversation/header"), conversation) if conversation else "")


# Keys models use for the correct option instead of "answer", in the order we
# trust them.
_ANSWER_KEYS = ("answer", "correct_index", "answer_index", "correct_answer", "correct")


def _resolve_answer_index(question, options):
    """Work out which option a model meant, or None if it cannot be trusted.

    This used to be one expression that fell back to 0, which is why every quiz
    looked like the answer was always A: a letter, a quoted digit, or the answer
    text — all of them ordinary model output for a multiple-choice key — failed
    the isinstance check and silently became the first option. A question whose
    key cannot be resolved is dropped now, because a confidently wrong answer
    key teaches the student the wrong thing.
    """
    raw = None
    for key in _ANSWER_KEYS:
        if question.get(key) is not None:
            raw = question[key]
            break
    if raw is None:
        return None
    # bool before int: isinstance(True, int) is True, so `"answer": true` used to
    # resolve to option B.
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if 0 <= raw < len(options) else None
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() and 0 <= raw < len(options) else None
    if not isinstance(raw, str):
        return None
    text = raw.strip().strip("()[].:# ").strip()
    if not text:
        return None
    if text.isdigit():
        index = int(text)
        return index if 0 <= index < len(options) else None
    if len(text) == 1 and text.isalpha():
        index = ord(text.lower()) - ord("a")
        return index if 0 <= index < len(options) else None
    # The answer's text rather than its position.
    wanted = " ".join(text.lower().split())
    for index, option in enumerate(options):
        if " ".join(str(option).lower().split()) == wanted:
            return index
    return None


def _shuffled_question(prompt, options, answer, explanation):
    """Randomize option order so the key is not positional.

    Even with the prompt asking for variety, a model at low temperature leans on
    whatever position its example showed. Shuffling here makes the distribution
    even regardless of what the model does.
    """
    pairs = list(enumerate(options))
    random.shuffle(pairs)
    return {
        "prompt": prompt,
        "options": [option for _, option in pairs],
        "answer": next(i for i, (original, _) in enumerate(pairs) if original == answer),
        "explanation": explanation,
    }


def _tool_json(text):
    clean = str(text or "").strip()
    # `\s`, not `\\s`: in a raw string the doubled backslash matches a literal
    # backslash followed by "s", so the fence was never stripped and every
    # ```json-wrapped reply — which is most of them, whatever the prompt asks —
    # failed json.loads and came back to the student as a 502.
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    try:
        return json.loads(clean)
    except (TypeError, ValueError):
        return None


def _decline_reason(text):
    """The model's own reason for building nothing, or ''."""
    result = _tool_json(text)
    reason = result.get("decline_reason") if isinstance(result, dict) else ""
    return " ".join(reason.split())[:240] if isinstance(reason, str) else ""


def _parse_tool_result(text, requested_type):
    """Validate a deliberately small JSON contract before the browser renders it."""
    result = _tool_json(text)
    if not isinstance(result, dict) or result.get("type") != requested_type:
        return None
    result["title"] = str(result.get("title") or requested_type.replace("_", " ").title())[:120]
    if requested_type == "quiz":
        questions = []
        for q in result.get("questions", [])[:5]:
            options = q.get("options") if isinstance(q, dict) else None
            if not isinstance(options, list) or len(options) < 2:
                continue
            # Truncate to four first, then resolve against what will actually be
            # stored: the old bound check used the untruncated list, so a fifth
            # option with answer 4 left an index past the end of the stored four
            # and the student's every click came back "Not quite."
            opts = [str(v)[:220] for v in options[:4]]
            answer = _resolve_answer_index(q, opts)
            if answer is None:
                logger.warning("Dropping quiz question with unusable answer key %r",
                               {k: q.get(k) for k in _ANSWER_KEYS if k in q})
                continue
            questions.append(_shuffled_question(str(q.get("prompt") or "")[:400], opts, answer,
                                                str(q.get("explanation") or "")[:500]))
        if not questions:
            return None
        result["questions"] = questions
    elif requested_type == "flashcards":
        cards = [{"front": str(c.get("front") or "")[:400], "back": str(c.get("back") or "")[:700]}
                 for c in result.get("cards", [])[:8] if isinstance(c, dict) and c.get("front") and c.get("back")]
        if not cards:
            return None
        result["cards"] = cards
    elif requested_type == "concept_map":
        nodes = [{"label": str(n.get("label") or "")[:120], "detail": str(n.get("detail") or "")[:300]}
                 for n in result.get("nodes", [])[:7] if isinstance(n, dict) and n.get("label")]
        if not nodes:
            return None
        links = [{"from": l.get("from"), "to": l.get("to"), "label": str(l.get("label") or "")[:120]}
                 for l in result.get("links", [])[:10] if isinstance(l, dict)
                 and isinstance(l.get("from"), int) and isinstance(l.get("to"), int)
                 and 0 <= l["from"] < len(nodes) and 0 <= l["to"] < len(nodes)]
        result["nodes"], result["links"] = nodes, links
    else:
        sections = [{"heading": str(s.get("heading") or "")[:140],
                     "points": [str(p)[:350] for p in s.get("points", [])[:5]]}
                    for s in result.get("sections", [])[:4] if isinstance(s, dict) and s.get("heading")]
        if not sections:
            return None
        result["sections"] = sections
    return result


def tool_failure(tool, raw_error, status, message=None):
    """A plain, internals-free message for a failed activity build.

    The raw reason is logged for the operator; the student only learns what to do next.
    """
    logger.warning("Activity build failed (%s on %r): %s", tool["type"], str(tool["topic"])[:120], str(raw_error)[:300])
    kind = tool["type"].replace("_", " ")
    if message:
        pass
    elif status == 422:
        message = "I couldn't find course material for that topic. Try a different topic."
    else:
        message = "I couldn't make that %s right now. Please try again in a moment." % kind
    return jsonify({"error": message, "failed": True}), status


@app.route('/tools/run', methods=['POST'])
@require_auth
def run_tool():
    """Generate a tool after the tutor's visible lead-in has been shown.

    A narrow call with a fixed UI contract, not the full tutor prompt. It does see
    the context /chat gives the tutor (recent turns, chat summary, class memory,
    preferences) so "quiz me on this" resolves to what the chat was about; student
    uploads stay out. The model may decline (`decline_reason`) rather than invent
    an item when there is nothing real to build it from.
    """
    if rate_limited(f"tool:{request.uid}", limit=TOOL_RATE_LIMIT, window=TOOL_RATE_WINDOW):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429
    data = request.get_json(silent=True) or {}
    class_id = str(data.get("class_id") or "").strip()
    chat_id = str(data.get("chat_id") or "").strip()
    tool = data.get("tool_request")
    role = get_role(request.uid)
    if not user_in_class(request.uid, class_id, role) or not valid_doc_id(chat_id):
        return jsonify({"error": "That learning tool is not available."}), 403
    chat_ref = _user_chats(request.uid).document(chat_id)
    chat_snap = chat_ref.get()
    if not chat_snap.exists:
        return jsonify({"error": "Conversation not found."}), 404
    settings = load_course_settings(class_id)
    # Re-validated server-side because the browser reaches this route directly
    # when a student taps a practice chip — the client never decides what is on.
    tool = validate_tool_request(tool, settings)
    if not tool:
        return jsonify({"error": "That toolkit is disabled for this course."}), 400
    try:
        # No `source` filter here, unlike /chat. Anything in the namespace is fair
        # material for an activity, and narrowing to uploaded documents is what made
        # this route unusable: migrate_legacy_custom_rules moves typed rules out of
        # Pinecone into Firestore, so a course taught from typed rules alone has no
        # document chunks at all and every request 422'd.
        def topic_matches(query):
            return pinecone_index.query(vector=embed(query), top_k=RETRIEVAL_TOP_K,
                                        include_metadata=True, namespace=class_id)["matches"]

        history = load_history(chat_ref.collection("Messages"))
        context_block, sources = _source_context(topic_matches(tool["topic"]))
        if not context_block and history:
            # A vague topic ("this", "it") embeds to nothing; add what was just discussed.
            recent = " ".join(t["parts"][0]["text"] for t in history if t["role"] == "user")[-400:]
            context_block, sources = _source_context(topic_matches(recent + " " + tool["topic"]))
        if not context_block:
            # "Quiz me on the whole course" or a phrasing the embedding misses:
            # retry from what the course outline says it covers, best-matching
            # outline topic first, then the course's own topic list.
            docs = load_course_manifest(class_id)
            topic_l = tool["topic"].lower()
            hinted = [t for t in manifest_topics(docs) if t.lower() in topic_l or topic_l in t.lower()]
            seeds = hinted[:1] or [", ".join(manifest_topics(docs, 12))]
            for seed in [s for s in seeds if s]:
                context_block, sources = _source_context(topic_matches(seed))
                if context_block:
                    break
        custom_rules = [r["text"] for r in load_custom_rules(class_id)]
        # Typed rules are the whole knowledge base for a course with no uploads, so
        # they stand in as material when retrieval is empty.
        if not context_block and custom_rules:
            context_block = "\n".join("[Course rule %d]\n%s" % (i, r)
                                      for i, r in enumerate(custom_rules, 1))
        if not context_block and settings["grounded_only"]:
            return tool_failure(tool, "No course material matched the topic \"%s\" and the course only "
                                      "allows answers from teacher material." % tool["topic"], 422)
        dev = is_dev_user(request.user)
        prefs = load_user_preferences(request.uid, dev)
        conversation = _tool_conversation(
            history, (chat_snap.to_dict() or {}).get("conversation_summary") or "",
            load_class_memory(request.uid, class_id, exclude_chat_id=chat_id,
                              profile=load_class_profile(request.uid, class_id)),
            preference_directive(prefs, dev))
        text = llm.generate(TOOL_MODEL, _tool_prompt(tool, context_block, custom_rules, conversation,
                                                     prefs.get("language", "")),
                            temperature=0.2, json_schema=ACTIVITY_SCHEMAS[tool["type"]],
                            think=data.get("think") is True,
                            max_tokens=8000).text
        declined = _decline_reason(text)
        if declined:
            return tool_failure(tool, "Model declined: " + declined, 422, declined)
        result = _parse_tool_result(text, tool["type"])
        if not result:
            return tool_failure(tool, "The model's output did not match the required %s format "
                                      "(missing or unusable fields)." % tool["type"], 502)
        if tool["type"] == "quiz":
            result["reveal_final_answers"] = bool(settings["reveal_final_answers"])
        kind_label = tool["type"].replace("_", " ")
        if sources:
            summary = "Used %d relevant course excerpt%s to create this %s on %s." % (
                len(sources), "s" if len(sources) != 1 else "", kind_label, tool["topic"])
        elif custom_rules:
            summary = "Built this %s on %s from the course rules your teacher wrote." % (
                kind_label, tool["topic"])
        else:
            summary = ("Built this %s on %s without course material, because strict grounding "
                       "is turned off for this course." % (kind_label, tool["topic"]))
        chat_ref.collection("Messages").add({"role": "teacher", "content": "", "tool": result,
            "tool_summary": summary, "sources": sources, "timestamp": firestore.SERVER_TIMESTAMP})
        return jsonify({"tool": result, "tool_summary": summary,
                        "sources": sources if role == "teacher" else []})
    except llm.EmbeddingsUnavailable as e:
        return retrieval_unavailable(e)
    except Exception as e:
        logger.exception("Could not create that learning activity.")
        return tool_failure(tool, "%s: %s" % (type(e).__name__, e), 500)


# ---------- teacher: knowledge management ----------

@app.route('/ingest', methods=['POST'])
@require_teacher
def ingest():
    """Save one or more custom teacher rules for a course.
    Body: { class_id, items: [{ id?, text }, ...] }  OR  { class_id, text, id? }

    These are prompt policy, not course material. Keeping them out of Pinecone
    prevents a rule such as "give hints first" from being retrieved as an answer
    to a student's subject question.
    """
    if rate_limited(f"teach:{request.uid}", TEACHER_RATE_LIMIT, TEACHER_RATE_WINDOW):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403

    items = data.get("items")
    if not items:
        if data.get("text"):
            items = [{"id": data.get("id"), "text": data["text"]}]
        else:
            return jsonify({"error": "No items or text provided"}), 400

    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400

    # Built before the transaction, so a retry re-applies the same additions
    # instead of minting fresh ids for them.
    additions, saved = {}, []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = " ".join(str(it.get("text") or "").split())[:1600]
        if not text:
            continue
        rid = str(it.get("id") or f"custom_{secrets.token_urlsafe(9)}")[:120]
        additions[rid] = {"id": rid, "text": text}
        saved.append({"id": rid, "status": "ok"})

    if not saved:
        return jsonify({"error": "No items or text provided"}), 400
    mutate_custom_rules(class_id, lambda current: {**current, **additions})
    return jsonify({"results": saved, "total_vectors": class_vector_count(class_id)})


def migrate_legacy_custom_rules(class_id):
    """Move pre-policy rules out of Pinecone, returning the remaining vector count.

    Older releases embedded typed rules without a ``source`` field. They need to
    stop being retrievable, but teachers should not have to re-enter them.
    """
    try:
        count = class_vector_count(class_id)
        if not count:
            return 0
        resp = pinecone_index.query(
            vector=[0.0] * EMBED_DIM, top_k=min(count, RULES_TOP_K),
            include_metadata=True, namespace=class_id,
            filter={"source": {"$exists": False}},
        )
        legacy = []
        for match in resp["matches"]:
            meta = match.get("metadata") or {}
            text = " ".join(str(meta.get("text") or "").split())[:1600]
            if text:
                legacy.append({"id": str(match["id"])[:120], "text": text})
        if not legacy:
            return count
        def absorb(current):
            for rule in legacy:
                current.setdefault(rule["id"], rule)
            return current

        mutate_custom_rules(class_id, absorb)
        pinecone_index.delete(ids=[r["id"] for r in legacy], namespace=class_id)
        return max(0, count - len(legacy))
    except Exception:
        logger.exception("Could not migrate legacy custom rules for course %s", class_id)
        return None


@app.route('/rules', methods=['POST'])
@require_teacher
def list_rules():
    """List custom rules (Firestore) and uploaded document chunks (Pinecone).

    Two filtered queries rather than one — see RULES_TOP_K. Typed rules carry only
    {"text": ...} in their metadata while file chunks also carry {"source": ...},
    so $exists on `source` separates them. Chunk entries omit `text`: the caller
    only groups them into one row per file by `source`, and shipping every chunk's
    body made this response many times larger to no end.

    `rules_truncated` / `docs_truncated` say the cap was hit and the lists are
    partial, so the page can admit it rather than quietly showing a short list.
    """
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403
    try:
        count = migrate_legacy_custom_rules(class_id)
        if count is None:
            count = class_vector_count(class_id)
        custom_rules = load_custom_rules(class_id)
        if count == 0:
            return jsonify({"rules": [{**r, "source": None} for r in custom_rules], "total_vectors": 0,
                            "rules_truncated": False, "docs_truncated": False})
        zero = [0.0] * EMBED_DIM

        def scan(cap, has_source):
            top_k = min(count, cap)
            if top_k <= 0:
                return []
            resp = pinecone_index.query(
                vector=zero,
                top_k=top_k,
                include_metadata=True,
                namespace=class_id,
                filter={"source": {"$exists": has_source}},
            )
            return resp["matches"]

        chunks = scan(DOC_CHUNK_TOP_K, True)

        rules = [{**r, "source": None} for r in custom_rules] + [
            {"id": m["id"], "source": m["metadata"].get("source")}
            for m in chunks
        ]
        # Documents uploaded before outlines existed get one built in the
        # background; the page shows summaries once it next loads.
        manifest_docs = load_course_manifest(class_id)
        if chunks and not manifest_docs and class_id not in _manifest_building:
            _manifest_building.add(class_id)
            Thread(target=rebuild_manifest, args=(class_id,), daemon=True).start()
        return jsonify({
            "rules": rules,
            "manifest": {d["id"]: {"summary": d["summary"], "topics": d["topics"], "added": d["added"]}
                         for d in manifest_docs},
            "total_vectors": count,
            # Compare against the cap, not against min(count, cap): `count` is the
            # namespace total across both kinds, so a class of 3 rules would fetch
            # top_k=3, match all 3, and report itself truncated.
            "rules_truncated": False,
            "docs_truncated": len(chunks) >= DOC_CHUNK_TOP_K,
        })
    except Exception as e:
        return server_error("Could not list rules.", e)


@app.route('/delete_rule', methods=['POST'])
@require_teacher
def delete_rule():
    """Delete rules by id from a class. Body: { class_id, id } or { class_id, ids }

    `ids` exists because deleting one document means deleting every chunk it was
    split into — a long PDF is hundreds of vectors, and one request per chunk meant
    hundreds of round-trips that each re-read the class from Firestore, with a
    failure partway through leaving orphaned chunks no page can see. Pinecone's
    delete already takes a list, so the batch costs the same as the single.
    """
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403

    # A whole document by its upload id: the server finds every chunk, so a file
    # bigger than one request's id cap (or the listing cap) still deletes fully.
    document = str(data.get("document") or "").strip()
    if document:
        if not re.fullmatch(r"file_\d+", document):
            return jsonify({"error": "Invalid document id"}), 400
        try:
            ids = []
            for page in pinecone_index.list(prefix=document + "_", namespace=class_id):
                ids.extend(page)
            for start in range(0, len(ids), DELETE_IDS_MAX):
                pinecone_index.delete(ids=ids[start:start + DELETE_IDS_MAX], namespace=class_id)
            remove_manifest_docs(class_id, ids or [document + "_0"])
            return jsonify({"status": "deleted", "deleted": len(ids)})
        except Exception as e:
            return server_error("Delete failed.", e)

    raw = data.get("ids")
    if raw is None:
        raw = [data.get("id")]
    if not isinstance(raw, list):
        return jsonify({"error": "ids must be a list"}), 400
    if not raw:
        return jsonify({"error": "No id provided"}), 400
    if len(raw) > DELETE_IDS_MAX:
        return jsonify({"error": f"Too many ids in one request (max {DELETE_IDS_MAX})."}), 400

    # JSON hands you whatever the caller typed: a list or dict where an id belongs
    # reaches Pinecone as a vector id and comes back as an unhandled 500.
    ids = []
    for rid in raw:
        if not isinstance(rid, str) or not rid.strip():
            return jsonify({"error": "No id provided"}), 400
        ids.append(rid.strip()[:512])   # Pinecone caps ids at 512 chars

    try:
        custom = {r["id"]: r for r in load_custom_rules(class_id)}
        custom_ids = [rid for rid in ids if rid in custom]
        vector_ids = [rid for rid in ids if rid not in custom]
        if custom_ids:
            doomed = set(custom_ids)
            mutate_custom_rules(
                class_id, lambda current: {rid: r for rid, r in current.items() if rid not in doomed}
            )
        if vector_ids:
            pinecone_index.delete(ids=vector_ids, namespace=class_id)
            remove_manifest_docs(class_id, vector_ids)
        return jsonify({"status": "deleted", "ids": ids, "deleted": len(ids)})
    except Exception as e:
        return server_error("Delete failed.", e)


# ---------- teacher: stats ----------

def _ts_seconds(ts):
    """Sortable seconds for a Firestore timestamp; missing/odd values sort first."""
    try:
        return ts.timestamp()
    except Exception:
        return 0.0


def _group_questions(rows):
    """Fold (ts, question, uid) rows into most-asked-first counts.

    Questions are keyed case- and whitespace-insensitively so the same question
    typed twice is one row, but the label keeps the first wording seen. `students`
    counts distinct askers, which is what makes a gap worth acting on — one
    student asking five times is not five students stuck.
    """
    out = {}
    for ts, question, uid in rows:
        text = " ".join(str(question or "").split())
        if not text:
            continue
        key = text.lower()
        row = out.get(key)
        if row is None:
            out[key] = row = {"question": text, "count": 0, "students": set(), "last_ts": ts}
        row["count"] += 1
        row["students"].add(uid)
        row["last_ts"] = max(row["last_ts"], ts)
    ranked = sorted(out.values(), key=lambda r: (r["count"], r["last_ts"]), reverse=True)
    return [
        {"question": r["question"], "count": r["count"], "students": len(r["students"])}
        for r in ranked
    ]


# Junk that retrieval fails on for reasons a teacher can't fix: a stray keypress,
# a greeting, abuse. Without this filter every one of them lands in Knowledge
# Gaps looking like uncovered course material.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_MIN_QUESTION_LETTERS = 8


def _is_real_question(text):
    """True when a gap row is worth showing a teacher.

    An empty retrieval means "nothing matched", and "j" or "fuck" match nothing
    just as surely as a genuinely uncovered topic does. Two words with at least
    _MIN_QUESTION_LETTERS characters between them, and no slur among them.
    """
    words = _WORD_RE.findall(str(text or "").lower())
    if len(words) < 2 or sum(len(w) for w in words) < _MIN_QUESTION_LETTERS:
        return False
    return not profanity.is_profane(text)


_LEARNING_SIGNAL_RE = re.compile(
    r"\b(i(?:'m| am)?\s+(?:confused|stuck|lost|unsure)|i\s+don'?t\s+understand|"
    r"not\s+sure|why\s+(?:is|does|do|did|can)|can\s+you\s+explain|help\s+me\s+understand)\b",
    re.IGNORECASE,
)
_SAFETY_CONCERN_RE = re.compile(
    r"\b(kill\s+(?:myself|yourself|him|her|them)|suicide|self[- ]?harm|hurt\s+(?:myself|you)|"
    r"threat(?:en|ening)?|harass(?:ment|ing)?|bully(?:ing)?)\b",
    re.IGNORECASE,
)


def _issue_kind(text):
    """Separate academic evidence from conduct/safety signals.

    A retrieval miss alone is a material-coverage gap, not proof that a student
    misunderstands something. Learning signals require explicit uncertainty or
    confusion. Profanity/abuse is routed to concerns and never academic gaps.
    """
    if profanity.is_profane(text) or _SAFETY_CONCERN_RE.search(str(text or "")):
        return "behavioral"
    if _is_real_question(text) and _LEARNING_SIGNAL_RE.search(str(text or "")):
        return "academic"
    return None


def _is_material_gap_question(text):
    return _is_real_question(text) and _issue_kind(text) != "behavioral"


# Bump when the shape of the per-chat summary changes; chats stamped with an
# older (or missing) version fall back to the slow re-read-every-message path.
STAT_SUMMARY_VERSION = 2
_SUMMARY_CONTEXT_CHARS = 600   # how much of a conversation feeds categorization
_SUMMARY_OPENING_CHARS = 300   # the question shown in the teacher's feed
_SUMMARY_MAX_GAPS = 25         # /stats only ever shows the newest 25 anyway


def summarize_exchange(prev, is_new, question, answer, gap_topics=()):
    """Fields to merge into a chat doc so /stats never has to open its messages.

    `prev` is the chat document as it was before this exchange. Everything here
    is derived from what /chat already has in hand, so maintaining it adds no
    reads and no extra writes — it rides along on the chat doc update.
    """
    summary = {"stat_v": STAT_SUMMARY_VERSION}
    issue = _issue_kind(question)

    # Abuse is recorded under `concerns` below and nowhere else. `opening` and
    # `context` are what /stats turns into Recent questions, Most repeated and
    # the model-generated topic list, and what the tutor recalls in later
    # prompts — none of which should ever echo a slur back at anyone.
    if issue != "behavioral":
        if is_new:
            summary["opening"] = question[:_SUMMARY_OPENING_CHARS]
        elif not prev.get("opening"):
            # A conversation that predates summaries: its real opening question
            # is gone from this request, but `title` was cut from it, so prefer
            # that over mislabelling the newest question as the one that started
            # the chat.
            summary["opening"] = (prev.get("title") or question)[:_SUMMARY_OPENING_CHARS]

    # Categorization reads the start of a conversation, so stop growing the blob
    # once we have enough of it.
    context = "" if is_new else (prev.get("context") or "")
    if issue != "behavioral" and len(context) < _SUMMARY_CONTEXT_CHARS:
        summary["context"] = (context + " " + question).strip()[:_SUMMARY_CONTEXT_CHARS]

    # Material gaps are only what the tutor itself flagged with mark_gap, never
    # inferred from an empty retrieval.
    topics = [str(t)[:_SUMMARY_OPENING_CHARS] for t in gap_topics if _is_material_gap_question(t)]
    if topics:
        gaps = [] if is_new else list(prev.get("material_gaps") or prev.get("gaps") or [])
        gaps.extend(t for t in topics if t not in gaps)
        summary["material_gaps"] = gaps[-_SUMMARY_MAX_GAPS:]

    if issue == "academic":
        gaps = [] if is_new else list(prev.get("learning_gaps") or [])
        gaps.append(question[:_SUMMARY_OPENING_CHARS])
        summary["learning_gaps"] = gaps[-_SUMMARY_MAX_GAPS:]
    elif issue == "behavioral":
        concerns = [] if is_new else list(prev.get("concerns") or [])
        concerns.append(question[:_SUMMARY_OPENING_CHARS])
        summary["concerns"] = concerns[-_SUMMARY_MAX_GAPS:]

    return summary


_category_cache = {}
_CATEGORY_CACHE_MAX = 5000


def _category_key(text):
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def categorize_conversations(convos):
    """Tag each whole conversation with ONE short topic category.

    `convos` is a list of strings, each the representative text of one
    conversation (opening question plus a little follow-up context). Labels are
    remembered per conversation text, so reloading the page or switching the date
    range only asks the model about conversations it has not seen yet.
    """
    if not convos:
        return []
    keys = [_category_key(c) for c in convos]
    labels = {}
    fresh = []
    for key, text in zip(keys, convos):
        if key in _category_cache:
            labels[key] = _category_cache[key]
        elif key not in labels and text not in fresh:
            fresh.append(text)
    if fresh:
        for text, label in zip(fresh, _categorize_uncached(fresh)):
            key = _category_key(text)
            labels[key] = label
            if label != "Uncategorized":   # a failure should be retried, not remembered
                if len(_category_cache) >= _CATEGORY_CACHE_MAX:
                    _category_cache.clear()
                _category_cache[key] = label
    return [labels.get(k, "Uncategorized") for k in keys]


def _categorize_uncached(convos):
    numbered = "\n".join(f"{i+1}. {c}" for i, c in enumerate(convos))
    prompt = prompts.fill("background/categorize_conversations", conversations=numbered)
    try:
        text = llm.generate(CHAT_MODEL, prompt,
                            json_schema={"type": "array", "items": {"type": "string"}}, think=False).text
        text = text.replace("```json", "").replace("```", "").strip()
        cats = json.loads(text)
        if isinstance(cats, list) and len(cats) == len(convos):
            # Truncate: the category is model output derived from student text, so
            # a prompt-injected "category" shouldn't be able to be a wall of text
            # (or a payload) in the teacher's dashboard.
            return [str(c)[:40] for c in cats]
    except Exception:
        pass
    return ["Uncategorized"] * len(convos)


# --- teacher: how an individual student is getting on ---
# /stats answers "how is the class doing"; this answers "how is this one student
# doing", from the same profile the tutor uses to pitch its replies. What comes
# back is deliberately prose and counts, never a score: a teacher should read
# guidance on how to help a child, not a number to rank them by.

@app.route('/roster', methods=['POST'])
@require_teacher
def roster():
    """The members of one class the teacher owns. Body: { class_id }"""
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403
    try:
        members = [
            {"uid": m.id, "email": str((m.to_dict() or {}).get("email") or "")[:200]}
            for m in db.collection("Classes").document(class_id)
            .collection("Members").limit(ROSTER_MAX).stream()
        ]
    except Exception:
        logger.exception("Could not read the class roster.")
        return jsonify({"error": "Could not load the class list."}), 500
    members.sort(key=lambda m: m["email"].lower())
    return jsonify({"members": members})


@app.route('/student-profile', methods=['POST'])
@require_teacher
def student_profile_view():
    """One student's profile for one class. Body: { class_id, student_uid }

    Two gates, both required: the teacher must own the class, and the student must
    be a member of it. Ownership alone is not enough — without the membership
    check a teacher could name any uid in the system and read a profile built in
    somebody else's classroom.
    """
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    student_uid = (data.get("student_uid") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403
    if not valid_doc_id(student_uid) or not user_in_class(student_uid, class_id, "student"):
        return jsonify({"error": "That student isn't in this course."}), 403

    prof = load_class_profile(student_uid, class_id)
    return jsonify({
        "messages": int(prof.get("msgs") or 0),
        # "" until there is enough evidence to say anything honest; the UI shows
        # its own "not enough yet" line rather than inventing a characterisation.
        "summary": student_profile.describe(prof),
        "sticking_points": [
            {"topic": g, "times": n} for g, n in student_profile.sticking_points(prof)
        ],
        "min_messages": student_profile.PROFILE_MIN_MSGS,
    })


@app.route('/stats', methods=['POST'])
@require_teacher
def stats():
    """Per-class analytics from Firestore chat logs. Body: { class_id, limit? }

    Scoped to one class the teacher owns: we walk the class's members and read
    each member's conversations for this class. This keeps a teacher's analytics
    to their own class (no cross-teacher leakage) and needs no special index.
    """
    # Every call here is a model generation plus a walk of the class's chats —
    # the most expensive thing a teacher account can trigger in a loop.
    if rate_limited(f"teach:{request.uid}", TEACHER_RATE_LIMIT, TEACHER_RATE_WINDOW):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403
    try:
        limit = int(data.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 1000))
    # Optional date window. 0/absent means "everything", which is what the
    # dashboard's widest range asks for. Conversations carry a last-activity
    # timestamp already, so this costs a comparison, not another read.
    try:
        days = int(data.get("days", 0))
    except (TypeError, ValueError):
        days = 0
    cutoff = (time.time() - days * 86400) if days > 0 else None

    try:
        # One conversation = one chat doc. A follow-up answer in the same chat must
        # NOT count as another question/topic — the whole conversation counts once.
        convos = []            # {opening, context, last_ts, uid, gapped} per conversation
        gaps = []              # material coverage gaps
        learning = []          # explicit confusion/uncertainty evidence
        concerns = []          # conduct/safety signals, never academic gaps
        session_count = 0
        legacy_chats = 0       # chats still needing the slow per-message read
        member_uids = [
            m.id for m in
            db.collection("Classes").document(class_id).collection("Members").stream()
        ]
        for uid in member_uids:
            chats = (
                db.collection("Users").document(uid).collection("Chats")
                .where("class_id", "==", class_id).stream()
            )
            for chat in chats:
                session_count += 1
                d = chat.to_dict() or {}

                if d.get("stat_v") == STAT_SUMMARY_VERSION:
                    # Fast path: /chat already summarized this conversation, so
                    # the chat document alone answers everything below.
                    last_ts = _ts_seconds(d.get("last_active"))
                    if cutoff is not None and last_ts < cutoff:
                        continue
                    # Re-filtered on read: chats summarized before _is_real_question
                    # existed still carry keysmashes and abuse in their gap list.
                    chat_gaps = [q for q in (d.get("material_gaps") or []) if _is_material_gap_question(q)]
                    chat_learning = [q for q in (d.get("learning_gaps") or []) if _issue_kind(q) == "academic"]
                    chat_concerns = [q for q in (d.get("concerns") or []) if _issue_kind(q) == "behavioral"]
                    # Concerns are collected before the opening is judged, so a
                    # conversation that is *only* abuse still reaches the teacher
                    # — under Behavioral concerns, which is the panel for it.
                    concerns.extend((last_ts, q, uid) for q in chat_concerns)

                    opening = (d.get("opening") or d.get("title") or "").strip()
                    # Chats written before the block existed still have a swear
                    # stored as their opening. Dropping them here is what keeps
                    # the existing data out of Recent questions, Most repeated
                    # and the topic list without a migration.
                    if not opening or profanity.is_profane(opening):
                        continue
                    convos.append({
                        "opening": opening,
                        "context": (d.get("context") or opening),
                        "last_ts": last_ts,
                        "uid": uid,
                        "gapped": bool(chat_gaps),
                    })
                    # Per-gap timestamps aren't stored; the chat's last activity
                    # is close enough for "most recent first" ordering.
                    gaps.extend((last_ts, q, uid) for q in chat_gaps)
                    learning.extend((last_ts, q, uid) for q in chat_learning)
                    continue

                # Legacy path, for conversations written before summaries existed:
                # replay the messages in timestamp order so each tutor answer pairs
                # with the question right before it.
                legacy_chats += 1
                msgs = []
                chat_gaps = []          # (ts, topic) the tutor flagged with mark_gap
                chat_learning = []
                chat_concerns = []
                for msg in chat.reference.collection("Messages").order_by("timestamp").stream():
                    m = msg.to_dict() or {}
                    role = m.get("role")
                    content = (m.get("content") or "").strip()
                    if role == "student":
                        if content:
                            msgs.append((m.get("timestamp"), content))
                            issue = _issue_kind(content)
                            if issue == "academic":
                                chat_learning.append((_ts_seconds(m.get("timestamp")), content))
                            elif issue == "behavioral":
                                chat_concerns.append((_ts_seconds(m.get("timestamp")), content))
                    elif role == "teacher":
                        chat_gaps.extend((_ts_seconds(m.get("timestamp")), str(t)) for t in (m.get("gap_topics") or [])
                                         if _is_material_gap_question(t))
                if msgs:
                    msgs.sort(key=lambda x: _ts_seconds(x[0]))
                    last_ts = max(_ts_seconds(t) for t, _ in msgs)
                    if cutoff is not None and last_ts < cutoff:
                        continue
                    gaps.extend((ts, q, uid) for ts, q in chat_gaps)
                    learning.extend((ts, q, uid) for ts, q in chat_learning)
                    concerns.extend((ts, q, uid) for ts, q in chat_concerns)
                    # Same rule as the fast path: flagged messages are already
                    # counted above, and must not also appear as questions. The
                    # conversation drops out entirely when abuse is all it was.
                    clean = [(t, c) for t, c in msgs if not profanity.is_profane(c)]
                    if not clean:
                        continue
                    convos.append({
                        "opening": clean[0][1],                         # the question that started it
                        "context": " ".join(c for _, c in clean[:4])[:_SUMMARY_CONTEXT_CHARS],
                        "last_ts": last_ts,
                        "uid": uid,
                        "gapped": bool(chat_gaps),
                    })

        if legacy_chats:
            logger.info(
                "stats(%s): %d/%d conversations still read message-by-message "
                "(pre-summary chats).", class_id, legacy_chats, session_count
            )

        # Keep the most recent `limit` conversations (caps categorization cost).
        convos.sort(key=lambda c: c["last_ts"])
        convos = convos[-limit:]
        total_questions = len(convos)

        categories = categorize_conversations([c["context"] for c in convos])
        # The model words the same topic slightly differently between calls
        # ("Photosynthesis" / "photosynthesis "), so merge on a normalised key and
        # show the first spelling seen.
        cat_counts, cat_names = {}, {}
        for c in categories:
            key = " ".join(c.lower().split())
            cat_names.setdefault(key, c.strip())
            cat_counts[key] = cat_counts.get(key, 0) + 1

        sorted_cats = sorted(((cat_names[k], v) for k, v in cat_counts.items()),
                             key=lambda x: x[1], reverse=True)

        # Openings a class keeps coming back to. Grouped case/whitespace-insensitively
        # so "What is osmosis?" and "what is osmosis" are one row, labelled with the
        # first wording seen.
        repeats = _group_questions(
            (c["last_ts"], c["opening"], c["uid"]) for c in convos
        )[:8]

        # Gaps, most-asked first — the dashboard leads with them, so frequency
        # matters more than recency here.
        unanswered = _group_questions(gaps)[:25]
        learning_gaps = _group_questions(learning)[:25]
        behavior_concerns = _group_questions(concerns)[:25]

        return jsonify({
            "total_questions": total_questions,
            "total_sessions": session_count,   # every chat ever; not date-windowed
            "students_total": len(member_uids),
            "students_active": len({c["uid"] for c in convos}),
            "grounded_conversations": sum(1 for c in convos if not c["gapped"]),
            "categories": [{"name": k, "count": v} for k, v in sorted_cats],
            "repeats": repeats,
            "unanswered": unanswered,
            "unanswered_count": len(gaps),
            "learning_gaps": learning_gaps,
            "learning_gap_count": len(learning),
            "behavior_concerns": behavior_concerns,
            "behavior_concern_count": len(concerns),
        })
    except Exception as e:
        return server_error("Stats failed.", e)


class ExtractError(Exception):
    """An upload couldn't be turned into text. The message is safe to show."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def extract_file_text(file, max_chars=MAX_EXTRACT_CHARS):
    """Plain text out of an uploaded .pdf/.docx/.txt/.md/.csv → (text, truncated).

    Shared by the teacher upload (chunked into Pinecone) and the student upload
    (kept whole for review), so the decompression guards here cover both paths —
    a student's zip bomb costs the same 512 MiB instance as a teacher's.
    """
    fname = (file.filename or "").lower()
    try:
        if fname.endswith(".pdf"):
            if pypdf is None:
                raise ExtractError("pypdf not installed", status=500)
            reader = pypdf.PdfReader(file.stream)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif fname.endswith(".docx"):
            if DocxDocument is None:
                raise ExtractError("python-docx not installed", status=500)
            # Check the archive's declared uncompressed size before handing it to
            # python-docx, which would expand the whole thing into memory first.
            # Reading the central directory decompresses nothing.
            # ponytail: trusts the declared sizes, which a hand-built zip can
            # understate; a hard ceiling would need a counting decompress stream.
            try:
                declared = sum(i.file_size for i in zipfile.ZipFile(file.stream).infolist())
            except zipfile.BadZipFile:
                raise ExtractError("That .docx isn't a readable Word file.")
            if declared > MAX_EXTRACT_BYTES:
                raise ExtractError("That document expands to far too much content.", status=413)
            file.stream.seek(0)
            doc = DocxDocument(file.stream)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif fname.endswith((".txt", ".md", ".csv")):
            text = file.read().decode("utf-8", errors="ignore")
        else:
            raise ExtractError("Unsupported file type. Use .pdf, .docx, .txt, .md, or .csv")
    except ExtractError:
        raise
    except Exception as e:
        logger.exception("Could not read uploaded file %s", file.filename)
        raise ExtractError(
            "Could not read that file. Is it a valid, non-encrypted document?"
        ) from e

    if not text.strip():
        raise ExtractError("No text could be extracted from the file")

    # Every format converges here, so one cap bounds the cost for all of them —
    # however the text got large.
    truncated = len(text) > max_chars
    if truncated:
        logger.info("Truncated %s from %d to %d characters.", file.filename, len(text), max_chars)
        text = text[:max_chars]
    return text, truncated


@app.route('/upload', methods=['POST'])
@require_teacher
def upload():
    if rate_limited(f"teach:{request.uid}", TEACHER_RATE_LIMIT, TEACHER_RATE_WINDOW):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429
    class_id = (request.form.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown course, or you don't own it."}), 403

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    try:
        text, truncated = extract_file_text(file)
    except ExtractError as e:
        return jsonify({"error": str(e)}), e.status

    chunks = chunk_text(text)
    # Drop the source text (and its normalized copy inside chunk_text) before we
    # start embedding — on a small instance these copies add up fast.
    del text
    gc.collect()
    if not chunks:
        return jsonify({"error": "File produced no usable text chunks"}), 400

    # Embed many chunks per request and upsert a batch at a time. Batching the
    # embeddings is what keeps a multi-chunk document fast enough to finish inside
    # the host's request window (sequential per-chunk calls were timing out → 503),
    # and processing a batch at a time keeps peak memory flat for large files.
    # llm.embed_documents splits into requests of at most 16 inputs for the server.
    BATCH = 48
    source = file.filename
    base_id = f"file_{int(time.time() * 1000)}"
    stored = 0

    try:
        for start in range(0, len(chunks), BATCH):
            group = chunks[start:start + BATCH]
            vectors = llm.embed_documents(group, source)
            pending = [
                {"id": f"{base_id}_{start + j}", "values": vectors[j],
                 "metadata": {"text": group[j], "source": source,
                              "chunk": start + j, "kind": "course_document",
                              "embed_model": llm.embed_model()}}
                for j in range(len(group))
            ]
            pinecone_index.upsert(vectors=pending, namespace=class_id)
            stored += len(pending)
            del vectors, pending, group
            gc.collect()
    except llm.EmbeddingsUnavailable as e:
        return retrieval_unavailable(e)
    except Exception as e:
        return server_error("Upload failed while indexing the document.", e)

    gc.collect()
    # Outline entry so the tutor knows what this document covers. Best effort:
    # summarize_document falls back to the file name, and a failed write only
    # means this document is missing from the outline until the next rebuild.
    try:
        save_manifest_entry(class_id, base_id, summarize_document(source, chunks))
    except Exception:
        logger.exception("Could not save the course outline entry for %s", source)
    result = {"chunks": len(chunks), "stored": stored, "total_vectors": class_vector_count(class_id)}
    if truncated:
        # Say so rather than silently indexing half a document — a teacher who
        # thinks all of it landed would trust gaps that aren't really gaps.
        result["warning"] = (
            f"Only the first {MAX_EXTRACT_CHARS:,} characters were indexed. "
            "Split the document and upload it in parts to add the rest."
        )
    return jsonify(result)


# ---------- student: their own assignment / rubric ----------
# These files are never embedded and never touch a course namespace. They are one
# student's work, visible only to that student's tutor: dropping them into the
# course knowledge base would let any student rewrite the material every other
# student is answered from, which is the one thing the namespace-per-class design
# exists to prevent. They are keyed by class_id and chat_id, so they stay within
# the conversation where the student attached them.

def _user_files(uid):
    return db.collection("Users").document(uid).collection("Files")


@app.route('/student/files', methods=['GET'])
@require_auth
def list_student_files():
    """Uploads attached to one conversation. Text is deliberately omitted."""
    class_id = (request.args.get("class_id") or "").strip()
    chat_id = (request.args.get("chat_id") or "").strip()
    if not user_in_class(request.uid, class_id, get_role(request.uid)):
        return jsonify({"error": "Join this course first."}), 403
    if not valid_doc_id(chat_id):
        return jsonify({"error": "Choose a conversation first."}), 400
    try:
        docs = list(
            _user_files(request.uid).where("class_id", "==", class_id)
            .where("chat_id", "==", chat_id).stream()
        )
    except Exception as e:
        return server_error("Could not list your files.", e)
    files = []
    for d in docs:
        m = d.to_dict() or {}
        files.append({"id": d.id, "name": m.get("name") or "untitled",
                      "kind": m.get("kind") or "file"})
    return jsonify({"files": files, "max": STUDENT_DOCS_MAX})


@app.route('/student/files', methods=['POST'])
@require_auth
def add_student_file():
    """Upload an assignment, or the rubric it will be marked against."""
    if rate_limited(f"stufile:{request.uid}", STUDENT_UPLOAD_RATE_LIMIT, STUDENT_UPLOAD_RATE_WINDOW):
        return jsonify({"error": "Too many uploads. Please wait a few minutes."}), 429
    class_id = (request.form.get("class_id") or "").strip()
    chat_id = (request.form.get("chat_id") or "").strip()
    if not user_in_class(request.uid, class_id, get_role(request.uid)):
        return jsonify({"error": "Join this course first."}), 403
    if not valid_doc_id(chat_id):
        return jsonify({"error": "Choose a conversation first."}), 400

    # The tutor is not a research assistant over whatever a student uploads: the
    # only things allowed in are work to be marked and the criteria to mark it
    # against. Asking for the kind is what keeps the prompt able to say which.
    kind = (request.form.get("kind") or "").strip().lower()
    if kind not in STUDENT_DOC_KINDS:
        return jsonify({"error": "Say whether this is an assignment or a rubric."}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    try:
        existing = list(
            _user_files(request.uid).where("class_id", "==", class_id)
            .where("chat_id", "==", chat_id).stream()
        )
    except Exception as e:
        return server_error("Could not check your existing files.", e)
    if len(existing) >= STUDENT_DOCS_MAX:
        return jsonify({"error": f"You can keep {STUDENT_DOCS_MAX} files per course conversation. "
                                 "Remove one first."}), 409

    try:
        text, truncated = extract_file_text(file, STUDENT_DOC_CHARS)
    except ExtractError as e:
        return jsonify({"error": str(e)}), e.status

    # Last, and only once everything cheap has passed: a request that would be
    # refused anyway never pays for a model call, and a refusal here writes
    # nothing to Firestore.
    detected = classify_student_upload(text)
    if detected == "material":
        return jsonify({"error": "This looks like course material. Only your own work, or the "
                                 "rubric it is marked against, belongs here — course material "
                                 "is your teacher's to add."}), 400
    if detected and detected != kind:
        return jsonify({"error": f"This reads like a {detected}, not {'an' if kind[0] in 'aeiou' else 'a'} "
                                 f"{kind}. Attach it with the {detected} button instead."}), 400

    name = file.filename[:200]
    doc = _user_files(request.uid).document()
    doc.set({
        "class_id": class_id,
        "chat_id": chat_id,
        "kind": kind,
        "name": name,
        "text": text,
        "summary": summarize_student_doc(text),
        "created_at": firestore.SERVER_TIMESTAMP,
    })
    result = {"id": doc.id, "name": name, "kind": kind}
    if truncated:
        # Same reason /upload says so: a student who thinks the whole essay went
        # in would trust feedback on the half that did.
        result["warning"] = (f"Only the first {STUDENT_DOC_CHARS:,} characters were kept. "
                             "Upload a shorter extract if the rest matters.")
    return jsonify(result)


@app.route('/student/files/<file_id>', methods=['DELETE'])
@require_auth
def delete_student_file(file_id):
    """Remove one of the signed-in student's uploads."""
    if not valid_doc_id(file_id):
        return jsonify({"error": "File not found"}), 404
    ref = _user_files(request.uid).document(file_id)
    if not ref.get().exists:
        return jsonify({"error": "File not found"}), 404
    try:
        ref.delete()
    except Exception as e:
        return server_error("Could not delete that file.", e)
    return jsonify({"status": "deleted", "id": file_id})


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


@app.route('/status/public', methods=['GET'])
def status_public():
    """Public, intentionally coarse service health. Safe for unauthenticated use."""
    return jsonify(public_status_snapshot())


@app.route('/status/deployment', methods=['GET'])
@require_developer
def status_deployment():
    """Cloud deployment details are limited to verified developer accounts."""
    return jsonify(deployment_status_snapshot())


if __name__ == '__main__':
    # Bind to 0.0.0.0 and the host-provided PORT (Cloud Run injects this; default
    # 5000 locally). Debug is off unless FLASK_DEBUG is explicitly set (top of file).
    # NOTE: this dev server is only a fallback — production runs waitress via the
    # Dockerfile's CMD.
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
