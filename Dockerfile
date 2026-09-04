FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Navegadores fora de /root: o install roda como root, e /root é 700, então o
# usuário sem privilégio não alcançaria o cache padrão. Sem isto, o Chromium
# nem chega a subir — e é o sandbox dele que nos protege do HTML hostil que um
# crawler consome o dia inteiro.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Chromium do Playwright para o crawl4ai (usado só pelo serviço crawler)
RUN uv run playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

# Usuário sem privilégio. O Chromium recusa rodar como root sem --no-sandbox,
# e o sandbox é justamente a defesa contra HTML hostil de terceiros — que é o
# que um crawler consome o dia inteiro.
RUN useradd --create-home --uid 10001 juris \
    && chown -R juris:juris /app /home/juris
USER juris

ENV PATH="/app/.venv/bin:$PATH"
