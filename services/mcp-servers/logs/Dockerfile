FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=logs-mcp

WORKDIR /app

COPY pyproject.toml .
COPY shared/ shared/
RUN pip install --no-cache-dir -e .

COPY services/mcp-servers/logs/ services/mcp-servers/logs/

WORKDIR /app/services/mcp-servers/logs

EXPOSE 8003

CMD ["python", "main.py"]
