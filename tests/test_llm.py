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

    def do_GET(self):
        requests.append({"path": self.path, "body": None, "auth": self.headers.get("Authorization")})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "embeddinggemma-q8"}]}).encode())

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
llm.EMBED_BASE_URL = llm.BASE_URL
llm.API_KEY = "sekrit"
llm.CHAT_API_KEY = "sekrit"
llm.TIMEOUT_S = 5
llm.time.sleep = lambda _s: None

TOOL = A.practice_activity_tool(dict(A.course_settings(), practice_tools=True))


def _reply(content="", usage=None):
    out = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage:
        out["usage"] = usage
    return out


def _call(args):
    return "\nTOOL_CALL " + json.dumps({"name": A.TOOL_FUNCTION_NAME, "args": args})


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
    assert b["messages"][0]["content"].startswith("SYS") and "TOOL_CALL" in b["messages"][0]["content"]
    assert A.TOOL_FUNCTION_NAME in b["messages"][0]["content"] and "tools" not in b
    assert b["stream"] is False and b["temperature"] == 0.3
    assert (b["top_p"], b["top_k"], b["max_tokens"]) == (0.95, 64, llm.MAX_TOKENS)
    assert "response_format" not in b and b["reasoning"] == {"exclude": True}
    print("ok - request carries system+tool prompt, roles, sampling and auth")


def test_tool_call_parsed_from_text():
    want = {"name": A.TOOL_FUNCTION_NAME, "args": {"type": "quiz", "topic": "osmosis"}}
    replies.append((200, _reply("Sure!" + _call({"type": "quiz", "topic": "osmosis"}))))
    r = llm.generate("m", "quiz me", tools=[TOOL])
    assert r.tool_call == want and r.text == "Sure!", r
    replies.append((200, _reply("Sure! TOOL_CALL {not json")))
    r = llm.generate("m", "quiz me", tools=[TOOL])
    assert r.tool_call is None and r.text == "Sure!", r
    print("ok - tool calls are parsed out of the text; malformed ones are dropped")


def test_thinking_is_stripped():
    replies.append((200, _reply("<|channel>thought\nhmm<channel|>Answer.")))
    assert llm.generate("m", "hi").text == "Answer."
    print("ok - thinking text never reaches the caller")


def test_json_schema_becomes_response_format():
    replies.append((200, _reply("{}")))
    llm.generate("m", "p", json_schema=A.ACTIVITY_SCHEMAS["quiz"])
    assert requests[-1]["body"]["response_format"]["json_schema"]["schema"] == A.ACTIVITY_SCHEMAS["quiz"]
    replies.append((200, _reply("{}")))
    llm.generate("m", "p", json_schema={})
    assert requests[-1]["body"]["response_format"] == {"type": "json_object"}
    print("ok - a JSON schema constrains decoding via response_format")


def test_stream():
    d = lambda c: {"choices": [{"delta": {"content": c}}]}
    replies.append((200, [
        d("<|chan"), d("nel>thought\nx<channel|>"), d("Hel"), d("lo\nTOOL_"), d("CALL "),
        d('{"name": "%s", "args": {"type": "flash' % A.TOOL_FUNCTION_NAME), d('cards", "topic": "cells"}}'),
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    ]))
    chunks = list(llm.stream("m", "hi", tools=[TOOL]))
    assert "".join(c.text for c in chunks).strip() == "Hello", chunks
    assert [c.tool_call for c in chunks if c.tool_call] == [
        {"name": A.TOOL_FUNCTION_NAME, "args": {"type": "flashcards", "topic": "cells"}}]
    assert chunks[-1].usage == {"prompt": 4, "reply": 2, "total": 6}
    assert requests[-1]["body"]["stream"] is True
    print("ok - streaming hides thinking and the tool call, yields the call and final usage")


def test_history_trim():
    big = "x" * (llm.PROMPT_TOKENS * 3 // 2)
    contents = [{"role": r, "parts": [{"text": big}]} for r in ("user", "model", "user", "model", "user")]
    replies.append((200, _reply("ok")))
    llm.generate("m", contents)
    msgs = requests[-1]["body"]["messages"]
    assert sum(len(m["content"]) for m in msgs) <= llm.PROMPT_TOKENS * 3 and len(msgs) < 5 and msgs[-1]["role"] == "user" and msgs[0]["role"] == "user", [m["role"] for m in msgs]
    print("ok - oldest turns are dropped to fit the context window")


def test_embeddings():
    saved = llm.EMBED_DIM
    llm.EMBED_DIM = 1
    try:
        replies.append((200, {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}))
        assert llm.embed_documents(["a", "b"], "Cells.pdf") == [[1.0], [2.0]]
        sent = requests[-1]
        assert sent["path"] == "/v1/embeddings" and sent["auth"] == "Bearer sekrit"
        assert sent["body"]["model"] == "embeddinggemma-q8"      # the id GET /models returned
        assert sent["body"]["input"] == ["title: Cells.pdf | text: a", "title: Cells.pdf | text: b"]
        replies.append((200, {"data": [{"index": 0, "embedding": [3.0]}]}))
        assert llm.embed("x") == [3.0]
        assert requests[-1]["body"]["input"] == ["task: search result | query: x"]
        # more than one request's worth is split, never sent in one go
        for _ in range(2):
            replies.append((200, {"data": [{"index": i, "embedding": [0.0]} for i in range(16)]}))
        assert len(llm.embed_documents(["t"] * 32)) == 32
        assert [len(r["body"]["input"]) for r in requests[-2:]] == [16, 16]
        # a wrong-size vector is refused, never stored or searched with
        replies.append((200, {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}))
        try:
            llm.embed("x")
            raise AssertionError("a 2-d vector was accepted")
        except llm.EmbeddingsUnavailable:
            pass
        replies.append((200, {"data": [{"index": 0, "embedding": [1.0]}]}))
        assert llm.verify_embeddings() == ("embeddinggemma-q8", 1)
    finally:
        llm.EMBED_DIM = saved
    print("ok - embeddings use the reported model, task formats, 16-input batches and a dimension check")


def test_embeddings_down():
    saved = llm.EMBED_BASE_URL
    llm.EMBED_BASE_URL = "http://127.0.0.1:9/v1"
    llm._embed_model = None
    try:
        llm.embed("x")
        raise AssertionError("an offline embedding server was swallowed")
    except llm.EmbeddingsUnavailable as e:
        assert "temporarily unavailable" in str(e)
    finally:
        llm.EMBED_BASE_URL = saved
    print("ok - an offline embedding server raises a clear error")


def test_context_too_long():
    replies.append((400, {"error": {"message": "the request exceeds the available context size"}}))
    try:
        llm.generate("m", "hi")
        raise AssertionError("HTTP 400 was swallowed")
    except llm.ContextTooLong:
        pass
    print("ok - a context overflow is its own error")


def test_errors_raise():
    llm.TRIES = 2
    replies.append((502, {"error": "tunnel down"}))
    replies.append((200, _reply("back")))
    assert llm.generate("m", "hi").text == "back"      # 5xx is retried
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
    A.load_course_name = lambda *a, **k: "Test Course"
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

    replies.append((200, _reply("Let's check what you know." + _call({"type": "quiz", "topic": "Osmosis"}))))
    r = c.post("/chat", json={"message": "can you test me on osmosis", "class_id": "c1", "chat_id": "chat1"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["response"] == "Let's check what you know."
    assert body["tool_request"] == {"type": "quiz", "topic": "Osmosis"}, body
    sent = requests[-1]["body"]
    assert sent["model"] == A.CHAT_MODEL
    assert "Osmosis is the movement" not in sent["messages"][0]["content"]       # system prompt stays stable
    assert sent["messages"][-1]["content"].startswith("Teacher material for this message:")
    assert "Osmosis is the movement" in sent["messages"][-1]["content"]
    assert sent["messages"][-1]["content"].endswith("can you test me on osmosis")

    # HTTP 400 (context exceeded): one retry with less, then the normal answer.
    replies.append((400, {"error": {"message": "request exceeds the available context size"}}))
    replies.append((200, _reply("Retried fine.")))
    r = c.post("/chat", json={"message": "what is osmosis", "class_id": "c1", "chat_id": "chat1"})
    assert r.status_code == 200 and r.get_json()["response"] == "Retried fine."

    # Embedding server offline: a clear 503, never a silent answer without retrieval.
    saved_embed = A.embed
    def _down(_t): raise llm.EmbeddingsUnavailable(llm.EMBED_UNAVAILABLE)
    A.embed = _down
    r = c.post("/chat", json={"message": "what is osmosis", "class_id": "c1", "chat_id": "chat1"})
    assert r.status_code == 503 and r.get_json()["code"] == "retrieval_unavailable", r.get_json()
    A.embed = saved_embed

    quiz = {"type": "quiz", "title": "Osmosis", "questions": [
        {"prompt": "Osmosis moves", "options": ["water", "salt", "sugar", "air"], "answer": 0,
         "explanation": "Water crosses the membrane."}] * 3}
    replies.append((200, _reply(json.dumps(quiz))))
    r = c.post("/tools/run", json={"class_id": "c1", "chat_id": "chat1",
                                   "tool_request": {"type": "quiz", "topic": "Osmosis"}})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["tool"]["questions"]) == 3
    assert requests[-1]["body"]["response_format"]["json_schema"]["schema"] == A.ACTIVITY_SCHEMAS["quiz"]

    # A failed build gets a fixed, plain message: no model call, and nothing about the internals.
    requests.clear()
    replies.append((200, _reply("not json at all")))
    r = c.post("/tools/run", json={"class_id": "c1", "chat_id": "chat1",
                                   "tool_request": {"type": "quiz", "topic": "Osmosis"}})
    assert r.status_code == 502 and r.get_json()["error"].startswith("I couldn't make that quiz"), r.get_json()
    assert len(requests) == 1 and "format" not in r.get_json()["error"]

    # The model may decline: a plain reason, no activity stored, and the prompt carries the chat.
    A.load_history = lambda *a, **k: [{"role": "user", "parts": [{"text": "tell me about mitosis"}]},
                                      {"role": "model", "parts": [{"text": "Mitosis is cell division."}]}]
    requests.clear()
    replies.append((200, _reply(json.dumps({"type": "quiz", "decline_reason": "The course has nothing on that.",
                                            "title": "", "questions": []}))))
    r = c.post("/tools/run", json={"class_id": "c1", "chat_id": "chat1",
                                   "tool_request": {"type": "quiz", "topic": "this"}})
    assert r.status_code == 422 and r.get_json()["error"] == "The course has nothing on that.", r.get_json()
    sent = requests[-1]["body"]["messages"][-1]["content"]
    assert "Student: tell me about mitosis" in sent and "NOTHING" in sent, sent
    A.load_history = lambda *a, **k: []

    # A tool the course has switched off is dropped even if the model calls it.
    replies.append((200, _reply("Here you go." + _call({"type": "concept_map", "topic": "x"}))))
    r = c.post("/chat", json={"message": "map osmosis for me", "class_id": "c1", "chat_id": "chat1"})
    assert r.get_json()["tool_request"] is None
    print("ok - /chat and /tools/run work end to end through the endpoint")


def _agent_call(name, args):
    return "TOOL_CALL " + json.dumps({"name": name, "args": args})


def test_agent_tools_and_thinking_switch():
    import agent_tools
    assert agent_tools.calculate("2 + 3 * 4").endswith("= 14")
    assert agent_tools.calculate("sqrt(16) ^ 2").endswith("= 16")
    assert agent_tools.calculate("__import__('os')").startswith("Could not")
    assert agent_tools.calculate("9 ** 9 ** 9").startswith("Could not")
    assert [c["name"] for c in llm._parse_calls(' {"name":"a","args":{}}\nTOOL_CALL {"name":"b","args":{"x":1}}')] == ["a", "b"]
    assert "UTC" in agent_tools.current_datetime("")
    assert agent_tools.current_datetime("Not/AZone").endswith("UTC")
    assert agent_tools.RENAME_CHAT not in [t["name"] for t in agent_tools.declarations(False)]
    assert agent_tools.RENAME_CHAT in [t["name"] for t in agent_tools.declarations(False, first_message=True)]
    names = [t["name"] for t in agent_tools.declarations(False)]
    assert agent_tools.WEB_SEARCH not in names and agent_tools.MARK_GAP in names
    assert agent_tools.WEB_SEARCH in [t["name"] for t in agent_tools.declarations(True)]

    A._rate_hits.clear()
    settings = dict(A.course_settings(), practice_tools=True)
    _route_fakes(settings)
    c = A.app.test_client()
    ask = lambda m="what is 12 times 13?", **kw: c.post("/chat", json=dict(
        {"message": m, "class_id": "c1", "chat_id": "chat1"}, **kw))

    # A lookup tool is the whole reply; its result goes back and the model carries on.
    requests.clear()
    replies.append((200, _reply(_agent_call("calculate", {"expression": "12*13"}))))
    replies.append((200, _reply("That's 156.")))
    r = ask()
    assert r.status_code == 200 and r.get_json()["response"] == "That's 156.", r.get_json()
    second = requests[-1]["body"]["messages"]
    assert second[-1]["content"] == "Tool result (calculate):\n12*13 = 156", second[-1]
    assert second[-2]["role"] == "assistant" and "TOOL_CALL" in second[-2]["content"]
    assert "web_search" not in second[0]["content"], "web search offered to a grounded course"

    # mark_gap and end_conversation end the reply: no extra round trip, and a reply can make several.
    seen = []
    A.summarize_exchange = lambda *a, **k: seen.append(a[-1]) or {}
    requests.clear()
    replies.append((200, _reply("The material doesn't cover that yet.\n"
                                + _agent_call("mark_gap", {"topic": "Cell division stages"}))))
    body = ask("explain mitosis please").get_json()
    assert seen[-1] == ["Cell division stages"] and body["response"] == "The material doesn't cover that yet."
    assert body["ended"] is False and len(requests) == 1
    replies.append((200, _reply("I'm going to stop here.\n" + _agent_call("end_conversation", {"reason": "abuse"}))))
    body = ask("you are useless").get_json()
    assert body["ended"] is True and body["response"] == "I'm going to stop here."
    # ...but never for a safety disclosure, whatever the model asks.
    replies.append((200, _reply("I'm here. Please talk to a trusted adult.\n"
                                + _agent_call("end_conversation", {"reason": "x"}))))
    assert ask("I want to hurt myself").get_json()["ended"] is False
    # Two calls in one reply: an activity and a gap.
    replies.append((200, _reply("Here you go.\n" + _call({"type": "quiz", "topic": "Osmosis"}) + "\n"
                                + _agent_call("mark_gap", {"topic": "Osmosis in plants"}))))
    body = ask("quiz me on osmosis").get_json()
    assert body["tool_request"] == {"type": "quiz", "topic": "Osmosis"} and seen[-1] == ["Osmosis in plants"], body

    # Web search is only offered when the teacher turned strict grounding off.
    A.load_course_settings = lambda *a, **k: dict(settings, grounded_only=False)
    requests.clear()
    replies.append((200, _reply("Fine.")))
    ask("hello again")
    assert "web_search" in requests[-1]["body"]["messages"][0]["content"]
    A.load_course_settings = lambda *a, **k: settings

    # More lookups than allowed: the last round has no lookup tools to call.
    requests.clear()
    for _ in range(A.MAX_TOOL_ROUNDS):
        replies.append((200, _reply(_agent_call("get_datetime", {}))))
    replies.append((200, _reply("It is what it is.")))
    assert ask("what day is it").get_json()["response"] == "It is what it is."
    assert "get_datetime" not in requests[-1]["body"]["messages"][0]["content"]

    # Thinking off is requested; a model that insists on reasoning is retried once with it on.
    requests.clear()
    replies.append((200, _reply("No thinking.")))
    assert ask("hello", think=False).status_code == 200
    assert requests[-1]["body"]["reasoning"] == {"enabled": False} and llm.can_disable_thinking() is True
    llm._can_disable_thinking = None
    replies.append((400, {"error": {"message": "Reasoning is mandatory for this endpoint"}}))
    replies.append((200, _reply("Thinking anyway.")))
    assert ask("hello", think=False).get_json()["response"] == "Thinking anyway."
    assert "enabled" not in requests[-1]["body"]["reasoning"] and llm.can_disable_thinking() is False
    requests.clear()
    replies.append((200, _reply("Cached.")))
    ask("hello", think=False)
    assert len(requests) == 1 and "enabled" not in requests[-1]["body"]["reasoning"]
    llm._can_disable_thinking = None
    print("ok - lookup tools loop, gaps and endings are recorded quietly, web search follows grounding, thinking can be switched off")


if __name__ == "__main__":
    try:
        test_generate_text_and_request_shape()
        test_tool_call_parsed_from_text()
        test_thinking_is_stripped()
        test_history_trim()
        test_json_schema_becomes_response_format()
        test_embeddings()
        test_embeddings_down()
        test_context_too_long()
        test_stream()
        test_errors_raise()
        test_chat_and_tool_routes_on_endpoint()
        test_agent_tools_and_thinking_switch()
    finally:
        server.shutdown()
