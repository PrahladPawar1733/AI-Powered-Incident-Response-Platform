FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=diagnosis-agent

WORKDIR /app

COPY pyproject.toml .
COPY shared/ shared/
RUN pip install --no-cache-dir -e .

COPY services/diagnosis-agent/ services/diagnosis-agent/
COPY services/mcp-servers/ services/mcp-servers/

WORKDIR /app/services/diagnosis-agent

CMD ["python", "main.py"]
