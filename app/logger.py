"""Logging configuration for ChaseBase — rotating file + stderr"""
import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: str | Path = "logs") -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("chasebase")
    logger.setLevel(logging.DEBUG)

    # Avoid adding duplicate handlers on repeated calls
    if logger.handlers:
        return

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    # Rotating file handler — 5 MB each, keep last 7
    file_handler = logging.handlers.RotatingFileHandler(
        log_path / "chasebase.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    # Stderr handler (visible in uvicorn console)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
