"""Setup and diagnostics; prints missing setting names, never their values."""
import argparse
import json
import os
from pathlib import Path
import secrets
import httpx
from dotenv import dotenv_values, load_dotenv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "env", "health", "state", "tracing"])
    parser.add_argument("--thread", default="demo")
    parser.add_argument("--user", default="student")
    args = parser.parse_args()
    if args.command == "init":
        if Path(".env").exists():
            raise SystemExit(".env exists; left unchanged. Edit it, then run check.py env.")
        text = Path(".env.example").read_text()
        for key in ["API_TOKEN", "POSTGRES_PASSWORD"]:
            text = text.replace(key + "=\n", key + "=" + secrets.token_hex(24) + "\n")
        Path(".env").write_text(text)
        print("Created .env with random API and database credentials. Fill the model settings.")
        return
    load_dotenv()
    if args.command == "env":
        values = dotenv_values(".env")
        needed = ["LLM_API_KEY", "MODEL_CHEAP", "API_TOKEN", "POSTGRES_PASSWORD"]
        if values.get("LLM_PROVIDER", "openai_compat") != "google_genai":
            needed.append("LLM_BASE_URL")
        missing = [key for key in needed if not values.get(key)]
        if missing:
            raise SystemExit("Fill in .env: " + ", ".join(missing))
        if len(values["API_TOKEN"]) < 24:
            raise SystemExit("API_TOKEN must contain at least 24 random characters.")
        if not values["POSTGRES_PASSWORD"].isalnum():
            raise SystemExit("Use the generated letters/digits password; special characters need URL encoding.")
        print("Required .env settings: OK. Values are not printed.")
        return
    if args.command == "tracing":
        from langfuse import get_client
        if not os.getenv("LANGFUSE_PUBLIC_KEY"):
            raise SystemExit("Fill the Langfuse keys and URL from lesson 2 first.")
        assert get_client().auth_check(), "Check the Langfuse project and URL."
        print("Langfuse connection: OK")
        return
    base = os.getenv("API_URL", "http://localhost:8108").rstrip("/")
    if args.command == "health":
        response = httpx.get(base + "/health", timeout=10)
    else:
        response = httpx.get(base + "/state/" + args.thread,
            params={"user_id": args.user}, timeout=10,
            headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]})
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
