FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system evoagent && adduser --system --ingroup evoagent evoagent

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

RUN python -m pip install --no-cache-dir .

RUN mkdir -p /app/workspace && chown -R evoagent:evoagent /app/workspace
USER evoagent

CMD ["evoagent-api"]
