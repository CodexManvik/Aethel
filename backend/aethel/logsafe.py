"""Keep the auth token out of log files.

The WebSocket authenticates with `?token=...`, and uvicorn logs request paths
(uvicorn.error for WebSocket handshakes, uvicorn.access for HTTP) including
the query string. This filter rewrites `token=<value>` to `token=***` in the
message and in every string %-arg, keeping record.args' shape intact
(uvicorn's AccessFormatter unpacks it as a tuple).
"""
import copy
import logging
import re

from uvicorn.config import LOGGING_CONFIG

_TOKEN_RE = re.compile(r"(token=)[^&\s\"']+")
FILTER_NAME = "redact_token"


def redact(text: str) -> str:
    return _TOKEN_RE.sub(r"\1***", text)


def _redact_arg(value):
    return redact(value) if isinstance(value, str) else value


class RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        return True


def build_log_config() -> dict:
    """uvicorn's default logging config plus the redaction filter on its
    handlers (catches anything they print) and on the uvicorn loggers."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})[FILTER_NAME] = {"()": "aethel.logsafe.RedactTokenFilter"}
    for handler in config["handlers"].values():
        handler.setdefault("filters", []).append(FILTER_NAME)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        config["loggers"].setdefault(name, {}).setdefault("filters", []).append(FILTER_NAME)
    return config
