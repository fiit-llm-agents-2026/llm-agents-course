"""Shared SSE decoder for the command-line client and Telegram bot."""
import argparse
import asyncio
import json
import os
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv()


async def ask(path, thread_id, payload, client=None):
    if client is None:
        async with httpx.AsyncClient(timeout=180) as connection:
            return await ask(path, thread_id, payload, connection)
    base = os.getenv("API_URL", "http://localhost:8108").rstrip("/")
    headers = {"Authorization": "Bearer " + os.environ["API_TOKEN"]}
    answer, pauses, done = [], [], False
    async with client.stream("POST", f"{base}/{path}/{quote(thread_id, safe='')}",
                             json=payload, headers=headers) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            item = json.loads(line[6:])
            if item["type"] == "error":
                raise RuntimeError(item["message"])
            if item["type"] == "text":
                answer.append(item["text"])
            elif item["type"] == "interrupt":
                pauses.append(item)
            elif item["type"] == "done":
                done = True
    if not done:
        raise RuntimeError("The stream ended without completion; check the API before retrying.")
    return {"text": "".join(answer), "interrupts": pauses}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--thread", default="demo")
    parser.add_argument("--user", default="student")
    parser.add_argument("--interrupt")
    parser.add_argument("--decision", choices=["approve", "reject", "edit"])
    parser.add_argument("--args", dest="edited_args", help="JSON arguments for one edited action")
    args = parser.parse_args()
    payload = {"text": args.text, "user_id": args.user}
    path = "chat"
    if args.decision:
        if not args.interrupt:
            parser.error("Pass the --interrupt id shown in the saved state.")
        response = httpx.get(os.getenv("API_URL", "http://localhost:8108") + "/state/" + args.thread,
            params={"user_id": args.user}, headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]})
        response.raise_for_status()
        pause = next((p for p in response.json()["interrupts"] if p["id"] == args.interrupt), None)
        if pause is None:
            parser.error("Pause no longer exists; fetch state again.")
        actions = pause["value"]["action_requests"]
        decisions = [{"type": args.decision} for _ in actions]
        if args.decision == "edit":
            if len(actions) != 1 or not args.edited_args:
                parser.error("Edit accepts one action and --args with its full new arguments.")
            decisions[0]["edited_action"] = {"name": actions[0]["name"], "args": json.loads(args.edited_args)}
        payload = {"user_id": args.user, "interrupt_id": args.interrupt, "decisions": decisions}
        path = "resume"
    print(json.dumps(asyncio.run(ask(path, args.thread, payload)),
                     ensure_ascii=False, indent=2))
