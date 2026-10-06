# Build context: repository root (the backend reads config/ next to backend/).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements-dev.lock backend/requirements-dev.lock
RUN pip install -r backend/requirements-dev.lock

# Run from source (not installed): the app locates config/ relative to backend/app.
COPY backend/app backend/app

# Example network configs; UI-edited profiles live in config/local (a volume).
COPY config/*.yaml config/
RUN useradd --create-home --uid 10001 app \
    && mkdir -p config/local \
    && chown -R app:app config/local
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
