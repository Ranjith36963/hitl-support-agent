# Slim Python image — match the version CI runs against so locally-passing
# checks translate to in-container behaviour.
FROM python:3.11-slim

# Don't write .pyc, flush stdout immediately for friendlier docker logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so source-only edits don't bust the layer cache.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Now the source. .dockerignore (sibling file) keeps the image small.
COPY . .

# /metrics is an HTTP endpoint; serve via the FastAPI app's uvicorn entry.
# HOST=0.0.0.0 is set in docker-compose.yml — the runtime default in
# src/config.py is 127.0.0.1 for safer dev (see docs/threat_model.md A5).
EXPOSE 8000

CMD ["python", "-m", "src.server"]
