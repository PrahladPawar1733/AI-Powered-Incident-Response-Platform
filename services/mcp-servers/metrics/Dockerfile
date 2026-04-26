FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=metrics-mcp

WORKDIR /app

COPY shared/pyproject.toml shared/
COPY shared/ shared/
RUN pip install --no-cache-dir -e ./shared

COPY services/mcp-servers/metrics/ services/mcp-servers/metrics/

WORKDIR /app/services/mcp-servers/metrics

EXPOSE 8004

CMD ["python", "main.py"]
