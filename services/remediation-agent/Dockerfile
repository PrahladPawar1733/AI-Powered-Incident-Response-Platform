FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=remediation-agent
WORKDIR /app
COPY pyproject.toml .
COPY shared/ shared/
RUN pip install --no-cache-dir -e .
COPY services/remediation-agent/ services/remediation-agent/
COPY services/mcp-servers/remediation/ services/mcp-servers/remediation/
WORKDIR /app/services/remediation-agent
CMD ["python", "main.py"]
