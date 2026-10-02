"""Self-check for llm.py: the OpenAI-compatible backend against a fake server, and
/chat and /tools/run running end to end through it.

Run:  python test_llm.py
A local HTTP server stands in for the endpoint; Firestore, Pinecone and embeddings are
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


class _Server(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        status, payload = replies.pop(0)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if isinstance(payload, list):           # a stream: one JSON object per line
            for line in payload:
                self.wfile.write(("data: " + json.dumps(line) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.wfile.write(json.dumps(payload).encode())


server = HTTPServer(("127.0.0.1", 0), _Server)
threading.Thread(target=server.serve_forever, daemon=True).start()
llm.BASE_URL = "http://127.0.0.1:%d/v1" % server.server_port
llm.API_KEY = "sekrit"
llm.TIMEOUT_S = 5

TOOL = A.practice_activity_tool(dict(A.course_settings(), practice_tools=True))


def _reply(content="", tool_calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    out = {"choices": [{"message": msg}]}
    if usage:
        out["usage"] = usage
    return out


def _call(args):
    return [{"id": "c1", "type": "function", "function": {"name": A.TOOL_FUNCTION_NAME, "arguments": args}}]


def test_generate_text_and_request_shape():
    requests.clear()
    replies.append((200, _reply(" Hello \n", usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})))
    contents = [{"role": "user", "parts": [{"text": "hi"}]}, {"role": "model", "parts": [{"text": "yo"}]},
                {"role": "user", "parts": [{"text": "again"}]}]
    r = llm.generate("m", contents, system="SYS", tools=[TOOL], temperature=0.3)
    assert r.text == "Hello" and r.tool_call is None, r
    assert r.usage == {"prompt": 7, "reply": 3, "total": 10}, r.usage
    sent = requests[-1]
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == "Bearer sekrit"
    b = sent["body"]
    assert [m["role"] for m in b["messages"]] == ["system", "user", "assistant", "user"], b["messages"]
    assert b["messages"][0]["content"] == "SYS" and b["stream"] is False
    assert b["temperature"] == 0.3
    assert b["tools"][0]["function"]["name"] == A.TOOL_FUNCTION_NAME
    assert b["tools"][0]["function"]["parameters"]["properties"]["type"]["enum"] == ["quiz", "flashcards"]
    assert "response_format" not in b
    print("ok - request carries system, roles, tools, temperature and auth")


def test_tool_call_and_string_arguments():
    want = {"name": A.TOOL_FUNCTION_NAME, "args": {"type": "quiz", "topic": "osmosis"}}
    for args in ({"type": "quiz", "topic": "osmosis"}, json.dumps({"type": "quiz", "topic": "osmosis"})):
        replies.append((200, _reply("", _call(args))))
        assert llm.generate("m", "quiz me", tools=[TOOL]).tool_call == want
    print("ok - tool calls parse whether arguments arrive as an object or a JSON string")


def test_json_schema_becomes_response_format():
    replies.append((200, _reply("{}")))
    llm.generate("m", "p", json_schema=A.ACTIVITY_SCHEMAS["quiz"])
    assert requests[-1]["body"]["response_format"]["json_schema"]["schema"] == A.ACTIVITY_SCHEMAS["quiz"]
    replies.append((200, _reply("{}")))
    llm.generate("m", "p", json_schema={})
    assert requests[-1]["body"]["response_format"] == {"type": "json_object"}
    print("ok - a JSON schema constrains decoding via response_format")


def test_stream():
    d = lambda **kw: {"choices": [{"delta": kw}]}
    replies.append((200, [
        d(content="Hel"), d(content="lo"),
        d(tool_calls=[{"index": 0, "function": {"name": A.TOOL_FUNCTION_NAME, "arguments": '{"type": "flash'}}]),
        d(tool_calls=[{"index": 0, "function": {"arguments": 'cards", "topic": "cells"}'}}]),
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    ]))
    chunks = list(llm.stream("m", "hi", tools=[TOOL]))
    assert "".join(c.text for c in chunks) == "Hello"
    assert [c.tool_call for c in chunks if c.tool_call] == [
        {"name": A.TOOL_FUNCTION_NAME, "args": {"type": "flashcards", "topic": "cells"}}]
    assert chunks[-1].usage == {"prompt": 4, "reply": 2, "total": 6}
    assert requests[-1]["body"]["stream"] is True
    print("ok - streaming yields text deltas, the assembled tool call and final usage")


def test_embeddings():
    replies.append((200, {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}))
    assert llm.embed_batch(["a", "b"]) == [[1.0], [2.0]]
    assert requests[-1]["path"] == "/v1/embeddings" and requests[-1]["body"]["input"] == ["a", "b"]
    replies.append((200, {"data": [{"index": 0, "embedding": [3.0]}]}))
    assert llm.embed("x") == [3.0]
    print("ok - embeddings are returned in input order")


def test_errors_raise():
    replies.append((404, {"error": {"message": "model 'm' not found"}}))
    try:
        llm.generate("m", "hi")
        raise AssertionError("an HTTP error was swallowed")
    except llm.LLMError as e:
        assert "404" in str(e) and "not found" in str(e), e
    saved = llm.BASE_URL
    llm.BASE_URL = "http://127.0.0.1:9/v1"      # nothing listens here
    try:
        llm.generate("m", "hi")
        raise AssertionError("an unreachable host was swallowed")
    except llm.LLMError:
        pass
    finally:
        llm.BASE_URL = saved
    print("ok - HTTP and connection failures raise LLMError")


# ---------- the routes, end to end through the endpoint ----------

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


def test_chat_and_tool_routes_on_endpoint():
    A._rate_hits.clear()
    settings = dict(A.course_settings(), practice_tools=True)
    _route_fakes(settings)
    c = A.app.test_client()

    replies.append((200, _reply("Let's check what you know.", _call({"type": "quiz", "topic": "Osmosis"}))))
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
    replies.append((200, _reply(json.dumps(quiz))))
    r = c.post("/tools/run", json={"class_id": "c1", "chat_id": "chat1",
                                   "tool_request": {"type": "quiz", "topic": "Osmosis"}})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["tool"]["questions"]) == 3
    assert requests[-1]["body"]["response_format"]["json_schema"]["schema"] == A.ACTIVITY_SCHEMAS["quiz"]

    # A tool the course has switched off is dropped even if the model calls it.
    replies.append((200, _reply("Here you go.", _call({"type": "concept_map", "topic": "x"}))))
    r = c.post("/chat", json={"message": "map osmosis for me", "class_id": "c1", "chat_id": "chat1"})
    assert r.get_json()["tool_request"] is None
    print("ok - /chat and /tools/run work end to end through the endpoint")


if __name__ == "__main__":
    try:
        test_generate_text_and_request_shape()
        test_tool_call_and_string_arguments()
        test_json_schema_becomes_response_format()
        test_embeddings()
        test_stream()
        test_errors_raise()
        test_chat_and_tool_routes_on_endpoint()
    finally:
        server.shutdown()
