"""Self-check for llm.py: the Ollama backend against a fake Ollama server, and
/chat and /tools/run running end to end through it.

Run:  python test_llm.py
A local HTTP server stands in for Ollama; Firestore, Pinecone and embeddings are
stubbed, so this runs offline.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A
import llm

requests = []      # every body the fake server received
replies = []       # queue of (status, body-or-lines) the server answers with


class _Ollama(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        status, payload = replies.pop(0)
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        if isinstance(payload, list):           # a stream: one JSON object per line
            for line in payload:
                self.wfile.write((json.dumps(line) + "\n").encode())
        else:
            self.wfile.write(json.dumps(payload).encode())


server = HTTPServer(("127.0.0.1", 0), _Ollama)
threading.Thread(target=server.serve_forever, daemon=True).start()
llm.PROVIDER = "ollama"
llm.OLLAMA_URL = "http://127.0.0.1:%d" % server.server_port
llm.OLLAMA_API_KEY = "sekrit"
llm.OLLAMA_TIMEOUT_S = 5

TOOL = A.practice_activity_tool(dict(A.course_settings(), practice_tools=True))


def test_generate_text_and_request_shape():
    requests.clear()
    replies.append((200, {"message": {"role": "assistant", "content": " Hello \n"},
                          "done": True, "prompt_eval_count": 7, "eval_count": 3}))
    contents = [{"role": "user", "parts": [{"text": "hi"}]}, {"role": "model", "parts": [{"text": "yo"}]},
                {"role": "user", "parts": [{"text": "again"}]}]
    r = llm.generate("gemma", contents, system="SYS", tools=[TOOL], temperature=0.3)
    assert r.text == "Hello" and r.tool_call is None, r
    assert r.usage == {"prompt": 7, "reply": 3, "total": 10}, r.usage
    sent = requests[-1]
    assert sent["path"] == "/api/chat" and sent["auth"] == "Bearer sekrit"
    b = sent["body"]
    assert [m["role"] for m in b["messages"]] == ["system", "user", "assistant", "user"], b["messages"]
    assert b["messages"][0]["content"] == "SYS" and b["stream"] is False
    assert b["options"] == {"temperature": 0.3}
    assert b["tools"][0]["function"]["name"] == A.TOOL_FUNCTION_NAME
    assert b["tools"][0]["function"]["parameters"]["properties"]["type"]["enum"] == ["quiz", "flashcards"]
    assert "format" not in b
    print("ok - Ollama request carries system, roles, tools, temperature and auth")


def test_tool_call_and_string_arguments():
    for args in ({"type": "quiz", "topic": "osmosis"}, json.dumps({"type": "quiz", "topic": "osmosis"})):
        replies.append((200, {"message": {"role": "assistant", "content": "",
                                          "tool_calls": [{"function": {"name": A.TOOL_FUNCTION_NAME,
                                                                       "arguments": args}}]}, "done": True}))
        r = llm.generate("gemma", "quiz me", tools=[TOOL])
        assert r.tool_call == {"name": A.TOOL_FUNCTION_NAME, "args": {"type": "quiz", "topic": "osmosis"}}, r
    print("ok - tool calls parse whether arguments arrive as an object or a JSON string")


def test_json_schema_becomes_format():
    replies.append((200, {"message": {"content": "{}"}, "done": True}))
    llm.generate("gemma", "p", json_schema=A.ACTIVITY_SCHEMAS["quiz"])
    assert requests[-1]["body"]["format"] == A.ACTIVITY_SCHEMAS["quiz"]
    replies.append((200, {"message": {"content": "{}"}, "done": True}))
    llm.generate("gemma", "p", json_schema={})
    assert requests[-1]["body"]["format"] == "json"
    print("ok - a JSON schema constrains Ollama decoding via `format`")


def test_stream():
    replies.append((200, [
        {"message": {"content": "Hel"}, "done": False},
        {"message": {"content": "lo"}, "done": False},
        {"message": {"content": "", "tool_calls": [{"function": {"name": A.TOOL_FUNCTION_NAME,
                                                                 "arguments": {"type": "flashcards", "topic": "cells"}}}]},
         "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 4, "eval_count": 2},
    ]))
    chunks = list(llm.stream("gemma", "hi", tools=[TOOL]))
    assert "".join(c.text for c in chunks) == "Hello"
    assert [c.tool_call for c in chunks if c.tool_call] == [
        {"name": A.TOOL_FUNCTION_NAME, "args": {"type": "flashcards", "topic": "cells"}}]
    assert chunks[-1].usage == {"prompt": 4, "reply": 2, "total": 6}
    assert requests[-1]["body"]["stream"] is True
    print("ok - streaming yields text deltas, the tool call and final usage")


def test_errors_raise():
    replies.append((404, {"error": "model 'gemma' not found"}))
    try:
        llm.generate("gemma", "hi")
        raise AssertionError("an HTTP error was swallowed")
    except llm.LLMError as e:
        assert "404" in str(e) and "not found" in str(e), e
    saved = llm.OLLAMA_URL
    llm.OLLAMA_URL = "http://127.0.0.1:9"      # nothing listens here
    try:
        llm.generate("gemma", "hi")
        raise AssertionError("an unreachable host was swallowed")
    except llm.LLMError:
        pass
    finally:
        llm.OLLAMA_URL = saved
    replies.append((200, {"model": "gemma"}))
    assert llm.health("gemma") and requests[-1]["path"] == "/api/show"
    print("ok - HTTP and connection failures raise LLMError; health uses /api/show")


# ---------- the routes, end to end through the Ollama backend ----------

def _route_fakes(settings):
    A.verify_user = lambda: {"uid": "stu", "email": "s@example.test"}
    A.get_role = lambda _uid: "student"
    A.user_in_class = lambda *a, **k: True
    A.load_course_settings = lambda *a, **k: settings
    A.load_custom_rules = lambda *a, **k: []
    A.load_course_manifest = lambda *a, **k: []
    A.load_user_preferences = lambda *a, **k: A.normalize_preferences(None)
    A.load_class_memory = lambda *a, **k: ""
    A.load_class_profile = lambda *a, **k: {}
    A.load_student_docs = lambda *a, **k: []
    A.load_history = lambda *a, **k: []
    A.refresh_conversation_summary = lambda *a, **k: ("", {})
    A._user_profile = lambda *a, **k: type("_P", (), {"set": lambda s, *a, **k: None})()
    A.summarize_exchange = lambda *a, **k: {}
    A.embed = lambda _t: [0.0] * A.EMBED_DIM
    A.pinecone_index = type("_PC", (), {"query": lambda s, **kw: {"matches": [
        {"id": "file_1_0", "metadata": {"text": "Osmosis is the movement of water across a membrane.",
                                        "source": "Cells.pdf", "chunk": 0}, "score": 0.8}]}})()

    class _Doc:
        id = "chat1"
        exists = True
        def get(self): return self
        def to_dict(self): return {"message_count": 2}
        def set(self, *a, **k): pass
        def collection(self, _n): return type("_M", (), {"add": lambda s, *a, **k: None})()

    A._user_chats = lambda _uid: type("_C", (), {"document": lambda s, *_a: _Doc()})()


def test_chat_and_tool_routes_on_ollama():
    A._rate_hits.clear()
    settings = dict(A.course_settings(), practice_tools=True)
    _route_fakes(settings)
    c = A.app.test_client()

    replies.append((200, {"message": {"content": "Let's check what you know.",
                                      "tool_calls": [{"function": {"name": A.TOOL_FUNCTION_NAME,
                                                                   "arguments": {"type": "quiz", "topic": "Osmosis"}}}]},
                          "done": True}))
    r = c.post("/chat", json={"message": "can you test me on osmosis", "class_id": "c1", "chat_id": "chat1"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["response"] == "Let's check what you know."
    assert body["tool_request"] == {"type": "quiz", "topic": "Osmosis"}, body
    sent = requests[-1]["body"]
    assert sent["model"] == A.CHAT_MODEL and "Osmosis is the movement" in sent["messages"][0]["content"]

    quiz = {"type": "quiz", "title": "Osmosis", "questions": [
        {"prompt": "Osmosis moves", "options": ["water", "salt", "sugar", "air"], "answer": 0,
         "explanation": "Water crosses the membrane."}] * 3}
    replies.append((200, {"message": {"content": json.dumps(quiz)}, "done": True}))
    r = c.post("/tools/run", json={"class_id": "c1", "chat_id": "chat1",
                                   "tool_request": {"type": "quiz", "topic": "Osmosis"}})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["tool"]["questions"]) == 3
    assert requests[-1]["body"]["format"] == A.ACTIVITY_SCHEMAS["quiz"]

    # A tool the course has switched off is dropped even if the model calls it.
    replies.append((200, {"message": {"content": "Here you go.",
                                      "tool_calls": [{"function": {"name": A.TOOL_FUNCTION_NAME,
                                                                   "arguments": {"type": "concept_map", "topic": "x"}}}]},
                          "done": True}))
    r = c.post("/chat", json={"message": "map osmosis for me", "class_id": "c1", "chat_id": "chat1"})
    assert r.get_json()["tool_request"] is None
    print("ok - /chat and /tools/run work end to end on the Ollama backend")


if __name__ == "__main__":
    try:
        test_generate_text_and_request_shape()
        test_tool_call_and_string_arguments()
        test_json_schema_becomes_format()
        test_stream()
        test_errors_raise()
        test_chat_and_tool_routes_on_ollama()
    finally:
        server.shutdown()
