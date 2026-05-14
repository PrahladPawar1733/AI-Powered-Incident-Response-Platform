# Contributing to Incident Response Platform

Thank you for your interest in contributing! 🎉

## Getting Started

1. **Fork** the repo and clone it locally
2. Follow the [Quick Start](README.md#-quick-start) to get the platform running
3. Create a branch: `git checkout -b feat/your-feature-name`

## Development Guidelines

### Code Style
- Python: follow PEP 8, use `structlog` for logging (not `print`)
- JavaScript/React: use functional components + hooks
- Always use `shared/config.py` `Settings` for configuration — no hardcoded values

### Secrets
- **Never** commit `.env` or any file containing real API keys
- The `.gitignore` blocks `.env` — please verify before pushing
- Use `.env.example` to document new env vars

### Adding a New Agent
1. Create `services/<agent-name>/main.py` with a Kafka consumer loop
2. Register new Kafka topics in `shared/config.py`
3. Add the service to `infra/docker-compose.override.yml` if needed

### Adding a New MCP Tool
1. Add the tool function to `services/mcp-servers/<server>/main.py`
2. Register it via `@mcp.tool()` decorator
3. Document expected inputs/outputs in the docstring

## Pull Request Checklist

- [ ] No secrets or API keys in the code
- [ ] `.env` is not committed
- [ ] New env vars are added to `.env.example`
- [ ] Python code runs without errors
- [ ] Frontend builds with `npm run build`

## Reporting Issues

Please open a GitHub Issue with:
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (redact any sensitive data)
