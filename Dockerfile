# ── Backend Dockerfile (for Railway) ──
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all backend code + authorities data
COPY . .

# Railway sets PORT env var; default to 8000 for local
ENV PORT=8000
EXPOSE ${PORT}

# Start uvicorn — use shell form so $PORT is expanded at runtime
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
