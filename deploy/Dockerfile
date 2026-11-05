# Production Multi-Stage Dockerfile for OpenReview AI GitHub App Server
FROM python:3.11-slim-bookworm AS base

# Prevent python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies: Git, Node.js, and CA certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install static linters
RUN pip install --no-cache-dir ruff \
    && npm install -g eslint

# Create non-root openreview user (UID 1000)
RUN groupadd -g 1000 openreview \
    && useradd -u 1000 -g openreview -m -s /bin/bash openreview

WORKDIR /app

# Install OpenReview AI application
COPY --chown=openreview:openreview . /app
RUN pip install --no-cache-dir .

# Create persistent state directory for SQLite database
RUN mkdir -p /data && chown -R openreview:openreview /data /app

# Switch to non-root unprivileged user
USER openreview

EXPOSE 8000

# Health check using standard endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; resp = urllib.request.urlopen('http://localhost:8000/health', timeout=3); exit(0 if resp.status == 200 else 1)"

ENV OPENREVIEW_JOBS_DB=/data/openreview_jobs.db

ENTRYPOINT ["openreview"]
CMD ["server", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/openreview_jobs.db"]

