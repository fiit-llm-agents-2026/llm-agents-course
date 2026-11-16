FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.3 /uv /bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY agent.py api.py bot.py client.py check.py ./
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
