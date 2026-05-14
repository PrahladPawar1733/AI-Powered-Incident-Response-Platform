FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=db-mcp

WORKDIR /app

COPY pyproject.toml .
COPY shared/ shared/
RUN pip install --no-cache-dir -e .

COPY services/mcp-servers/db/ services/mcp-servers/db/

WORKDIR /app/services/mcp-servers/db

EXPOSE 8002

CMD ["python", "main.py"]
