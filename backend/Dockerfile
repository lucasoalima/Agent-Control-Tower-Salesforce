FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 act
USER act
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Apply migrations, then serve. Structured request logs come from the app itself.
CMD ["sh", "-c", "act migrate && uvicorn control_tower.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
