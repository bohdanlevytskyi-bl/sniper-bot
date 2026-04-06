FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/
COPY config/example.yaml ./config/example.yaml
COPY migrations/ ./migrations/
COPY alembic.ini ./

RUN pip install --no-cache-dir -e .
RUN mkdir -p /app/config/data /app/config/logs

ENV SNIPER_CONFIG=/app/config/example.yaml

EXPOSE 8080

CMD ["sniper-bot", "run", "-c", "/app/config/example.yaml"]
