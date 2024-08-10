import logging
import logging.handlers
from pathlib import Path
import sys


LEVEL = logging.DEBUG


def get_logger() -> logging.Logger:
    return logging.getLogger()


def configure_host_logger() -> None:
    host_log_directory = Path.cwd() / "log" / "host"
    host_log_directory.mkdir(exist_ok=True, parents=True)

    logger = logging.getLogger()
    logger.handlers.clear()
    logger.setLevel(LEVEL)

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=host_log_directory / "host.log",
        when="h",
        interval=2,
        backupCount=72,
    )
    handler.setLevel(LEVEL)
    handler.setFormatter(
        logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(message)s")
    )

    logger.addHandler(handler)

    handler2 = logging.StreamHandler(stream=sys.stderr)
    handler2.setLevel(logging.WARNING)
    handler2.setFormatter(
        logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler2)


def configure_vm_logger(name: str) -> logging.Logger:
    host_log_directory = Path.cwd() / "log" / name
    host_log_directory.mkdir(exist_ok=True, parents=True)

    logger = logging.Logger(name)
    logger.handlers.clear()
    logger.setLevel(LEVEL)

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=host_log_directory / "vm.log",
        when="h",
        interval=2,
        backupCount=72,
    )
    handler.setLevel(LEVEL)
    handler.setFormatter(
        logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(message)s")
    )

    logger.addHandler(handler)

    return logger
