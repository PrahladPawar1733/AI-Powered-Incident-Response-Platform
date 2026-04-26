FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=k8s-mcp

WORKDIR /app

COPY shared/pyproject.toml shared/
COPY shared/ shared/
RUN pip install --no-cache-dir -e ./shared

COPY services/mcp-servers/k8s/ services/mcp-servers/k8s/

WORKDIR /app/services/mcp-servers/k8s

EXPOSE 8001

CMD ["python", "main.py"]
