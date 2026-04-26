FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=remediation-mcp
WORKDIR /app
COPY pyproject.toml .
COPY shared/ shared/
RUN pip install --no-cache-dir -e .
COPY services/mcp-servers/remediation/ services/mcp-servers/remediation/
WORKDIR /app/services/mcp-servers/remediation
CMD ["python", "main.py"]
