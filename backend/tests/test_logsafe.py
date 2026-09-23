import io
import logging
import logging.config

from uvicorn.logging import AccessFormatter

from aethel.logsafe import RedactTokenFilter, build_log_config, redact

SECRET = "s3cr3t-T0k_en"


def test_redact_masks_token_values_only():
    assert redact(f"GET /ws/session?token={SECRET}") == "GET /ws/session?token=***"
    assert redact(f"/ws/session?a=1&token={SECRET}&b=2 HTTP/1.1") == "/ws/session?a=1&token=***&b=2 HTTP/1.1"
    assert redact("/api/health") == "/api/health"


def _record(logger, msg, args):
    return logging.LogRecord(logger, logging.INFO, __file__, 1, msg, args, None)


def test_filter_redacts_uvicorn_ws_record_in_args():
    # uvicorn.error logs the WS path as a %-arg: '%s - "WebSocket %s" [accepted]'
    record = _record("uvicorn.error", '%s - "WebSocket %s" [accepted]',
                     ("127.0.0.1:5000", f"/ws/session?token={SECRET}"))
    assert RedactTokenFilter().filter(record) is True
    assert SECRET not in record.getMessage() and "token=***" in record.getMessage()


def test_filter_keeps_access_record_shape_for_uvicorn_formatter():
    # AccessFormatter unpacks record.args as a 5-tuple, so redaction must keep it.
    record = _record("uvicorn.access", '%s - "%s %s HTTP/%s" %d',
                     ("127.0.0.1:5000", "GET", f"/api/x?token={SECRET}", "1.1", 200))
    RedactTokenFilter().filter(record)
    line = AccessFormatter(fmt="%(client_addr)s - \"%(request_line)s\" %(status_code)s", use_colors=False).format(record)
    assert SECRET not in line and "token=***" in line and "200" in line


UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _snapshot():
    return {n: (list(logging.getLogger(n).handlers), list(logging.getLogger(n).filters),
                logging.getLogger(n).level, logging.getLogger(n).propagate) for n in UVICORN_LOGGERS}


def _restore(saved):
    for name, (handlers, filters, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:], logger.filters[:] = handlers, filters
        logger.setLevel(level)
        logger.propagate = propagate


def test_log_config_wires_filter_into_uvicorn_loggers_and_handlers():
    config = build_log_config()
    for handler in ("default", "access"):
        assert "redact_token" in config["handlers"][handler]["filters"]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert "redact_token" in config["loggers"][name]["filters"]
    # And it really works end to end once applied.
    saved = _snapshot()
    stream = io.StringIO()
    try:
        logging.config.dictConfig(config)
        logging.getLogger("uvicorn").handlers[0].setStream(stream)
        logging.getLogger("uvicorn.error").info('%s - "WebSocket %s" [accepted]', "c", f"/ws/session?token={SECRET}")
    finally:
        _restore(saved)
    assert "token=***" in stream.getvalue() and SECRET not in stream.getvalue()
