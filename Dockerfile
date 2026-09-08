FROM node:24-alpine AS frontend-build

WORKDIR /frontend
RUN npm install --global pnpm@11.19.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system evoagent && adduser --system --ingroup evoagent evoagent

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY evals ./evals
COPY --from=frontend-build /frontend/dist ./frontend/dist

RUN python -m pip install --no-cache-dir .

RUN mkdir -p /app/workspace && chown -R evoagent:evoagent /app/workspace
USER evoagent

CMD ["evoagent-api"]
