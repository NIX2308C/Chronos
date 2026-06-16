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
import firebase_admin
from firebase_admin import credentials, firestore
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

# Password the teacher panels must send (sent in the JSON/form body).
TEACHER_PASSWORD = os.getenv("TEACHER_PASSWORD", "")

# Run with the interactive debugger ONLY when explicitly enabled. Leaving the
# Werkzeug debugger on in a reachable deployment is a remote-code-execution risk.
DEBUG = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")

# Refuse to start with a missing or well-known default password — otherwise the
# teacher endpoints (which can read all chat logs and edit the knowledge base)
# would be wide open.
if not TEACHER_PASSWORD or TEACHER_PASSWORD.lower() in ("changeme", "password", "admin"):
    raise SystemExit(
        "Refusing to start: set a strong TEACHER_PASSWORD in your .env "
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


def check_password(supplied):
    """Constant-time password comparison to avoid timing side-channels."""
    return bool(supplied) and hmac.compare_digest(str(supplied), TEACHER_PASSWORD)


def require_teacher(fn):
    """Simple password gate. Frontend sends {'password': '...'} in the JSON body."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        data = request.get_json(silent=True) or {}
        if not check_password(data.get("password")):
            return jsonify({"error": "Unauthorized. Wrong teacher password."}), 401
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
@app.route('/student.html')
def page_student():
    return send_from_directory(BASE_DIR, 'student.html')


@app.route('/teacherknowledge.html')
def page_knowledge():
    return send_from_directory(BASE_DIR, 'teacherknowledge.html')


@app.route('/teacherstats.html')
def page_stats():
    return send_from_directory(BASE_DIR, 'teacherstats.html')


# ---------- student endpoint ----------

@app.route('/chat', methods=['POST'])
def chat():
    ip = request.remote_addr or "unknown"
    if rate_limited(ip):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429

    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", "default_session"))[:128]
    user_message = data.get("message")

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

        system_prompt = f"Answer using ONLY these rules:\n{context_block}\n\nQuestion: {user_message}"

        ai_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=system_prompt,
        )
        final_answer = ai_response.text

        session_doc = db.collection('Chat_Sessions').document(session_id)
        # Write a field on the parent doc so it actually "exists" and shows up in
        # collection queries (a doc with only subcollections is a phantom in Firestore).
        session_doc.set({"last_active": firestore.SERVER_TIMESTAMP}, merge=True)
        chat_ref = session_doc.collection('Messages')
        chat_ref.add({"role": "student", "content": user_message, "timestamp": firestore.SERVER_TIMESTAMP})
        chat_ref.add({"role": "teacher", "content": final_answer, "timestamp": firestore.SERVER_TIMESTAMP})

        return jsonify({"response": final_answer, "rules_used": teacher_rules})

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

def categorize_questions(questions):
    """Ask Gemini to tag each question with a short topic category."""
    if not questions:
        return []
    numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    prompt = (
        "You are categorizing student questions for a tutoring class. "
        "For each numbered question, give a SHORT topic category (1-3 words), "
        "like 'Gear ratios', 'Sensors', 'Wiring', 'Off-topic', 'Documentation'. "
        "Respond ONLY with a JSON array of strings, one per question, in order. "
        "No markdown, no extra text.\n\n"
        f"Questions:\n{numbered}"
    )
    try:
        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        text = resp.text.strip().replace("```json", "").replace("```", "").strip()
        cats = json.loads(text)
        if isinstance(cats, list) and len(cats) == len(questions):
            return [str(c) for c in cats]
    except Exception:
        pass
    return ["Uncategorized"] * len(questions)


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
        questions = []
        session_ids = set()
        for m in db.collection_group('Messages').stream():
            d = m.to_dict()
            if d.get("role") != "student":
                continue
            sess_ref = m.reference.parent.parent
            if sess_ref is not None:
                session_ids.add(sess_ref.id)
            content = d.get("content", "")
            if content:
                questions.append(content)

        session_count = len(session_ids)
        questions = questions[:limit]
        total_questions = len(questions)

        categories = categorize_questions(questions)
        cat_counts = {}
        for c in categories:
            cat_counts[c] = cat_counts.get(c, 0) + 1

        sorted_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)

        recent = [
            {"question": q, "category": categories[i]}
            for i, q in enumerate(questions)
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
def upload():
    if not check_password(request.form.get("password", "")):
        return jsonify({"error": "Unauthorized. Wrong teacher password."}), 401

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
    # Bind to loopback only so the dev server isn't exposed to the local network.
    # Debug is off unless FLASK_DEBUG is explicitly set (see top of file).
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)