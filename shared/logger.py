# shared/logger.py
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(service_name: str, log_level: str = "INFO") -> None:
    """
    Call once at service startup. After this every log
    line is JSON with consistent fields across all services.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer()
            if _is_development()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )


def get_logger(service: str, **ctx: Any) -> structlog.BoundLogger:
    """
    Usage in any service:
        log = get_logger("triage-agent", version="1.0")
        log.info("alert_received", alert_id="abc", service="payment")

    Dev output (readable):
        [triage-agent] alert_received  alert_id=abc service=payment

    Prod output (searchable JSON):
        {"level":"info","service":"triage-agent","alert_id":"abc",...}
    """
    return structlog.get_logger(service).bind(service=service, **ctx)


def _is_development() -> bool:
    import os
    return os.getenv("ENVIRONMENT", "development") == "development"