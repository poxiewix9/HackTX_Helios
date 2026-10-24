"""Structured JSON logging shared by the vision services (same shape as blackboard/agents)."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

_configured = False


def configure_logging(service: str) -> None:
    global _configured  # noqa: PLW0603 - idempotent global init
    if _configured:
        return
    level = logging.getLevelName(os.environ.get("ARCO_LOG_LEVEL", "INFO").upper())
    json_logs = os.environ.get("ARCO_LOG_JSON", "true").lower() in {"1", "true", "yes"}
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)
    _configured = True


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
