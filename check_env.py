"""Run this before session 1, and again at the start of session 2.

Every line must say PASS. The last check is the one that matters: it makes two
tool calls in one conversation where the second is only possible once the first
has returned. Plenty of endpoints answer a single chat message and cannot do
that, and the difference does not show up until you are three sessions in.

    uv run python check_env.py

Failures print a code and a fix, never a traceback. Commit the output into your
own repository as runs/session-00.txt; it is the first artifact of the course.

This file is also the reference for how the course reaches a provider. Session 1
builds its client in three lines and does not need any of this; from session 2
you will want something like the two functions below in your own repository.
"""

from __future__ import annotations

import importlib.metadata as metadata
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RESULTS: list[tuple[bool, str, str]] = []
PACKAGES = ("langchain", "langgraph", "langchain-core", "openai")
PROVIDERS = ("openai_compat", "google_genai")

# langchain-openai resolves a base URL in this order: explicit argument, then
# OPENAI_API_BASE, then OPENAI_BASE_URL. A stale OPENAI_API_BASE exported in a
# shell profile therefore beats a correct .env, and the symptom is a request
# going somewhere you never configured. The course uses its own variable names
# and passes the endpoint explicitly, and this script refuses to run when the
# OpenAI ones are set.
SHADOWING_VARS = ("OPENAI_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_KEY")


class ConfigError(RuntimeError):
    """Something in .env is missing or contradictory. The message says what."""


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is empty. Set it in .env.")
    return value


def provider() -> str:
    value = os.getenv("LLM_PROVIDER", "openai_compat").strip()
    if value not in PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER is {value!r}, expected one of {' | '.join(PROVIDERS)}."
        )
    return value


def model_id(size: str = "cheap") -> str:
    if size not in ("cheap", "strong"):
        raise ConfigError(f"size is {size!r}, expected 'cheap' or 'strong'.")
    return _require(f"MODEL_{size.upper()}")


def chat_model(size: str = "cheap", **kwargs):
    """A LangChain chat model for the configured provider.

    Gemini needs its own client class rather than an OpenAI-compatible URL. Over
    the compatible endpoint it returns tool calls on the first turn and then
    fails on the second, because the reasoning signatures that ride along with
    an assistant message are not carried across.

    No temperature is set anywhere in this course. Current Gemini models are
    documented to loop or degrade when it is moved off the default, and a loop
    inside an agent burns a day of quota in one run.
    """
    from langchain.chat_models import init_chat_model

    name = model_id(size)
    secret = _require("LLM_API_KEY")
    if provider() == "google_genai":
        return init_chat_model(f"google_genai:{name}", api_key=secret, **kwargs)
    return init_chat_model(
        f"openai:{name}", api_key=secret, base_url=_require("LLM_BASE_URL"), **kwargs
    )


def describe() -> str:
    endpoint = os.getenv("LLM_BASE_URL", "").strip() or "provider default"
    return (
        f"provider={provider()} endpoint={endpoint} "
        f"cheap={os.getenv('MODEL_CHEAP', '')} strong={os.getenv('MODEL_STRONG', '')}"
    )


def record(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    return ok


def fail(code: str, message: str, fix: str) -> None:
    record(False, code, message)
    print(f"      fix: {fix}")


def check_python() -> bool:
    version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    if sys.version_info < (3, 10):
        fail("E01 python", f"found {version}", "install Python 3.10 or newer")
        return False
    return record(True, "python", version)


def check_packages() -> bool:
    missing = []
    found = []
    for name in PACKAGES:
        try:
            found.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        fail("E02 packages", f"missing {', '.join(missing)}", "run: uv sync")
        return False
    return record(True, "packages", ", ".join(found))


def check_git() -> bool:
    if shutil.which("git") is None:
        fail(
            "E03 git", "not on PATH", "install git; the course submits work as commits"
        )
        return False
    return record(True, "git", "found")


def check_env_file() -> bool:
    path = Path(".env")
    if not path.exists():
        fail(
            "E04 .env",
            "not found in the current directory",
            "copy .env.example to .env and fill it in",
        )
        return False
    return record(True, ".env", str(path.resolve()))


def check_no_shadowing() -> bool:
    """The variable that makes a correct .env stop working.

    langchain-openai prefers OPENAI_API_BASE over OPENAI_BASE_URL, so a value
    left in a shell profile years ago quietly wins and your requests go
    somewhere you never configured. The course passes the endpoint explicitly
    and keeps these names out of the way entirely.
    """
    present = [name for name in SHADOWING_VARS if os.environ.get(name)]
    if present:
        fail(
            "E05 environment",
            f"{', '.join(present)} set outside .env",
            "unset them in your shell profile; the course uses LLM_API_KEY and LLM_BASE_URL",
        )
        return False
    return record(True, "environment", "no shadowing variables")


def check_config() -> bool:
    try:
        line = describe()
        model_id("cheap")
        model_id("strong")
    except ConfigError as error:
        fail("E06 config", str(error), "see .env.example")
        return False
    return record(True, "config", line)


def check_tool_loop() -> bool:
    """Two dependent tool calls in one conversation."""
    from langchain_core.messages import HumanMessage, ToolMessage
    from langchain_core.tools import tool

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers and return the sum."""
        return a + b

    question = "What is 17 plus 25, and then add 8 to that result? Use the tool for both steps."
    model = chat_model("strong").bind_tools([add])
    messages = [HumanMessage(question)]
    calls = 0
    results = 0
    usage = {}

    try:
        for _ in range(4):
            answer = model.invoke(messages)
            messages.append(answer)  # the whole object, never a rebuilt copy
            usage = getattr(answer, "usage_metadata", None) or usage
            if not answer.tool_calls:
                break
            for call in answer.tool_calls:
                calls += 1
                output = add.invoke(call["args"])
                results += 1
                messages.append(ToolMessage(str(output), tool_call_id=call["id"]))
    except Exception as error:  # noqa: BLE001 — the point is to name the failure
        return _explain_exception(error)

    text = str(getattr(messages[-1], "text", "") or messages[-1].content)
    if calls == 0:
        fail(
            "E20 tool calling",
            "the model answered without calling the tool",
            "this endpoint does not do native tool calling; change MODEL_STRONG or the provider",
        )
        return False
    if calls == 1:
        fail(
            "E21 chaining",
            "the model called the tool once and stopped",
            "it cannot chain dependent calls and will fail from session 2; change provider now",
        )
        return False
    if "50" not in text:
        fail(
            "E22 result",
            f"{calls} calls but the answer was: {text[:80]!r}",
            "the model loses the intermediate result; try MODEL_STRONG on a larger model",
        )
        return False
    tokens = (
        f", tokens in/out {usage.get('input_tokens')}/{usage.get('output_tokens')}"
        if usage
        else ""
    )
    return record(
        True, "tool loop", f"{calls} calls, {results} results, answer holds 50{tokens}"
    )


def _explain_exception(error: Exception) -> bool:
    text = f"{type(error).__name__}: {error}"
    lowered = text.lower()
    if "thought" in lowered or "signature" in lowered:
        fail(
            "E23 state",
            text[:160],
            "this endpoint drops provider state between turns; on Gemini set LLM_PROVIDER=google_genai",
        )
    elif "authentication" in lowered or "401" in lowered or "api key" in lowered:
        fail(
            "E10 auth",
            text[:160],
            "check LLM_API_KEY and LLM_BASE_URL against your provider's page",
        )
    elif "429" in lowered or "quota" in lowered or "rate limit" in lowered:
        fail(
            "E30 quota",
            text[:160],
            "you are out of quota; wait for the reset or change provider",
        )
    elif "context" in lowered or "too many tokens" in lowered or "maximum" in lowered:
        fail(
            "E40 limits",
            text[:160],
            "the per-request token cap is too small for this course",
        )
    else:
        fail("E99 unknown", text[:160], "paste this line into the course chat")
    return False


def check_langfuse() -> bool:
    """Optional until session 2, when the local server has to be up."""
    if not os.getenv("LANGFUSE_PUBLIC_KEY", "").strip():
        return record(True, "langfuse", "not configured yet, needed from session 2")
    try:
        from langfuse import get_client

        if not get_client().auth_check():
            fail(
                "E50 langfuse",
                "server reachable but the keys are rejected",
                "the keys in .env must match the ones the container was created with; "
                "recreate it with: docker compose down -v && docker compose up -d",
            )
            return False
    except Exception as error:  # noqa: BLE001
        fail(
            "E51 langfuse",
            f"{type(error).__name__}: {error}"[:160],
            "start it with: docker compose up -d (see README for the one-time curl)",
        )
        return False
    return record(True, "langfuse", os.getenv("LANGFUSE_HOST", ""))


def main() -> int:
    print("Course environment check\n")
    ordered = (
        check_python,
        check_packages,
        check_git,
        check_env_file,
        check_no_shadowing,
        check_config,
    )
    for step in ordered:
        if not step():
            print("\nStopped early: fix the line above and run again.")
            return 1

    print("\nCalling the model. One request, two tool calls.\n")
    ok = check_tool_loop()
    ok = check_langfuse() and ok

    print()
    print(
        "All checks passed."
        if ok
        else "Some checks failed. Fix them before the session."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
