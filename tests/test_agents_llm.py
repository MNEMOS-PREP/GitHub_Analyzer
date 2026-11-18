"""Tests for the OpenAI-compatible client and the scripted fake. No network access."""

from __future__ import annotations

import http.client
import http.server
import json
import threading

import pytest
from agent_fakes import FakeClient, tool_call

from gh_analyzer.agents.llm import LLMError, OpenAICompatClient, Usage

KEY = "gsk_" + "Qw3rTy7uIo9pAs1dFg5hJk8lZx2cVb4n"
OK_BODY = json.dumps({
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
})


class Transport:
    """Scripted transport: each step is (status, text) or an exception to raise."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def make(transport, sleeps=None, key=KEY):
    sleeps = sleeps if sleeps is not None else []
    return OpenAICompatClient(api_key=key, base_url="https://llm.local/v1", model="m",
                              transport=transport, sleep=sleeps.append)


MSGS = [{"role": "user", "content": "hello"}]


def test_missing_key_raises_without_leaking(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    c = OpenAICompatClient(transport=Transport())
    with pytest.raises(LLMError) as e:
        c.chat(MSGS)
    assert "LLM_API_KEY" in str(e.value)
    assert KEY not in str(e.value)


def test_env_defaults(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    t = Transport((200, OK_BODY))
    OpenAICompatClient(transport=t).chat(MSGS)
    assert t.calls[0]["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert json.loads(t.calls[0]["body"])["model"] == "openai/gpt-oss-120b"
    assert t.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"


def test_retries_then_succeeds():
    t = Transport((429, "slow down"), (500, "oops"), (200, OK_BODY))
    sleeps = []
    msg, usage = make(t, sleeps).chat(MSGS)
    assert len(t.calls) == 3
    assert sleeps == [1, 2]
    assert msg["content"] == "hi"


def test_retries_transport_errors():
    t = Transport(TimeoutError("t"), OSError("down"), (200, OK_BODY))
    sleeps = []
    make(t, sleeps).chat(MSGS)
    assert len(t.calls) == 3 and sleeps == [1, 2]


def test_gives_up_after_six():
    t = Transport(*[(503, "busy")] * 6)
    sleeps = []
    with pytest.raises(LLMError) as e:
        make(t, sleeps).chat(MSGS)
    assert len(t.calls) == 6
    assert sleeps == [1, 2, 4, 8, 16]
    assert KEY not in str(e.value)


def test_4xx_is_not_retried():
    t = Transport((400, "bad request"), (200, OK_BODY))
    sleeps = []
    with pytest.raises(LLMError):
        make(t, sleeps).chat(MSGS)
    assert len(t.calls) == 1 and sleeps == []


def test_usage_parsed_from_response():
    _, usage = make(Transport((200, OK_BODY))).chat(MSGS)
    assert usage == Usage(11, 7) and usage.total == 18


def test_usage_absent_or_odd_is_zero_or_coerced():
    body = json.dumps({"choices": [{"message": {"content": "x"}}]})
    assert make(Transport((200, body))).chat(MSGS)[1] == Usage(0, 0)
    body = json.dumps({"choices": [{"message": {"content": "x"}}],
                       "usage": {"prompt_tokens": "12", "completion_tokens": None}})
    assert make(Transport((200, body))).chat(MSGS)[1] == Usage(12, 0)
    body = json.dumps({"choices": [{"message": {"content": "x"}}], "usage": "bad"})
    assert make(Transport((200, body))).chat(MSGS)[1] == Usage(0, 0)


def test_usage_add():
    u = Usage(1, 2)
    u.add(Usage(10, 20))
    assert u == Usage(11, 22)


def test_http_error_message_has_no_key():
    t = Transport((401, f"bad key {KEY} for Bearer {KEY} " + "z" * 500))
    with pytest.raises(LLMError) as e:
        make(t).chat(MSGS)
    s = str(e.value)
    assert s.startswith("HTTP 401")
    assert KEY not in s
    assert len(s) <= len("HTTP 401") + 210


def test_key_never_in_repr_or_url():
    t = Transport((200, OK_BODY))
    c = make(t)
    c.chat(MSGS)
    assert KEY not in repr(c) and KEY not in str(c)
    assert "llm.local" in repr(c)
    assert KEY not in t.calls[0]["url"]


@pytest.mark.parametrize("body", [
    "not json", "[]", "{}", '{"choices": []}', '{"choices": [{}]}',
    '{"choices": ["x"]}', '{"choices": [{"message": "str"}]}', '{"choices": {}}',
])
def test_malformed_response_raises_llmerror(body):
    with pytest.raises(LLMError):
        make(Transport((200, body))).chat(MSGS)


def test_oversized_response_rejected():
    with pytest.raises(LLMError):
        make(Transport((200, "x" * (2 * 1024 * 1024 + 1)))).chat(MSGS)


def test_request_shape_and_optional_fields():
    t = Transport((200, OK_BODY), (200, OK_BODY))
    c = make(t)
    c.chat(MSGS)
    body = json.loads(t.calls[0]["body"])
    assert "tools" not in body and "tool_choice" not in body
    tools = [{"type": "function", "function": {"name": "f"}}]
    choice = {"type": "function", "function": {"name": "f"}}
    c.chat(MSGS, tools=tools, tool_choice=choice, temperature=0.0, max_tokens=99)
    body = json.loads(t.calls[1]["body"])
    assert body["tools"] == tools and body["tool_choice"] == choice
    assert body["temperature"] == 0.0 and body["max_tokens"] == 99


def test_secrets_in_messages_redacted_before_transport():
    secret = "ghp_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"
    msgs = [
        {"role": "system", "content": f"deploy with {secret} now"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": f"token={secret}"},
    ]
    t = Transport((200, OK_BODY))
    make(t).chat(msgs)
    assert secret.encode() not in t.calls[0]["body"]
    sent = json.loads(t.calls[0]["body"])["messages"]
    assert sent[1]["content"] is None
    assert sent[1]["tool_calls"] == msgs[1]["tool_calls"]
    assert sent[2]["tool_call_id"] == "c1" and sent[2]["role"] == "tool"
    assert secret in msgs[0]["content"]  # caller's list is not mutated


def test_fake_client_records_calls_and_exhausts():
    fc = FakeClient([
        {"role": "assistant", "content": "a"},
        ({"role": "assistant", "content": "b"}, Usage(3, 4)),
        lambda messages: {"role": "assistant", "content": f"saw {len(messages)}"},
        LLMError("boom"),
    ])
    m1 = [{"role": "user", "content": "q"}]
    msg, usage = fc.chat(m1, tools=[{"x": 1}], tool_choice={"t": 1}, max_tokens=7)
    assert msg["content"] == "a" and usage == Usage(100, 50)
    m1.append({"role": "user", "content": "later"})  # recorded call must be a snapshot
    assert len(fc.calls[0]["messages"]) == 1
    assert fc.calls[0]["tools"] == [{"x": 1}] and fc.calls[0]["tool_choice"] == {"t": 1}
    assert fc.calls[0]["max_tokens"] == 7
    assert fc.chat(m1)[1] == Usage(3, 4)
    assert fc.chat(m1)[0]["content"] == "saw 2"
    with pytest.raises(LLMError):
        fc.chat(m1)
    with pytest.raises(AssertionError, match="FakeClient exhausted"):
        fc.chat(m1)


def test_tool_call_helper():
    m = tool_call("read", {"a": 1}, call_id="c9")
    assert m["role"] == "assistant" and m["content"] is None
    tc = m["tool_calls"][0]
    assert tc["id"] == "c9" and tc["function"]["name"] == "read"
    assert json.loads(tc["function"]["arguments"]) == {"a": 1}
    assert tool_call("read", "{bad", content="hm")["tool_calls"][0]["function"]["arguments"] == "{bad"


# --- redirects, URL validation, message sanitising, transport failures ---------------

SECRET = "ghp_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"


@pytest.fixture
def two_servers(request):
    hits_b: list[dict] = []

    class B(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits_b.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(OK_BODY.encode())

        def log_message(self, *a):
            pass

    srv_b = http.server.HTTPServer(("127.0.0.1", 0), B)
    port_b = srv_b.server_address[1]

    class A(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port_b}/v1/chat/completions")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv_a = http.server.HTTPServer(("127.0.0.1", 0), A)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (srv_a, srv_b)]
    for t in threads:
        t.start()

    def fin():
        for s in (srv_a, srv_b):
            s.shutdown()
            s.server_close()
    request.addfinalizer(fin)
    return srv_a.server_address[1], hits_b


def test_redirect_is_not_followed_and_key_not_forwarded(two_servers):
    port_a, hits_b = two_servers
    sleeps = []
    c = OpenAICompatClient(api_key=KEY, base_url=f"http://127.0.0.1:{port_a}/v1",
                           sleep=sleeps.append)
    with pytest.raises(LLMError) as e:
        c.chat(MSGS)
    assert str(e.value).startswith("HTTP 302")
    assert hits_b == []
    assert sleeps == []  # 3xx is not retried
    assert KEY not in str(e.value)


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1", "https://llm.invalid/v1",
    "http://localhost:8080/v1", "http://127.0.0.1:11434/v1", "http://[::1]:8000/v1",
])
def test_base_url_accepted(url):
    t = Transport((200, OK_BODY))
    OpenAICompatClient(api_key=KEY, base_url=url, transport=t).chat(MSGS)
    assert len(t.calls) == 1


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://host/v1", "http://example.com/v1", "http://localhost.evil.com/v1",
    "http://127.0.0.2/v1", "llm.example.com/v1", "https:///v1", "https://", "http://[::1",
    "https://user:pw@host.example/v1", "https://user@host.example/v1", "javascript:alert(1)",
    "https://host.example:notaport/v1",
])
def test_base_url_refused_instantly(url):
    t = Transport()
    sleeps = []
    c = OpenAICompatClient(api_key=KEY, base_url=url, transport=t, sleep=sleeps.append)
    with pytest.raises(LLMError) as e:
        c.chat(MSGS)
    assert t.calls == [] and sleeps == []
    assert KEY not in str(e.value) and "pw" not in str(e.value)


def test_base_url_error_names_only_scheme_and_host():
    c = OpenAICompatClient(api_key=KEY, base_url="http://example.com/secretpath?token=abc123",
                           transport=Transport())
    with pytest.raises(LLMError) as e:
        c.chat(MSGS)
    s = str(e.value)
    assert "example.com" in s and "secretpath" not in s and "abc123" not in s


def _sent(msgs):
    t = Transport((200, OK_BODY))
    make(t).chat(msgs)
    assert SECRET.encode() not in t.calls[0]["body"]
    return json.loads(t.calls[0]["body"])["messages"]


def test_list_content_text_parts_redacted():
    img = {"type": "image_url", "image_url": {"url": "https://img.invalid/a.png"}}
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": f"use {SECRET}"}, img, {"type": "text", "text": "plain"}, "odd"]}]
    sent = _sent(msgs)
    assert sent[0]["content"][1] == img and sent[0]["content"][2]["text"] == "plain"
    assert sent[0]["content"][3] == "odd"
    assert SECRET in msgs[0]["content"][0]["text"]  # caller untouched


def test_tool_call_arguments_redacted_and_stay_valid_json():
    args = json.dumps({"path": "a.py", "token": SECRET})
    msgs = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": args}}]}]
    sent = _sent(msgs)
    sent_args = sent[0]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(sent_args)["path"] == "a.py"
    assert sent[0]["content"] is None and sent[0]["tool_calls"][0]["id"] == "c1"
    assert msgs[0]["tool_calls"][0]["function"]["arguments"] == args  # caller untouched


def test_tool_call_arguments_invalid_json_becomes_empty_object():
    msgs = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{bad " + SECRET}}]}]
    sent = _sent(msgs)
    assert sent[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert msgs[0]["tool_calls"][0]["function"]["arguments"].startswith("{bad")


@pytest.mark.parametrize("exc", [
    ValueError("boom " + KEY), RuntimeError("x"), http.client.IncompleteRead(b"ab"),
])
def test_transport_exceptions_are_wrapped_or_retried(exc):
    t = Transport(exc, exc, exc, exc, exc, exc)
    sleeps = []
    with pytest.raises(LLMError) as e:
        make(t, sleeps).chat(MSGS)
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    assert e.value.__cause__ is None and e.value.__context__ is None
    if isinstance(exc, http.client.HTTPException):
        assert len(t.calls) == 6
    else:
        assert str(e.value) == "transport failed" and len(t.calls) == 1


def test_transport_keyboard_interrupt_propagates():
    with pytest.raises(KeyboardInterrupt):
        make(Transport(KeyboardInterrupt())).chat(MSGS)


def test_http_exception_then_success():
    t = Transport(http.client.BadStatusLine("x"), (200, OK_BODY))
    make(t).chat(MSGS)
    assert len(t.calls) == 2


def test_usage_overflow_is_zero():
    body = '{"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": 1e999, "completion_tokens": 5}}'
    assert make(Transport((200, body))).chat(MSGS)[1] == Usage(0, 5)


@pytest.mark.parametrize("key", ["", "   ", " " + chr(10) + chr(9)])
def test_blank_key_counts_as_missing(key, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        make(Transport(), key=key).chat(MSGS)


def test_key_with_whitespace_is_stripped_in_header():
    t = Transport((200, OK_BODY))
    make(t, key="  " + KEY + chr(10)).chat(MSGS)
    assert t.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"


def test_no_error_path_leaks_key_or_instance_dict():
    cases = [(401, f"{KEY}"), (400, f"Bearer {KEY}"), (200, "junk"), (200, "{}"),
             (503, KEY)] 
    for status, text in cases:
        t = Transport(*[(status, text)] * 6)
        c = make(t)
        with pytest.raises(LLMError) as e:
            c.chat(MSGS)
        for s in (str(e.value), repr(e.value)):
            assert KEY not in s and "_api_key" not in s
    assert KEY not in repr(c) and "_api_key" not in repr(c)
