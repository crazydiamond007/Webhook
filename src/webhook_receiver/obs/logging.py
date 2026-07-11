"""Structured JSON logging (SPEC §6.7, NFR-5).

Day 0 establishes the pipeline. Correlation-id middleware and per-event binding
arrive with the ingestion and processing slices.

Two rules this configuration is built to keep:
  * Logs are JSON in every environment except a developer's terminal, so they
    are queryable rather than grep-able.
  * Payloads and secrets never enter a log record. Nothing here serialises a
    request body, and secrets are `SecretStr` at the source (see config.py).
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import Processor

from webhook_receiver.config import Environment, LogLevel


def configure_logging(*, level: LogLevel, environment: Environment) -> None:
    """Idempotent: safe to call from both the API and the worker entrypoints."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # A human reads the terminal; a machine reads everything else.
    renderer: Processor = (
        structlog.dev.ConsoleRenderer()
        if environment is Environment.LOCAL
        else structlog.processors.JSONRenderer()
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelNamesMapping()[level.value],
        force=True,
    )

    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.value]
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
