FROM python:3.11-slim

# Only non-secret, non-environment-specific defaults here.
# All Firebase config and secrets are injected at deploy time
# via Cloud Run environment variables (--set-env-vars or Secret Manager).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libreoffice-writer libreoffice-impress fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose port
EXPOSE 8080

# Run with Gunicorn
CMD if [ "$LMS_MCP_PROBE_ENABLED" = "true" ] || [ "$LMS_MCP_ENABLED" = "true" ]; then \
      exec uvicorn asgi:app --host 0.0.0.0 --port "$PORT" --no-access-log; \
    else \
      exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 run:app; \
    fi
