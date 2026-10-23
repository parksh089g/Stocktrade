import io
import logging

from trading.core.log import MASK, RedactingFormatter


def _logger_with_buffer(secrets) -> tuple[logging.Logger, io.StringIO]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(RedactingFormatter("%(message)s", secrets))
    logger = logging.getLogger(f"test-{id(buf)}")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, buf


def test_secret_in_message_is_masked():
    logger, buf = _logger_with_buffer(["my-app-key-1234"])
    logger.info("token request appkey=%s", "my-app-key-1234")
    out = buf.getvalue()
    assert "my-app-key-1234" not in out
    assert MASK in out


def test_secret_in_traceback_is_masked():
    logger, buf = _logger_with_buffer(["bot123:secret"])
    try:
        raise RuntimeError("failed url https://api.telegram.org/botbot123:secret/sendMessage")
    except RuntimeError:
        logger.exception("send failed")
    assert "bot123:secret" not in buf.getvalue()


def test_short_values_are_not_masked():
    logger, buf = _logger_with_buffer(["01"])
    logger.info("account suffix 01")
    assert buf.getvalue().strip() == "account suffix 01"
