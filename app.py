import os
import gc
import json
import time
import hmac
import secrets
import logging
import zipfile
from functools import wraps
from collections import deque
from threading import Lock
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from google import genai
from google.genai import types
import firebase_admin
from firebase_admin import credentials, firestore, auth as fb_auth
from pinecone import Pinecone
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

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
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

# Refuse to start with a missing or well-known default teacher code — otherwise
# anyone could self-register as a teacher and read all chat logs / edit the
# knowledge base.
if not TEACHER_SIGNUP_CODE or TEACHER_SIGNUP_CODE.lower() in ("changeme", "password", "admin", "skibidi"):
    raise SystemExit(
        "Refusing to start: set a strong TEACHER_SIGNUP_CODE in your .env "
        "(it is missing or set to an insecure default)."
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
# Each Q&A is 2 messages, so 20 ≈ the last 10 exchanges. Capped to bound tokens/latency.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "20"))
# Hard cap on how many messages a single conversation will return, so one very
# long chat can't turn into an unbounded Firestore read + response body.
MAX_MESSAGES_RETURNED = int(os.getenv("MAX_MESSAGES_RETURNED", "500"))

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
EMBED_DIM = 768
# Chat/generation model. flash-lite is cheaper and has higher throughput than
# 2.5-flash, so it scales better for a class of users. Override via env if needed.
CHAT_MODEL = os.getenv("CHAT_MODEL", "gemini-2.5-flash-lite")

# Retrieval tuning for the tutor. RETRIEVAL_TOP_K is how many knowledge chunks we
# pull per question (more context generally = fuller answers). RETRIEVAL_MIN_SCORE
# is a cosine-similarity floor: chunks below it are dropped as not-really-related,
# so an off-topic question ends up with empty context and a truthful "not in my
# knowledge base" answer instead of being force-fed the least-bad matches. The
# index uses cosine; with Gemini embeddings on-topic chunks score ~0.52+ and
# unrelated ones ~0.48–0.51, so 0.5 is a sensible default. Tune per your material.
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))
RETRIEVAL_MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.5"))

# Directory this file lives in — used to serve the front-end HTML pages.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
    return resp


client = genai.Client(api_key=GEMINI_KEY)

pc = Pinecone(api_key=PINE_KEY)
pinecone_index = pc.Index(INDEX_NAME)

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
    """Return a 768-dim embedding for the given text."""
    result = client.models.embed_content(
        model="models/gemini-embedding-001",
        contents=text,
        config={"output_dimensionality": EMBED_DIM},
    )
    return result.embeddings[0].values


def embed_batch(texts):
    """Embed many texts in a single request and return their vectors in order.

    Uploads used to embed one chunk at a time — dozens of sequential round-trips
    for a real document, slow enough that the host could kill the request (a 503).
    Batching collapses that into a handful of calls, so even large files finish
    quickly."""
    result = client.models.embed_content(
        model="models/gemini-embedding-001",
        contents=texts,
        config={"output_dimensionality": EMBED_DIM},
    )
    return [e.values for e in result.embeddings]


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
    out = {"id": doc.id, "name": d.get("name") or "Untitled class"}
    if include_code:
        out["join_code"] = d.get("join_code")
    return out


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
    """Return the most recent stored messages as Gemini 'contents' turns
    (oldest first) so the model can see the conversation so far.

    Roles map student->'user', teacher->'model'. Any leading model turns are
    dropped because Gemini expects the conversation to start with a user turn.
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
        role = "user" if m.get("role") == "student" else "model"
        if not contents and role == "model":
            continue  # skip any leading model turn
        contents.append({"role": role, "parts": [{"text": text}]})
    return contents


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
@app.route('/landing.html')
def page_landing():
    return send_from_directory(BASE_DIR, 'landing.html')


@app.route('/student.html')
def page_student():
    return send_from_directory(BASE_DIR, 'student.html')


@app.route('/login.html')
def page_login():
    return send_from_directory(BASE_DIR, 'login.html')


@app.route('/auth.js')
def auth_js():
    return send_from_directory(BASE_DIR, 'auth.js')


@app.route('/teacherknowledge.html')
def page_knowledge():
    return send_from_directory(BASE_DIR, 'teacherknowledge.html')


@app.route('/teacherstats.html')
def page_stats():
    return send_from_directory(BASE_DIR, 'teacherstats.html')


@app.route('/theme.css')
def theme_css():
    return send_from_directory(BASE_DIR, 'theme.css')


@app.route('/theme.js')
def theme_js():
    return send_from_directory(BASE_DIR, 'theme.js')


@app.route('/transition.css')
def transition_css():
    return send_from_directory(BASE_DIR, 'transition.css')


@app.route('/transition.js')
def transition_js():
    return send_from_directory(BASE_DIR, 'transition.js')


# ---------- auth ----------

@app.route('/auth/config', methods=['GET'])
def auth_config():
    """Public Firebase Web SDK config the browser needs to sign users in.
    apiKey is not a secret (it ships in every Firebase web app)."""
    return jsonify({
        "apiKey": FIREBASE_WEB_API_KEY,
        "authDomain": FIREBASE_AUTH_DOMAIN,
        "projectId": FIREBASE_PROJECT_ID,
    })


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
    })


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
        return jsonify({"error": "Class name is required"}), 400
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
        return server_error("Could not create the class.", e)


@app.route('/classes/join', methods=['POST'])
@require_auth
def join_class():
    """Join a class by its code. Body: { join_code }"""
    # Throttle before touching Firestore: this is the one endpoint where a
    # wrong answer is still informative (it tells you a code doesn't exist), so
    # it's the one an attacker would spray to find live classes.
    if rate_limited(f"join:{request.uid}", JOIN_RATE_LIMIT, JOIN_RATE_WINDOW):
        return jsonify({"error": "Too many join attempts. Please wait a few minutes."}), 429

    data = request.get_json(silent=True) or {}
    code = (data.get("join_code") or "").strip().upper()
    if not code:
        return jsonify({"error": "A join code is required"}), 400
    try:
        hit = list(db.collection("Classes").where("join_code", "==", code).limit(1).stream())
        if not hit:
            return jsonify({"error": "No class found for that code."}), 404
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
        return server_error("Could not join that class.", e)


@app.route('/classes/<class_id>', methods=['DELETE'])
@require_teacher
def delete_class(class_id):
    """Delete a class the teacher owns: its rules (Pinecone namespace), member
    records, and the class document."""
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Class not found"}), 404
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
        return server_error("Could not delete the class.", e)


# ---------- student: chat (cloud-synced per user) ----------

def _user_chats(uid):
    return db.collection("Users").document(uid).collection("Chats")


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
    if not chat_ref.get().exists:
        return jsonify({"error": "Chat not found"}), 404
    # Bounded so one runaway conversation can't turn into an unbounded read.
    docs = list(
        chat_ref.collection("Messages").order_by("timestamp").limit(MAX_MESSAGES_RETURNED).stream()
    )
    messages = []
    for d in docs:
        m = d.to_dict() or {}
        messages.append({
            "role": m.get("role"),
            "content": m.get("content", ""),
            "rules": m.get("rules") or [],
        })
    return jsonify({"messages": messages})


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
    if not user_in_class(request.uid, class_id, get_role(request.uid)):
        return jsonify({"error": "Join this class before using the tutor."}), 403

    if not user_message or not isinstance(user_message, str) or not user_message.strip():
        return jsonify({"error": "No message provided"}), 400
    user_message = user_message.strip()
    if len(user_message) > MAX_MESSAGE_CHARS:
        return jsonify({"error": "Message is too long."}), 413

    try:
        question_embedding = embed(user_message)

        pinecone_resp = pinecone_index.query(
            vector=question_embedding,
            top_k=RETRIEVAL_TOP_K,
            include_metadata=True,
            namespace=class_id,
        )

        # Keep only chunks similar enough to the question. If nothing clears the
        # bar, context is empty and the system prompt tells the tutor to admit
        # it's not in the knowledge base — which is exactly what the gap
        # analytics later count as an unanswered question.
        teacher_rules = [
            m['metadata']['text'] for m in pinecone_resp['matches']
            if 'metadata' in m and m.get('score', 0) >= RETRIEVAL_MIN_SCORE
        ]
        context_block = "\n".join(teacher_rules)

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

        # Replay the recent conversation so the AI remembers earlier turns.
        history = load_history(msgs_ref)

        if not teacher_rules:
            # Nothing in this class's knowledge base cleared the relevance bar.
            # Refuse outright instead of letting the model answer from its own
            # general knowledge — the tutor is only allowed to know the teacher's
            # material. Stored with empty rules, so it surfaces as a knowledge gap.
            final_answer = (
                "I don't have anything on that in this class's knowledge base yet. "
                "Ask your teacher to add it, or try rephrasing your question."
            )
        else:
            contents = history + [{"role": "user", "parts": [{"text": user_message}]}]

            system_instruction = (
                "You are Chronos, a tutor whose entire knowledge is the teacher "
                "material provided below. Follow these rules exactly:\n"
                "1. Answer using ONLY the teacher material below. Treat it as the only "
                "thing you know about the subject.\n"
                "2. Do NOT use outside or general knowledge, even if you are sure of the "
                "answer. If a fact is not stated in the material, you do not know it.\n"
                "3. If the material below does not cover the question, say you don't have "
                "that in your knowledge base and suggest asking the teacher. Never guess "
                "or fill gaps from your own knowledge.\n"
                "4. You may use the earlier conversation for context, but never as a "
                "source of new facts.\n\n"
                f"Teacher material:\n{context_block}"
            )

            ai_response = client.models.generate_content(
                model=CHAT_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
            final_answer = ai_response.text

        # Title a brand-new conversation from its opening question.
        chat_meta = {"last_active": firestore.SERVER_TIMESTAMP, "class_id": class_id}
        title = None
        if is_new:
            title = user_message[:40] + ("…" if len(user_message) > 40 else "")
            chat_meta["title"] = title
            chat_meta["created_at"] = firestore.SERVER_TIMESTAMP

        # Roll this exchange into the chat's stats summary. /stats used to derive
        # all of this by re-reading every message of every chat of every member —
        # thousands of Firestore reads to render one page. Keeping the summary
        # current here costs nothing (we're already writing this doc) and lets
        # /stats work off chat documents alone.
        chat_meta.update(summarize_exchange(prev, is_new, user_message, final_answer, teacher_rules))

        chat_doc.set(chat_meta, merge=True)

        msgs_ref.add({"role": "student", "content": user_message, "timestamp": firestore.SERVER_TIMESTAMP})
        msgs_ref.add({"role": "teacher", "content": final_answer, "rules": teacher_rules, "timestamp": firestore.SERVER_TIMESTAMP})

        return jsonify({
            "response": final_answer,
            "rules_used": teacher_rules,
            "chat_id": chat_id,
            "title": title,
        })

    except Exception as e:
        return server_error("Server issue while answering.", e)


# ---------- teacher: knowledge management ----------

@app.route('/ingest', methods=['POST'])
@require_teacher
def ingest():
    """Add one or more rules to a class's knowledge base.
    Body: { class_id, items: [{ id?, text }, ...] }  OR  { class_id, text, id? }
    """
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown class, or you don't own it."}), 403

    items = data.get("items")
    if not items:
        if data.get("text"):
            items = [{"id": data.get("id"), "text": data["text"]}]
        else:
            return jsonify({"error": "No items or text provided"}), 400

    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400

    # Collect the usable rules first, then embed them in batches. A "bulk add" of
    # 50 pasted rules used to be 50 sequential embedding round-trips; batching
    # turns that into one or two, which is the difference between a snappy save
    # and a request the host times out.
    pending = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = (it.get("text") or "").strip()
        if not text:
            continue
        # Pinecone caps vector ids at 512 chars; truncate rather than let the
        # whole upsert fail on one over-long teacher-supplied id.
        rid = str(it.get("id") or f"rule_{int(time.time()*1000)}_{len(pending)}")[:512]
        pending.append((rid, text))

    if not pending:
        return jsonify({"error": "No items or text provided"}), 400

    results = []
    vectors = []
    BATCH = 50  # keeps each embedding request well under the API's token limit
    for start in range(0, len(pending), BATCH):
        group = pending[start:start + BATCH]
        try:
            values = embed_batch([t for _, t in group])
        except Exception as e:
            # One failed batch shouldn't sink the rules that did embed.
            logger.exception("Embedding batch failed during ingest.")
            results.extend({"id": rid, "status": "error", "detail": str(e)} for rid, _ in group)
            continue
        for (rid, text), vec in zip(group, values):
            vectors.append({"id": rid, "values": vec, "metadata": {"text": text}})
            results.append({"id": rid, "status": "ok"})

    if vectors:
        try:
            pinecone_index.upsert(vectors=vectors, namespace=class_id)
        except Exception as e:
            return server_error("Upsert failed.", e)

    return jsonify({"results": results, "total_vectors": class_vector_count(class_id)})


@app.route('/rules', methods=['POST'])
@require_teacher
def list_rules():
    """List a class's rules. Pinecone has no 'list all' so we query broadly
    within the class namespace. Body: { class_id }
    """
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown class, or you don't own it."}), 403
    try:
        count = class_vector_count(class_id)
        if count == 0:
            return jsonify({"rules": [], "total_vectors": 0})
        zero = [0.0] * EMBED_DIM
        resp = pinecone_index.query(
            vector=zero,
            top_k=min(count, 100),
            include_metadata=True,
            namespace=class_id,
        )
        rules = [
            {"id": m["id"], "text": m["metadata"].get("text", ""), "source": m["metadata"].get("source")}
            for m in resp["matches"]
        ]
        return jsonify({"rules": rules, "total_vectors": count})
    except Exception as e:
        return server_error("Could not list rules.", e)


@app.route('/delete_rule', methods=['POST'])
@require_teacher
def delete_rule():
    """Delete a rule by id from a class. Body: { class_id, id }"""
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown class, or you don't own it."}), 403
    rid = data.get("id")
    if not rid:
        return jsonify({"error": "No id provided"}), 400
    try:
        pinecone_index.delete(ids=[rid], namespace=class_id)
        return jsonify({"status": "deleted", "id": rid})
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


# Wording the tutor falls back to when a question isn't covered by the material.
_GAP_PHRASES = (
    "don't have", "do not have", "not in my", "isn't in", "is not in",
    "don't cover", "do not cover", "not covered", "no information",
)


def _is_unanswered(teacher_msg):
    """True when a tutor answer shows the class knowledge base couldn't cover the
    question — either no source chunks backed the answer, or it fell back to the
    'not in my knowledge base' wording. Surfaced to teachers as a content gap.

    Works on historical logs too: every teacher message already stores the
    `rules` it used, so an empty list is a reliable 'nothing matched' signal.
    """
    rules = teacher_msg.get("rules")
    if isinstance(rules, list) and len(rules) == 0:
        return True
    text = (teacher_msg.get("content") or "").lower()
    return "knowledge base" in text and any(p in text for p in _GAP_PHRASES)


# Bump when the shape of the per-chat summary changes; chats stamped with an
# older (or missing) version fall back to the slow re-read-every-message path.
STAT_SUMMARY_VERSION = 1
_SUMMARY_CONTEXT_CHARS = 600   # how much of a conversation feeds categorization
_SUMMARY_OPENING_CHARS = 300   # the question shown in the teacher's feed
_SUMMARY_MAX_GAPS = 25         # /stats only ever shows the newest 25 anyway


def summarize_exchange(prev, is_new, question, answer, rules):
    """Fields to merge into a chat doc so /stats never has to open its messages.

    `prev` is the chat document as it was before this exchange. Everything here
    is derived from what /chat already has in hand, so maintaining it adds no
    reads and no extra writes — it rides along on the chat doc update.
    """
    summary = {"stat_v": STAT_SUMMARY_VERSION}

    if is_new:
        summary["opening"] = question[:_SUMMARY_OPENING_CHARS]
    elif not prev.get("opening"):
        # A conversation that predates summaries: its real opening question is
        # gone from this request, but `title` was cut from it, so prefer that
        # over mislabelling the newest question as the one that started the chat.
        summary["opening"] = (prev.get("title") or question)[:_SUMMARY_OPENING_CHARS]

    # Categorization reads the start of a conversation, so stop growing the blob
    # once we have enough of it.
    context = "" if is_new else (prev.get("context") or "")
    if len(context) < _SUMMARY_CONTEXT_CHARS:
        summary["context"] = (context + " " + question).strip()[:_SUMMARY_CONTEXT_CHARS]

    if _is_unanswered({"rules": rules, "content": answer}):
        gaps = [] if is_new else list(prev.get("gaps") or [])
        gaps.append(question[:_SUMMARY_OPENING_CHARS])
        summary["gaps"] = gaps[-_SUMMARY_MAX_GAPS:]

    return summary


def categorize_conversations(convos):
    """Ask Gemini to tag each whole conversation with ONE short topic category.

    `convos` is a list of strings, each the representative text of one
    conversation (opening question plus a little follow-up context).
    """
    if not convos:
        return []
    numbered = "\n".join(f"{i+1}. {c}" for i, c in enumerate(convos))
    prompt = (
        "You are categorizing student tutoring conversations. Each numbered item is "
        "ONE conversation (it may include follow-up turns). Give a SHORT topic "
        "category (1-3 words) for the whole conversation based on what it is mainly "
        "about, like 'Quadratic formula', 'Gear ratios', 'Sensors', 'Off-topic'. "
        "Respond ONLY with a JSON array of strings, one per conversation, in order. "
        "No markdown, no extra text.\n\n"
        f"Conversations:\n{numbered}"
    )
    try:
        resp = client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
        )
        text = resp.text.strip().replace("```json", "").replace("```", "").strip()
        cats = json.loads(text)
        if isinstance(cats, list) and len(cats) == len(convos):
            # Truncate: the category is model output derived from student text, so
            # a prompt-injected "category" shouldn't be able to be a wall of text
            # (or a payload) in the teacher's dashboard.
            return [str(c)[:40] for c in cats]
    except Exception:
        pass
    return ["Uncategorized"] * len(convos)


@app.route('/stats', methods=['POST'])
@require_teacher
def stats():
    """Per-class analytics from Firestore chat logs. Body: { class_id, limit? }

    Scoped to one class the teacher owns: we walk the class's members and read
    each member's conversations for this class. This keeps a teacher's analytics
    to their own class (no cross-teacher leakage) and needs no special index.
    """
    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown class, or you don't own it."}), 403
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
        gaps = []              # (ts, question, uid) the knowledge base couldn't answer
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
                    opening = (d.get("opening") or d.get("title") or "").strip()
                    if not opening:
                        continue
                    chat_gaps = d.get("gaps") or []
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
                    continue

                # Legacy path, for conversations written before summaries existed:
                # replay the messages in timestamp order so each tutor answer pairs
                # with the question right before it.
                legacy_chats += 1
                msgs = []
                chat_gaps = []          # (ts, question) this conversation couldn't answer
                last_student = None     # (timestamp, content) of the latest question
                for msg in chat.reference.collection("Messages").order_by("timestamp").stream():
                    m = msg.to_dict() or {}
                    role = m.get("role")
                    content = (m.get("content") or "").strip()
                    if role == "student":
                        if content:
                            msgs.append((m.get("timestamp"), content))
                            last_student = (m.get("timestamp"), content)
                    elif role == "teacher" and last_student and _is_unanswered(m):
                        chat_gaps.append((_ts_seconds(last_student[0]), last_student[1]))
                        last_student = None
                if msgs:
                    msgs.sort(key=lambda x: _ts_seconds(x[0]))
                    last_ts = max(_ts_seconds(t) for t, _ in msgs)
                    if cutoff is not None and last_ts < cutoff:
                        continue
                    convos.append({
                        "opening": msgs[0][1],                          # the question that started it
                        "context": " ".join(c for _, c in msgs[:4])[:_SUMMARY_CONTEXT_CHARS],
                        "last_ts": last_ts,
                        "uid": uid,
                        "gapped": bool(chat_gaps),
                    })
                    gaps.extend((ts, q, uid) for ts, q in chat_gaps)

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
        cat_counts = {}
        for c in categories:
            cat_counts[c] = cat_counts.get(c, 0) + 1

        sorted_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)

        # One row per conversation, most recent first.
        recent = [
            {"question": convos[i]["opening"], "category": categories[i]}
            for i in range(len(convos))
        ][-25:][::-1]

        # Openings a class keeps coming back to. Grouped case/whitespace-insensitively
        # so "What is osmosis?" and "what is osmosis" are one row, labelled with the
        # first wording seen.
        repeats = _group_questions(
            (c["last_ts"], c["opening"], c["uid"]) for c in convos
        )[:8]

        # Gaps, most-asked first — the dashboard leads with them, so frequency
        # matters more than recency here.
        unanswered = _group_questions(gaps)[:25]

        return jsonify({
            "total_questions": total_questions,
            "total_sessions": session_count,   # every chat ever; not date-windowed
            "students_total": len(member_uids),
            "students_active": len({c["uid"] for c in convos}),
            "grounded_conversations": sum(1 for c in convos if not c["gapped"]),
            "categories": [{"name": k, "count": v} for k, v in sorted_cats],
            "recent": recent,
            "repeats": repeats,
            "unanswered": unanswered,
            "unanswered_count": len(gaps),
        })
    except Exception as e:
        return server_error("Stats failed.", e)


@app.route('/upload', methods=['POST'])
@require_teacher
def upload():
    class_id = (request.form.get("class_id") or "").strip()
    if not class_owned_by(class_id, request.uid):
        return jsonify({"error": "Unknown class, or you don't own it."}), 403

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    fname = file.filename.lower()
    text = ""

    try:
        if fname.endswith(".pdf"):
            if pypdf is None:
                return jsonify({"error": "pypdf not installed"}), 500
            reader = pypdf.PdfReader(file.stream)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif fname.endswith(".docx"):
            if DocxDocument is None:
                return jsonify({"error": "python-docx not installed"}), 500
            # Check the archive's declared uncompressed size before handing it to
            # python-docx, which would expand the whole thing into memory first.
            # Reading the central directory decompresses nothing.
            # ponytail: trusts the declared sizes, which a hand-built zip can
            # understate; a hard ceiling would need a counting decompress stream.
            try:
                declared = sum(i.file_size for i in zipfile.ZipFile(file.stream).infolist())
            except zipfile.BadZipFile:
                return jsonify({"error": "That .docx isn't a readable Word file."}), 400
            if declared > MAX_EXTRACT_BYTES:
                return jsonify({"error": "That document expands to far too much content."}), 413
            file.stream.seek(0)
            doc = DocxDocument(file.stream)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif fname.endswith((".txt", ".md", ".csv")):
            text = file.read().decode("utf-8", errors="ignore")
        else:
            return jsonify({"error": "Unsupported file type. Use .pdf, .docx, .txt, .md, or .csv"}), 400
    except Exception as e:
        return server_error("Could not read that file. Is it a valid, non-encrypted document?", e, status=400)

    if not text.strip():
        return jsonify({"error": "No text could be extracted from the file"}), 400

    # Every format converges here, so one cap bounds the embedding cost and the
    # peak memory of the chunk list for all of them — however the text got large.
    truncated = len(text) > MAX_EXTRACT_CHARS
    if truncated:
        logger.info("Truncated %s from %d to %d characters.", file.filename, len(text), MAX_EXTRACT_CHARS)
        text = text[:MAX_EXTRACT_CHARS]

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
    # 50 keeps each embedding request well under the API's per-call token limit.
    BATCH = 50
    source = file.filename
    base_id = f"file_{int(time.time() * 1000)}"
    stored = 0

    try:
        for start in range(0, len(chunks), BATCH):
            group = chunks[start:start + BATCH]
            vectors = embed_batch(group)
            pending = [
                {"id": f"{base_id}_{start + j}", "values": vectors[j],
                 "metadata": {"text": group[j], "source": source}}
                for j in range(len(group))
            ]
            pinecone_index.upsert(vectors=pending, namespace=class_id)
            stored += len(pending)
            del vectors, pending, group
            gc.collect()
    except Exception as e:
        return server_error("Upload failed while indexing the document.", e)

    gc.collect()
    result = {"chunks": len(chunks), "stored": stored, "total_vectors": class_vector_count(class_id)}
    if truncated:
        # Say so rather than silently indexing half a document — a teacher who
        # thinks all of it landed would trust gaps that aren't really gaps.
        result["warning"] = (
            f"Only the first {MAX_EXTRACT_CHARS:,} characters were indexed. "
            "Split the document and upload it in parts to add the rest."
        )
    return jsonify(result)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    # Bind to 0.0.0.0 and the host-provided PORT (Cloud Run injects this; default
    # 5000 locally). Debug is off unless FLASK_DEBUG is explicitly set (top of file).
    # NOTE: this dev server is only a fallback — production runs waitress via the
    # Dockerfile's CMD.
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)