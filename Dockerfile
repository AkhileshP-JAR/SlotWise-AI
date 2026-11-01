# syntax=docker/dockerfile:1.4
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# Install minimal OS dependencies for healthchecks & compilation if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root application user
RUN groupadd -g 10001 slotwise && \
    useradd -u 10001 -g slotwise -m -s /bin/bash slotwise

WORKDIR /app

# Install Python requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and configurations
COPY configs ./configs
COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts
COPY docs ./docs
COPY alembic.ini pyproject.toml ./

# Ensure data and artifacts directories exist with non-root ownership
RUN mkdir -p /app/data /app/artifacts/reports /app/artifacts/benchmarks /app/artifacts/models /app/artifacts/mlruns && \
    chown -R slotwise:slotwise /app

USER slotwise

EXPOSE 8000 8501

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Default command: start FastAPI backend
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
