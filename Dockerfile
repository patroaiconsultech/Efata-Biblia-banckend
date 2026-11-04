FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system bible && adduser --system --ingroup bible bible

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY data ./data

RUN pip install --upgrade pip && pip install .
RUN chown -R bible:bible /app

USER bible

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.getenv('PORT','8080'), timeout=3)"

CMD ["sh", "-c", "exec uvicorn bible_platform.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
