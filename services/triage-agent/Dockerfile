FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=triage-agent

WORKDIR /app

# Install the shared package first to take advantage of Docker cache
COPY shared/pyproject.toml shared/
COPY shared/ shared/
RUN pip install --no-cache-dir -e ./shared

# Copy the service code
COPY services/triage-agent/ services/triage-agent/

WORKDIR /app/services/triage-agent

# Run the python worker
CMD ["python", "main.py"]
