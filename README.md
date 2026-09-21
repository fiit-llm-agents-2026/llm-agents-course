# Разработка LLM-агентов

Материалы спецкурса: ноутбуки занятий и скрипт проверки окружения. Ноутбуки на английском, занятия ведутся на русском.

Репозиторий только для чтения. Свой репозиторий с агентом вы заводите сами на первом занятии и наращиваете его до защиты; здесь лежит то, что разбирается на экране.

## Что делать до первого занятия

Установить Python 3.11 или новее, git и Docker Desktop. Docker понадобится со второго занятия.

Скопировать `.env.example` в `.env` рядом с `check_env.py`. Студентам курса выдается ключ Polza (https://polza.ai): канал в файле уже заполнен, остается вставить ключ в `LLM_API_KEY`. Вольные слушатели берут свой ключ и меняют три строки: для Google AI Studio (https://aistudio.google.com/apikey, бесплатно, нужен только аккаунт Google) это `LLM_PROVIDER=google_genai`, `MODEL_CHEAP=gemini-3.5-flash-lite`, `MODEL_STRONG=gemini-3.6-flash`, а в `LLM_BASE_URL` для первого занятия — адрес из комментария в файле; для своего счета в Polza менять ничего не нужно. Суффикс `@reasoning_effort=none` в имени модели не убирать: без него каждый вызов оплачивает скрытые рассуждения и стоит примерно в пять раз дороже. Блок Langfuse остается пустым до второго занятия.

Поставить зависимости и запустить проверку:

```bash
uv venv && uv pip install "langchain>=1.3,<2" "langgraph>=1.2,<2" "langchain-openai>=1.4,<2" \
  "langchain-google-genai>=4.3,<5" "langfuse>=4.14,<5" "openai>=2.3,<3" "python-dotenv>=1.2,<2" \
  "langgraph-checkpoint-sqlite>=3.1,<4"
uv run python check_env.py
```

Позже понадобятся дополнительные пакеты, ставить их заранее не нужно: "trustcall>=0.0.39" к занятию 5; "fastapi>=0.141.1", "aiogram>=3", "langgraph-checkpoint-postgres>=3.1.2" и "psycopg[binary,pool]>=3.2,<4" к занятию 8; "agentevals>=0.0.9" и "openevals>=0.2.0" к занятию 11; "deepagents>=0.6,<0.7" к занятию 13 (требует Python 3.11); "langchain-mcp-adapters>=0.3" и "mcp>=1.29,<2" к занятию 14.

Все строки должны быть PASS. Последняя проверка — главная: она делает два зависимых вызова инструмента в одном диалоге. Эндпоинт, отвечающий на одно сообщение, но не выдерживающий цепочку, для курса не годится и сломается уже на первом занятии.

Вывод скрипта сохраните: на первом занятии он станет первым артефактом в вашем репозитории.

## Занятия

Аудиторных занятий 14: ноутбуки занятий 12 и 14 разбираются самостоятельно, их артефакты засчитываются так же.

| Ноутбук | Тема |
|---|---|
| `notebooks/session-01-agent-as-a-loop.ipynb` | агент как цикл: вызов модели, инструмент, разбор ответа руками, затем нативный вызов |
| `notebooks/session-02-langgraph-basics.ipynb` | LangGraph: состояние, узлы, ребра, ToolNode, локальная наблюдаемость |
| `notebooks/session-03-create-agent-and-tools.ipynb` | слой LangChain 1.x: create_agent, middleware, проектирование инструментов |
| `notebooks/session-04-short-term-memory.ipynb` | контекст и краткосрочная память, чекпоинтер |
| `notebooks/session-05-long-term-memory.ipynb` | долгосрочная память, Store, trustcall |
| `notebooks/session-06-streaming-and-hitl.ipynb` | стриминг и участие человека, interrupt() |
| `notebooks/session-07-observability.ipynb` | наблюдаемость, Langfuse |
| `notebooks/session-08-deploy.ipynb` | деплой: FastAPI, compose, Telegram |
| `notebooks/session-09-multi-agent.ipynb` | мультиагентность, Send, исследовательский агент |
| `notebooks/session-10-autoresearch.ipynb` | autoresearch, замороженная метрика |
| `notebooks/session-11-evaluation.ipynb` | три уровня оценки качества |
| `notebooks/session-12-harness-engineering.ipynb` | харнесс-инжиниринг (самостоятельное чтение) |
| `notebooks/session-13-deep-agents.ipynb` | deepagents, совет моделей |
| `notebooks/session-14-mcp.ipynb` | MCP (самостоятельное чтение) |
| `notebooks/session-15-security.ipynb` | безопасность, опасная триада |

Эталонный ассистент курса, по которому догоняют пропустившие занятие: https://github.com/fiit-llm-agents-2026/agents-course-assistant, тег на конец каждого занятия.

Ноутбуки прогнаны на версиях, закрепленных выше: langchain 1.3.14, langgraph 1.2.9, langchain-core 1.5.3, langfuse SDK 4.14.1, deepagents 0.6.12, mcp 1.29.0, Python 3.12.
