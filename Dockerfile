FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# UV_PROJECT_ENVIRONMENT=/usr/local: install straight into the base
# image's existing Python installation instead of creating a .venv —
# confirmed (via a real python:3.14-slim container) that uv detects the
# interpreter already there and just populates its site-packages
# directly, no pyvenv.cfg/symlink machinery gets created. A venv exists
# to isolate one machine's multiple projects from each other; a
# container already provides that isolation, so it buys nothing here —
# just one less directory to copy and no PATH to prepend (and get wrong).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

# Dependencies first, in their own layer — only reinstalled when
# pyproject.toml/uv.lock change, not on every app code edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --no-dev --frozen


FROM python:3.14-slim AS runtime

RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

COPY --from=builder --chown=appuser:appuser /usr/local /usr/local
COPY --from=builder --chown=appuser:appuser /app/app ./app
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser migrations ./migrations

USER appuser

EXPOSE 8000

CMD ["fastapi", "run", "app/main.py", "--port", "8000"]


FROM builder AS builder-streaming

# streaming/consumer.py needs its own extras (confluent-kafka, pymongo)
# on top of everything builder already installed into /usr/local — this
# sync is genuinely incremental (same UV_PROJECT_ENVIRONMENT, same
# target, already populated by the parent stage), not a from-scratch
# reinstall. Extras installed before copying streaming/ itself, same
# "deps in their own layer" reasoning as builder's pyproject.toml/uv.lock
# ordering above.
RUN uv sync --no-dev --frozen --extra streaming --extra mongodb
COPY streaming ./streaming


FROM python:3.14-slim AS runtime-streaming

RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

COPY --from=builder-streaming --chown=appuser:appuser /usr/local /usr/local
COPY --from=builder-streaming --chown=appuser:appuser /app/app ./app
COPY --from=builder-streaming --chown=appuser:appuser /app/streaming ./streaming

USER appuser

CMD ["python", "-m", "streaming.consumer"]

FROM builder AS builder-dbt

RUN uv sync --no-dev --frozen --extra dbt
COPY dbt ./dbt
# dbt_packages/ is gitignored (vendored, not source) — a fresh checkout
# (any CI build included) never has it, so it must be installed here, not
# assumed present from a developer's local `dbt deps` run.
RUN cd dbt && uv run --extra dbt dbt deps

FROM python:3.14-slim AS runtime-dbt

RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app/dbt

COPY --from=builder-dbt --chown=appuser:appuser /usr/local /usr/local
COPY --from=builder-dbt --chown=appuser:appuser /app/dbt ./

USER appuser

CMD ["sh", "scripts/run_and_publish.sh"]
