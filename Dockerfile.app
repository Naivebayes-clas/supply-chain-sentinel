FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code (also mounted as volumes for dev)
COPY agent/ ./agent/
COPY api/ ./api/
COPY evals/ ./evals/

# Create data dirs
RUN mkdir -p /app/data/chroma /app/data/lake

EXPOSE 8000 8501

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]  
