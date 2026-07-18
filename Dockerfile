FROM python@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3

WORKDIR /app

COPY pyproject.toml .
COPY candystore/ ./candystore/
RUN pip install --no-cache-dir -e "."

COPY migrations/ ./migrations/
COPY static/ ./static/

ENV PYTHONUNBUFFERED=1
ENV APP_PORT=3001

EXPOSE 3001
CMD ["python", "-m", "candystore.main"]
