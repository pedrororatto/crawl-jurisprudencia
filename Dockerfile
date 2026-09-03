FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Chromium do Playwright para o crawl4ai (usado só pelo serviço crawler)
RUN uv run playwright install --with-deps chromium

ENV PATH="/app/.venv/bin:$PATH"
