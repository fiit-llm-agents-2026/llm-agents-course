"""Small report used by session 7 and imported by its pytest example."""


def summarize_run(messages, elapsed_seconds):
    """Summarize new messages from one invocation; missing usage stays unknown."""
    replies = [message for message in messages if message.type == "ai"]
    if not replies:
        raise ValueError("No model replies to summarize")

    def complete_sum(values):
        # Частичная сумма скрыла бы стоимость вызовов без данных.
        return None if any(value is None for value in values) else sum(values)

    usage = [reply.usage_metadata or {} for reply in replies]
    charges = [
        reply.response_metadata.get("token_usage", {}).get("cost_rub")
        for reply in replies
    ]
    return {
        "model_calls": len(replies),
        "seconds": round(elapsed_seconds, 3),
        "input_tokens": complete_sum([item.get("input_tokens") for item in usage]),
        "output_tokens": complete_sum([item.get("output_tokens") for item in usage]),
        "cost_rub": complete_sum(charges),
    }
