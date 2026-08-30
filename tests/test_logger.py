import logging
import os
import tempfile
from app.logger import SensitiveDataFilter, setup_logger


def test_sensitive_data_filter_redactions():
    filt = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=12,
        msg="Admin_Password: 'MySecretPassword123' token=5b834ae0-822a-439f-b087-9ac42a16ac63 Basic dXNlcjpwYXNz",
        args=(),
        exc_info=None,
    )
    filt.filter(record)
    assert "MySecretPassword123" not in record.msg
    assert "Admin_Password=[REDACTED]" in record.msg
    assert "dXNlcjpwYXNz" not in record.msg


def test_setup_logger_with_rotation():
    tmpdir = tempfile.mkdtemp()
    try:
        logger = setup_logger("test_suite_logger_custom", log_dir=tmpdir, max_bytes=1024, backup_count=2)
        assert len(logger.handlers) >= 2

        logger.info("Test log entry message")
        log_file = os.path.join(tmpdir, "test_suite_logger_custom.log")
        assert os.path.exists(log_file)
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Test log entry message" in content

        # Close all file handlers so Windows allows rmtree
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
