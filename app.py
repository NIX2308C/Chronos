import os
import json
import time
import hmac
import logging
from functools import wraps
from collections import deque
from threading import Lock
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
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
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_MESSAGE_CHARS = 8000

# Lightweight per-IP rate limit for the unauthenticated /chat endpoint.
CHAT_RATE_LIMIT = int(os.getenv("CHAT_RATE_LIMIT", "20"))     # requests
CHAT_RATE_WINDOW = int(os.getenv("CHAT_RATE_WINDOW", "60"))   # seconds

# How many past messages to replay into the model so it remembers the conversation.
# Each Q&A is 2 messages, so 20 ≈ the last 10 exchanges. Capped to bound tokens/latency.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "20"))
_rate_hits = {}
_rate_lock = Lock()

INDEX_NAME = "teacherchronostwo"
EMBED_DIM = 768

# Directory this file lives in — used to serve the front-end HTML pages.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(app, origins=ALLOWED_ORIGINS)
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
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            for sep in ('. ', '! ', '? ', ' '):
                idx = text.rfind(sep, start + size // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start >= len(text):
            break
    return chunks


def embed(text):
    """Return a 768-dim embedding for the given text."""
    result = client.models.embed_content(
        model="models/gemini-embedding-001",
        contents=text,
        config={"output_dimensionality": EMBED_DIM},
    )
    return result.embeddings[0].values


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
    """Return the stored role ('teacher'/'student') for a user, or None."""
    try:
        doc = db.collection("Users").document(uid).get()
        if doc.exists:
            return (doc.to_dict() or {}).get("role")
    except Exception:
        logger.exception("Could not read user role for %s", uid)
    return None


def require_auth(fn):
    """Gate: any signed-in Firebase user. Stashes the decoded token + uid on
    `request` so the handler can use them."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        decoded = verify_user()
        if not decoded:
            return jsonify({"error": "Unauthorized. Please sign in."}), 401
        request.user = decoded
        request.uid = decoded["uid"]
        return fn(*args, **kwargs)
    return wrapper


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


def rate_limited(ip):
    """Sliding-window in-memory rate limit, per client IP."""
    now = time.time()
    with _rate_lock:
        dq = _rate_hits.setdefault(ip, deque())
        while dq and dq[0] <= now - CHAT_RATE_WINDOW:
            dq.popleft()
        if len(dq) >= CHAT_RATE_LIMIT:
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


@app.route('/auth/register', methods=['POST'])
@require_auth
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
        supplied = str(data.get("teacher_code") or "")
        if not supplied or not hmac.compare_digest(supplied, TEACHER_SIGNUP_CODE):
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
            "last_active": _ts_seconds(m.get("last_active")),
        })
    chats.sort(key=lambda c: c["last_active"], reverse=True)
    return jsonify({"chats": chats})


@app.route('/chats/<chat_id>/messages', methods=['GET'])
@require_auth
def chat_messages(chat_id):
    """Return all messages for one of the user's conversations, oldest first."""
    chat_ref = _user_chats(request.uid).document(chat_id)
    if not chat_ref.get().exists:
        return jsonify({"error": "Chat not found"}), 404
    docs = list(chat_ref.collection("Messages").order_by("timestamp").stream())
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
    ip = request.remote_addr or "unknown"
    if rate_limited(ip):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429

    data = request.get_json(silent=True) or {}
    user_message = data.get("message")
    # Conversations now live under the signed-in user. chat_id picks an existing
    # conversation; omit it (or pass a new id) to start a fresh one.
    chat_id = str(data.get("chat_id") or "").strip()[:128]

    if not user_message or not isinstance(user_message, str) or not user_message.strip():
        return jsonify({"error": "No message provided"}), 400
    user_message = user_message.strip()
    if len(user_message) > MAX_MESSAGE_CHARS:
        return jsonify({"error": "Message is too long."}), 413

    try:
        question_embedding = embed(user_message)

        pinecone_resp = pinecone_index.query(
            vector=question_embedding,
            top_k=2,
            include_metadata=True,
        )

        teacher_rules = [m['metadata']['text'] for m in pinecone_resp['matches'] if 'metadata' in m]
        context_block = "\n".join(teacher_rules)

        # Resolve (or create) the conversation document for this user.
        chats_col = _user_chats(request.uid)
        chat_doc = chats_col.document(chat_id) if chat_id else chats_col.document()
        chat_id = chat_doc.id
        msgs_ref = chat_doc.collection('Messages')

        is_new = not chat_doc.get().exists

        # Replay the recent conversation so the AI remembers earlier turns, then
        # append the new question as the latest user turn.
        history = load_history(msgs_ref)
        contents = history + [{"role": "user", "parts": [{"text": user_message}]}]

        system_instruction = (
            "You are Chronos, a helpful tutor. Use ONLY the teacher rules below and "
            "the conversation so far to answer. If the rules don't cover the question, "
            "say you don't have that in your knowledge base rather than guessing.\n\n"
            f"Teacher rules:\n{context_block}"
        )

        ai_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_instruction),
        )
        final_answer = ai_response.text

        # Title a brand-new conversation from its opening question.
        chat_meta = {"last_active": firestore.SERVER_TIMESTAMP}
        title = None
        if is_new:
            title = user_message[:40] + ("…" if len(user_message) > 40 else "")
            chat_meta["title"] = title
            chat_meta["created_at"] = firestore.SERVER_TIMESTAMP
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
    """Add one or more rules/documents to Pinecone.
    Body: { password, items: [{ id?, text }, ...] }  OR  { password, text, id? }
    """
    data = request.get_json()
    items = data.get("items")
    if not items:
        if data.get("text"):
            items = [{"id": data.get("id"), "text": data["text"]}]
        else:
            return jsonify({"error": "No items or text provided"}), 400

    results = []
    vectors = []
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        rid = it.get("id") or f"rule_{int(time.time()*1000)}_{len(vectors)}"
        try:
            values = embed(text)
            vectors.append({
                "id": rid,
                "values": values,
                "metadata": {"text": text},
            })
            results.append({"id": rid, "status": "ok"})
        except Exception as e:
            results.append({"id": rid, "status": "error", "detail": str(e)})

    if vectors:
        try:
            pinecone_index.upsert(vectors=vectors)
        except Exception as e:
            return server_error("Upsert failed.", e)

    stats = pinecone_index.describe_index_stats()
    return jsonify({"results": results, "total_vectors": stats.total_vector_count})


@app.route('/rules', methods=['POST'])
@require_teacher
def list_rules():
    """List stored rules. Pinecone has no 'list all' so we query broadly.
    Body: { password }
    """
    try:
        stats = pinecone_index.describe_index_stats()
        count = stats.total_vector_count
        if count == 0:
            return jsonify({"rules": [], "total_vectors": 0})
        zero = [0.0] * EMBED_DIM
        resp = pinecone_index.query(
            vector=zero,
            top_k=min(count, 100),
            include_metadata=True,
        )
        rules = [{"id": m["id"], "text": m["metadata"].get("text", "")} for m in resp["matches"]]
        return jsonify({"rules": rules, "total_vectors": count})
    except Exception as e:
        return server_error("Could not list rules.", e)


@app.route('/delete_rule', methods=['POST'])
@require_teacher
def delete_rule():
    """Delete a rule by id. Body: { password, id }"""
    data = request.get_json()
    rid = data.get("id")
    if not rid:
        return jsonify({"error": "No id provided"}), 400
    try:
        pinecone_index.delete(ids=[rid])
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
            model='gemini-2.5-flash',
            contents=prompt,
        )
        text = resp.text.strip().replace("```json", "").replace("```", "").strip()
        cats = json.loads(text)
        if isinstance(cats, list) and len(cats) == len(convos):
            return [str(c) for c in cats]
    except Exception:
        pass
    return ["Uncategorized"] * len(convos)


@app.route('/stats', methods=['POST'])
@require_teacher
def stats():
    """Aggregate analytics from Firestore chat logs.
    Body: { password, limit? }
    """
    data = request.get_json(silent=True) or {}
    try:
        limit = int(data.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 1000))

    try:
        # Use a collection-group query so we pick up every Messages doc even if its
        # parent session document is a Firestore "phantom" (subcollection only, no fields).
        # Filtering role in Python avoids needing a composite index.
        #
        # Group student messages by their parent session: each session is one
        # conversation/assignment. A follow-up answer in the same conversation must
        # NOT add another question or topic count — the whole conversation counts once.
        sessions = {}          # session id -> list of (timestamp, content)
        session_ids = set()
        for m in db.collection_group('Messages').stream():
            d = m.to_dict()
            sess_ref = m.reference.parent.parent
            sid = sess_ref.id if sess_ref is not None else None
            if sid is not None:
                session_ids.add(sid)
            if d.get("role") != "student":
                continue
            content = (d.get("content") or "").strip()
            if not content:
                continue
            # Orphan messages (no parent session) each stand alone.
            key = sid if sid is not None else f"_orphan_{m.id}"
            sessions.setdefault(key, []).append((d.get("timestamp"), content))

        session_count = len(session_ids)

        # Collapse each conversation to one representative question + a little context.
        convos = []
        for msgs in sessions.values():
            msgs.sort(key=lambda x: _ts_seconds(x[0]))
            opening = msgs[0][1]                       # the question that started it
            context = " ".join(c for _, c in msgs[:4])[:600]
            last_ts = max(_ts_seconds(t) for t, _ in msgs)
            convos.append({"opening": opening, "context": context, "last_ts": last_ts})

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

        return jsonify({
            "total_questions": total_questions,
            "total_sessions": session_count,
            "categories": [{"name": k, "count": v} for k, v in sorted_cats],
            "recent": recent,
        })
    except Exception as e:
        return server_error("Stats failed.", e)


@app.route('/upload', methods=['POST'])
@require_teacher
def upload():
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

    chunks = chunk_text(text)
    if not chunks:
        return jsonify({"error": "File produced no usable text chunks"}), 400

    vectors = []
    results = []
    base_id = f"file_{int(time.time()*1000)}"
    for i, chunk in enumerate(chunks):
        rid = f"{base_id}_{i}"
        try:
            values = embed(chunk)
            vectors.append({"id": rid, "values": values, "metadata": {"text": chunk, "source": file.filename}})
            results.append({"id": rid, "status": "ok"})
        except Exception as e:
            results.append({"id": rid, "status": "error", "detail": str(e)})

    if vectors:
        try:
            pinecone_index.upsert(vectors=vectors)
        except Exception as e:
            return server_error("Upsert failed.", e)

    stats = pinecone_index.describe_index_stats()
    return jsonify({"chunks": len(chunks), "results": results, "total_vectors": stats.total_vector_count})


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    # Bind to 0.0.0.0 and the host-provided PORT (Render/Heroku set this; default
    # 5000 locally). Debug is off unless FLASK_DEBUG is explicitly set (top of file).
    # NOTE: this dev server is only a fallback — production should run waitress
    # (see startCommand in render.yaml).
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)