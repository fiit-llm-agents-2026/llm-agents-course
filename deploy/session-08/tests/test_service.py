import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

import agent
import api
import bot
from client import ask

TOKEN = "test-only-api-token-1234567890"
HEADERS = {"Authorization": "Bearer " + TOKEN}


class FakeModel(GenericFakeChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("MODEL_CHEAP", "fake")
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setenv("LLM_BASE_URL", "http://fake.local")
    model = FakeModel(messages=iter([AIMessage(content="First\n\nSecond"), AIMessage(content="Follow-up")]),
                      disable_streaming=True)
    monkeypatch.setattr(agent, "init_chat_model", lambda *a, **kw: model)
    return api.app


def events(response):
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def chat(client, text="Hello", thread="test", user="student"):
    return client.post(f"/chat/{thread}", json={"text": text, "user_id": user}, headers=HEADERS)


def test_newlines_history_and_auth(service):
    with TestClient(service) as c:
        assert c.get("/health").json()["persistence"] == "memory"
        assert c.post("/chat/x", json={"text": "hello", "user_id": "student"}).status_code == 401
        assert chat(c, "").status_code == 422
        assert events(chat(c))[0] == {"type": "text", "text": "First\n\nSecond"}
        assert events(chat(c, "And then?"))[-1] == {"type": "done", "waiting": False}
        state = c.get("/state/test?user_id=student", headers=HEADERS).json()
        assert state["messages"] == 4
        assert c.get("/state/test?user_id=other", headers=HEADERS).json()["messages"] == 0


@pytest.mark.parametrize("kind", ["approve", "reject", "edit"])
def test_real_middleware_pause_resume_and_stale_click(service, monkeypatch, kind):
    script = [AIMessage(content="", tool_calls=[{
        "name": "request_delivery", "args": {"note": "Original"}, "id": "call-1"
    }]), AIMessage(content="Decision handled")]
    monkeypatch.setattr(agent, "init_chat_model", lambda *a, **kw: FakeModel(
        messages=iter(script), disable_streaming=True))
    with TestClient(service) as c:
        result = events(chat(c, "Arrange a delivery"))
        pause = next(e for e in result if e["type"] == "interrupt")
        assert result[-1] == {"type": "done", "waiting": True}
        assert pause["value"]["action_requests"][0]["args"] == {"note": "Original"}
        assert chat(c, "Another question").status_code == 409
        assert c.get("/state/test?user_id=student", headers=HEADERS).json()["interrupts"][0]["id"] == pause["id"]
        decision = {"type": kind}
        if kind == "edit":
            decision["edited_action"] = {"name": "request_delivery", "args": {"note": "Edited"}}
        payload = {"user_id": "student", "interrupt_id": pause["id"], "decisions": [decision]}
        invalid = {**payload, "decisions": [{"type": "invented"}]}
        assert c.post("/resume/test", json=invalid, headers=HEADERS).status_code == 422
        answer = events(c.post("/resume/test", json=payload, headers=HEADERS))
        assert answer[-1] == {"type": "done", "waiting": False}
        cfg = api.config_for("test", "student")
        state = c.portal.call(service.state.graph.aget_state, cfg)
        tools = [m for m in state.values["messages"] if m.type == "tool"]
        expected = "Edited" if kind == "edit" else "Original"
        if kind == "reject":
            assert all("Demo request approved:" not in m.content for m in tools)
        else:
            assert any(m.content == "Demo request approved: " + expected for m in tools)
        assert c.post("/resume/test", json=payload, headers=HEADERS).status_code == 409


def test_langmem_uses_real_store_and_user_namespace(service, monkeypatch):
    script = [AIMessage(content="", tool_calls=[{
        "name": "manage_memory", "args": {"content": "Prefers evening delivery", "action": "create"}, "id": "save"
    }]), AIMessage(content="Saved"), AIMessage(content="", tool_calls=[{
        "name": "search_memory", "args": {"query": ""}, "id": "find"
    }]), AIMessage(content="Evening delivery")]
    monkeypatch.setattr(agent, "init_chat_model", lambda *a, **kw: FakeModel(
        messages=iter(script), disable_streaming=True))
    with TestClient(service) as c:
        chat(c, "Remember evening delivery", thread="one")
        result = events(chat(c, "What do I prefer?", thread="two"))
        assert any(e.get("text") == "Evening delivery" for e in result)
        records = c.portal.call(service.state.store.asearch, ("users", "student"))
        assert len(records) == 1
        assert "evening" in json.dumps(records[0].value).lower()
        assert c.portal.call(service.state.store.asearch, ("users", "other")) == []


def test_busy_and_model_error(service):
    class Broken:
        async def aget_state(self, cfg):
            return SimpleNamespace(tasks=[])
        async def astream(self, *a, **kw):
            raise RuntimeError("Provider failure with a private diagnostic")
            yield
    with TestClient(service) as c:
        service.state.busy.add("student:test")
        assert chat(c).status_code == 409
        service.state.busy.clear()
        service.state.graph = Broken()
        result = events(chat(c))
        assert [e["type"] for e in result] == ["error"]
        assert "private diagnostic" not in json.dumps(result)
        assert not service.state.busy


@pytest.mark.parametrize("tail", ["", 'data: {"type":"error","message":"Failure"}\n\n'])
def test_client_rejects_incomplete_or_failed_response(monkeypatch, tail):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(
            200, text='data: {"type":"text","text":"partial"}\n\n'+tail))) as c:
            with pytest.raises(RuntimeError):
                await ask("chat", "x", {"user_id": "student", "text": "hi"}, c)
    asyncio.run(run())


def test_client_and_bot_share_protocol(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    pause={"type":"interrupt","id":"a"*32,"value":{
        "action_requests":[{"name":"request_delivery","args":{"note":"At six"}}],
        "review_configs":[{"action_name":"request_delivery","allowed_decisions":["approve","reject"]}]}}
    wire=''.join(api.event(e.pop("type"), **e) for e in [
        {"type":"text","text":"A\n\nB"},pause,{"type":"done","waiting":True}])
    sent=[]
    async def answer(text, **kw):sent.append((text,kw))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req:httpx.Response(200,text=wire))) as c:
            result=await ask("chat","x",{"text":"hi","user_id":"student"},c)
        await bot.show(SimpleNamespace(answer=answer),result)
    asyncio.run(run())
    assert sent[0][0]=="A\n\nB"
    assert "At six" in sent[2][0]
    buttons=sent[-1][1]["reply_markup"].inline_keyboard[0]
    assert len(buttons)==2 and all(len(b.callback_data.encode())<=64 for b in buttons)


def test_bot_handles_non_text_groups_and_long_answers(monkeypatch):
    sent=[]
    async def answer(text,**kw):sent.append(text)
    async def run():
        await bot.on_message(SimpleNamespace(chat=SimpleNamespace(type="group"),answer=answer))
        await bot.on_message(SimpleNamespace(chat=SimpleNamespace(type="private"),text=None,answer=answer))
        await bot.show(SimpleNamespace(answer=answer),{"text":"x"*8000,"interrupts":[]})
    asyncio.run(run())
    assert len(sent)==5 and all(len(t)<=3500 for t in sent)


def test_bot_removes_markdown_without_losing_code_or_links():
    source = "**RR-1001** is *in transit*.\n\n- [Support](https://example.com)\n- `**literal**`"
    rendered = bot.plain_text(source)
    assert rendered == "RR-1001 is in transit.\n\n• Support (https://example.com)\n• **literal**"
    assert bot.plain_text("```python\n2 ** 3\n```") == "2 ** 3"
    assert bot.plain_text("<b>Keep actual text</b>") == "<b>Keep actual text</b>"


def test_action_card_keeps_all_arguments_without_duplicate_description():
    card = bot.action_card([{"name": "request_delivery", "args": {
        "note": "Friday at 18:00", "options": {"floor": 2}, "count": 1,
    }, "description": "Internal description"}])
    assert "Заявка на доставку" in card
    assert "Примечание: Friday at 18:00" in card
    assert 'options: {"floor": 2}' in card and "count: 1" in card
    assert "Internal description" not in card
