FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=alert-ingestor

WORKDIR /app

# Install the shared package first to take advantage of Docker cache
COPY shared/pyproject.toml shared/
COPY shared/ shared/
RUN pip install --no-cache-dir -e ./shared

# Copy the service code
COPY services/alert-ingestor/ services/alert-ingestor/

WORKDIR /app/services/alert-ingestor

EXPOSE 8000

# Run FastAPI app with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
