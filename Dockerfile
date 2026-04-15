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

CMD ["python", "main.py"]
