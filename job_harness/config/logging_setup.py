"""Structured JSONL logging plus a readable console stream."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    COLORS = {"DEBUG": "\033[37m", "INFO": "\033[36m", "WARNING": "\033[33m",
              "ERROR": "\033[31m", "CRITICAL": "\033[41m"}
    RESET = "\033[0m"

    def __init__(self, color: bool = True) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)-28s %(message)s", "%H:%M:%S")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_") and k not in ("color",)
        }
        if extras:
            text += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        if self.color:
            return f"{self.COLORS.get(record.levelname, '')}{text}{self.RESET}"
        return text


def setup_logging(log_dir: str | Path, level: str = "INFO",
                  filename: str = "harness.jsonl") -> logging.Logger:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("harness")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.handlers.clear()
    root.propagate = False

    fh = logging.handlers.RotatingFileHandler(
        log_path / filename, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(ConsoleFormatter(color=sys.stderr.isatty() and not os.environ.get("NO_COLOR")))
    root.addHandler(ch)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"harness.{name}")
