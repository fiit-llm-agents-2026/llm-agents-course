"""One process owns the agent, durable state and an authenticated HTTP interface."""
import asyncio
import json
import logging
import os
import secrets
from contextlib import AsyncExitStack, asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent import build_agent

load_dotenv()
log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app):
    token = os.getenv("API_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("Set API_TOKEN to a random string of at least 24 characters.")
    app.state.token = token
    app.state.busy = set()
    app.state.handler = None
    async with AsyncExitStack() as stack:
        url = os.getenv("DATABASE_URL")
        if url:
            saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(url))
            store = await stack.enter_async_context(AsyncPostgresStore.from_conn_string(url))
            await saver.setup()
            await store.setup()
        else:
            saver, store = InMemorySaver(), InMemoryStore()
        app.state.lane = "postgres" if url else "memory"
        app.state.store = store
        app.state.graph = build_agent(saver, store)
        if os.getenv("LANGFUSE_PUBLIC_KEY"):
            from langfuse import get_client
            from langfuse.langchain import CallbackHandler
            app.state.handler = CallbackHandler()
            stack.callback(get_client().flush)
        yield


app = FastAPI(title="Course deployment", lifespan=lifespan)


def authorize(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, f"Bearer {app.state.token}"):
        raise HTTPException(401, "Invalid API token")


class Identity(BaseModel):
    user_id: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")


class ChatIn(Identity):
    text: str = Field(min_length=1, max_length=12000)


class ResumeIn(Identity):
    interrupt_id: str
    decisions: list[dict] = Field(min_length=1)


def config_for(thread_id, user_id):
    # A user's conversation and cross-conversation memory use different keys.
    key = f"{user_id}:{thread_id}"
    return {
        "run_name": "project/chat", "recursion_limit": 25,
        "configurable": {"thread_id": key, "user_id": user_id},
        "callbacks": [app.state.handler] if app.state.handler else [],
        "metadata": {"langfuse_session_id": key, "langfuse_user_id": user_id,
                     "langfuse_tags": ["session-08", "api"]},
    }


def pending(state):
    return [item for task in state.tasks for item in task.interrupts]


def event(kind, **fields):
    # JSON keeps newlines inside the payload instead of breaking SSE framing.
    return "data: " + json.dumps({"type": kind, **fields}, ensure_ascii=False) + "\n\n"


@app.get("/health")
async def health():
    return {"status": "ok", "persistence": app.state.lane}


@app.get("/state/{thread_id}", dependencies=[Depends(authorize)])
async def state(thread_id: str, user_id: str):
    cfg = config_for(thread_id, user_id)
    snapshot = await app.state.graph.aget_state(cfg)
    return {"messages": len(snapshot.values.get("messages", [])),
            "interrupts": [{"id": i.id, "value": i.value} for i in pending(snapshot)]}


async def start_run(thread_id, body, resume=False):
    cfg = config_for(thread_id, body.user_id)
    key = cfg["configurable"]["thread_id"]
    # One worker rejects overlapping requests to the same conversation.
    if key in app.state.busy:
        raise HTTPException(409, "This conversation is busy; wait for its response.")
    app.state.busy.add(key)
    try:
        pauses = pending(await app.state.graph.aget_state(cfg))
        if resume:
            if len(pauses) != 1 or pauses[0].id != body.interrupt_id:
                raise HTTPException(409, "Approval is stale; fetch /state again.")
            value = pauses[0].value
            if not isinstance(value, dict) or "action_requests" not in value:
                raise HTTPException(422, "This client supports HumanInTheLoopMiddleware pauses.")
            actions, reviews = value["action_requests"], value["review_configs"]
            allowed = {r["action_name"]: r["allowed_decisions"] for r in reviews}
            if len(actions) != len(body.decisions):
                raise HTTPException(422, "One decision is required per action, in the same order.")
            for action, decision in zip(actions, body.decisions):
                if decision.get("type") not in allowed[action["name"]]:
                    raise HTTPException(422, "Decision is not allowed for this action.")
                if decision["type"] == "edit":
                    edited = decision.get("edited_action", {})
                    if edited.get("name") != action["name"] or not isinstance(edited.get("args"), dict):
                        raise HTTPException(422, "Edit requires edited_action with the same name and new args.")
            data = Command(resume={pauses[0].id: {"decisions": body.decisions}})
        else:
            if pauses:
                raise HTTPException(409, "Awaiting approval; use /state and /resume.")
            data = {"messages": [("user", body.text)]}
    except BaseException:
        app.state.busy.discard(key)
        raise

    async def stream():
        try:
            async for message, metadata in app.state.graph.astream(data, cfg, stream_mode="messages"):
                if message.type in {"ai", "AIMessageChunk"} and message.text:
                    yield event("text", text=message.text)
            pauses = pending(await app.state.graph.aget_state(cfg))
            for pause in pauses:
                yield event("interrupt", id=pause.id, value=pause.value)
            yield event("done", waiting=bool(pauses))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Agent failed on thread %s", key)
            yield event("error", message="Agent failed. Check docker compose logs api.")
        finally:
            app.state.busy.discard(key)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/chat/{thread_id}", dependencies=[Depends(authorize)])
async def chat(thread_id: str, body: ChatIn):
    return await start_run(thread_id, body)


@app.post("/resume/{thread_id}", dependencies=[Depends(authorize)])
async def resume(thread_id: str, body: ResumeIn):
    return await start_run(thread_id, body, resume=True)
