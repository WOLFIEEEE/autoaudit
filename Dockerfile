# Dockerfile for the FastAPI server + Celery worker (no NVDA — Linux).
# NVDA auditing requires a Windows host; run that worker natively.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-fetch axe-core so containers can run offline.
RUN python scripts/fetch_axe.py || echo "axe fetch failed; will use CDN at runtime"

ENV SKIP_NVDA=true \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Container-level healthcheck. Orchestrators (Docker Swarm, K8s via the
# healthcheck bridge) can gate traffic on this. The worker container
# overrides CMD so the healthcheck is only meaningful for the server
# container — in practice the worker should set its own healthcheck or
# have orchestration watch its process state.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; \
r = urllib.request.urlopen('http://localhost:8000/health', timeout=3); \
sys.exit(0 if r.status == 200 else 1)" || exit 1

CMD ["python", "main.py"]
