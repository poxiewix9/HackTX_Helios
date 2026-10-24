"""Structured logging for the agents service (same JSON shape as the blackboard)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from arco_agents.settings import get_settings

_configured = False


def configure_logging(service: str = "agents") -> None:
    global _configured  # noqa: PLW0603 - idempotent global init
    if _configured:
        return
    settings = get_settings()
    level = logging.getLevelName(settings.arco_log_level.upper())
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.arco_log_json
        else structlog.dev.ConsoleRenderer()
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
