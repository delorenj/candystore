FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    APP_PORT=3001

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY static/ ./static/

RUN pip install --no-cache-dir .

EXPOSE 3001
CMD ["candystore", "serve", "--host", "0.0.0.0", "--port", "3001"]
