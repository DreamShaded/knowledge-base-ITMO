"""
structlog setup: app.jsonl under data/logs/.
Standard keys: event, job_type, latency_ms, task, provider, model, prompt_version.
Full LLM prompts only when log.log_llm_full=true.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from urllib.parse import unquote

import structlog

_configured = False
log_llm_full: bool = False


def _decode_uri_values(logger: object, method: str, event_dict: dict) -> dict:
    """Decode percent-encoded strings (e.g. Cyrillic in URNs) for human-readable logs."""
    for key, val in event_dict.items():
        if isinstance(val, str) and "%" in val:
            event_dict[key] = unquote(val)
    return event_dict


def configure_logging(log_dir: Path, level: str = "INFO", log_llm_full: bool = False) -> None:
    global _configured
    import app.logging as _self
    _self.log_llm_full = log_llm_full

    if _configured:
        return
    _configured = True

    log_dir.mkdir(parents=True, exist_ok=True)
    _level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _decode_uri_values,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(_level),
        cache_logger_on_first_use=True,
    )

    json_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(serializer=lambda v, **kw: json.dumps(v, ensure_ascii=False, **kw)),
        foreign_pre_chain=shared_processors,
    )

    # Configure stdlib root logger with JSON formatter on stdout
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=_level,
    )
    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(json_formatter)

    # File handler for all app.* loggers
    _add_file_handler("app", str(log_dir / "app.jsonl"), _level, json_formatter)
    # Dedicated handler for LLM call events (latency, model, prompt_version)
    _add_file_handler("app.llm", str(log_dir / "llm.jsonl"), _level, json_formatter)


def _add_file_handler(logger_name: str, path: str, level: int, formatter: logging.Formatter) -> None:
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = True


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)


def get_llm_logger() -> structlog.BoundLogger:
    """Logger for LLM call events: latency_ms, model, provider, prompt_version."""
    return structlog.get_logger("app.llm")
