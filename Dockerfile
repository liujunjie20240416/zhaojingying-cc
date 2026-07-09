FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app

WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/liujunjie20240416/zhaojingying-cc"
LABEL org.opencontainers.image.description="Zhaojingying AI character chat platform"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
COPY --from=frontend-builder /app/static/frontend ./static/frontend

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && uvicorn main:app --host 0.0.0.0 --port 8000"]
