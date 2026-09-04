FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Chromium do Playwright para o crawl4ai (usado só pelo serviço crawler)
RUN uv run playwright install --with-deps chromium

# Usuário sem privilégio. O Chromium recusa rodar como root sem --no-sandbox,
# e o sandbox é justamente a defesa contra HTML hostil de terceiros — que é o
# que um crawler consome o dia inteiro.
RUN useradd --create-home --uid 10001 juris \
    && chown -R juris:juris /app /home/juris
USER juris

ENV PATH="/app/.venv/bin:$PATH"
