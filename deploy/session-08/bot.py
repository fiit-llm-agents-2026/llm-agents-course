"""Telegram is an HTTP client; the API owns the agent and all saved state."""
import asyncio
import json
import logging
import os
import mistune

from aiogram import Bot, Dispatcher, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv
from client import ask

load_dotenv()
dp = Dispatcher()
log = logging.getLogger(__name__)
markdown = mistune.create_markdown(renderer="ast")


def plain_text(source):
    """Drop Markdown presentation, preserving code, links and actual text."""
    def render(nodes):
        parts = []
        for node in nodes:
            kind = node["type"]
            children = render(node.get("children", []))
            if kind == "list":
                ordered = node.get("attrs", {}).get("ordered", False)
                start = node.get("attrs", {}).get("start", 1)
                items = [render(item.get("children", [])).strip() for item in node["children"]]
                parts.append("\n".join(f"{start + i}. {text}" if ordered else f"• {text}"
                                       for i, text in enumerate(items)) + "\n\n")
            elif kind in {"link", "image"}:
                url = node.get("attrs", {}).get("url", "")
                parts.append(children if children == url else f"{children} ({url})")
            elif kind in {"paragraph", "heading", "block_quote"}:
                parts.append(children + "\n\n")
            elif kind == "blank_line":
                continue
            elif kind in {"softbreak", "linebreak", "thematic_break"}:
                parts.append("\n")
            elif kind == "block_code":
                parts.append(node.get("raw", "") + "\n")
            else:
                parts.append(node.get("raw", children))
        return "".join(parts)
    return render(markdown(source)).strip()


def action_card(actions):
    """Show every argument once, without internal middleware descriptions."""
    blocks = []
    for index, action in enumerate(actions, 1):
        name = action["name"]
        title = "Заявка на доставку" if name == "request_delivery" else name
        lines = [f"Действие {index}: {title}"]
        for key, value in action["args"].items():
            label = "Примечание" if key == "note" else key
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            lines.append(f"{label}: {text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def show(message, result):
    # Parse formatting before splitting, so long messages cannot break markup.
    text = plain_text(result["text"])
    for start in range(0, len(text), 3500):
        await message.answer(text[start:start + 3500])
    for pause in result["interrupts"]:
        value = pause["value"]
        if not isinstance(value, dict) or "action_requests" not in value:
            await message.answer("Нужен отдельный интерфейс для этого вида подтверждения.")
            continue
        description = action_card(value["action_requests"])
        await message.answer("Проверьте действия и все аргументы:")
        for start in range(0, len(description), 3500):
            await message.answer(description[start:start + 3500])
        reviews = value["review_configs"]
        buttons = [InlineKeyboardButton(text=title, callback_data=f"{kind}:{pause['id']}")
                   for kind, title in [("approve", "Одобрить все"), ("reject", "Отклонить все")]
                   if all(kind in r["allowed_decisions"] for r in reviews)]
        if buttons:
            await message.answer("Решение относится к показанному набору действий.",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]))
        else:
            await message.answer("Для этих действий используйте /resume API; пример есть в инструкции.")


@dp.message()
async def on_message(message: Message):
    if message.chat.type != "private":
        await message.answer("Для отдельной истории и памяти напишите боту в личный чат.")
        return
    if not message.text:
        await message.answer("Пришлите текстовый вопрос.")
        return
    try:
        result = await ask("chat", str(message.chat.id),
                           {"text": message.text, "user_id": str(message.from_user.id)})
        await show(message, result)
    except Exception:
        log.exception("Telegram request failed")
        await message.answer("Не удалось получить ответ. Проверьте логи api и bot; если есть пауза, используйте ее кнопки.")


@dp.callback_query(F.data.startswith("approve:") | F.data.startswith("reject:"))
async def decide(callback: CallbackQuery):
    await callback.answer()
    if callback.message is None or callback.message.chat.type != "private":
        return
    kind, pause_id = callback.data.split(":", 1)
    try:
        # State lives in Postgres, so buttons still work after either process restarts.
        import httpx
        async with httpx.AsyncClient(timeout=20) as connection:
            response = await connection.get(
                os.getenv("API_URL", "http://localhost:8108") + f"/state/{callback.message.chat.id}",
                params={"user_id": str(callback.from_user.id)},
                headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
            )
            response.raise_for_status()
            pause = next(p for p in response.json()["interrupts"] if p["id"] == pause_id)
        decisions = [{"type": kind} for _ in pause["value"]["action_requests"]]
        result = await ask("resume", str(callback.message.chat.id), {
            "user_id": str(callback.from_user.id), "interrupt_id": pause_id, "decisions": decisions,
        })
        await callback.message.edit_reply_markup(reply_markup=None)
        await show(callback.message, result)
    except Exception:
        log.exception("Approval failed")
        await callback.message.answer("Решение не применено. Возможно, эта кнопка уже использована; проверьте состояние и логи.")


async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env before starting the bot.")
    async with Bot(token=token) as bot:
        await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
