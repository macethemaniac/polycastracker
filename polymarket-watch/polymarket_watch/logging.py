from __future__ import annotations

import json
import logging as py_logging
import sys
from typing import Any

from .config import Settings, settings


class JsonFormatter(py_logging.Formatter):
    def format(self, record: py_logging.LogRecord) -> str:
        log_record: dict[str, Any] = {
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, self.datefmt),
        }
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


def setup_logging(config: Settings | None = None) -> None:
    cfg = config or settings
    handler = py_logging.StreamHandler(sys.stdout)
    if cfg.log_format.lower() == "json":
        formatter = JsonFormatter()
    else:
        formatter = py_logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)

    root_logger = py_logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(cfg.log_level.upper())
    root_logger.addHandler(handler)
