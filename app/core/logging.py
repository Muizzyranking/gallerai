import logging
import logging.handlers
import sys
from pathlib import Path


class DevFormatter(logging.Formatter):
    """
    Colored Formatter for development environment.
    Format: timestamp | LELVEL | logger name | message
    """

    LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET_COLOR = "\033[0m"
    BOLD = "\033[1m"

    def format(self, record: logging.LogRecord) -> str:
        """ """
        color = self.LEVEL_COLORS.get(record.levelno, self.RESET_COLOR)
        level = f"{color}{self.BOLD}{record.levelname:<8}{self.RESET_COLOR}"
        module = f"\033[90m{record.name}\033[0m"  # grey
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        message = record.getMessage()

        formatted_message = f"{timestamp} | {level} | {module} | {message}"

        if record.exc_info:
            formatted_message += "\n" + self.formatException(record.exc_info)
        return formatted_message


class ProdFormatter(logging.Formatter):
    """
    JSON formatter for production.
    Outputs one JSON object per line — suitable for log aggregators.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a JSON object."""
        import json
        from datetime import datetime, timezone

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Include any extra fields passed via extra={}
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload)


def setup_logging(env: str = "development", log_dir: str = "logs") -> None:
    """
    Args:
        env: "development" or "production". Controls level and format.
        log_dir: Directory for log files (production only).
    """
    is_dev = env == "development"
    level = logging.DEBUG if is_dev else logging.INFO
    formatter = DevFormatter() if is_dev else ProdFormatter()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if not is_dev:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / "galleria.log",
            maxBytes=10 * 1024 * 1024,  # rotate at 10MB
            backupCount=5,  # keep 5 rotated files
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("deepface").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured — env={env} level={logging.getLevelName(level)}")
