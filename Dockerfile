# Proof of Review API and worker image. The reviewer web app is built and served by the API.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY api ./api
COPY services ./services
COPY agents ./agents
COPY mcp_server ./mcp_server
COPY connectors ./connectors
COPY db ./db
COPY prompts ./prompts
COPY scripts ./scripts
COPY alembic.ini ./
RUN pip install --no-cache-dir ".[all]"
COPY --from=web /web/dist ./web/dist
RUN useradd -m por && mkdir -p /app/var && chown -R por /app/var
USER por
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port 8000"]
