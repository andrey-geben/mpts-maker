# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT
"""Файловый журнал приложения с ротацией и перехватом исключений."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path

LOG_NAME = "mpts-maker.log"
_LOG_PATH: Path | None = None
_ORIGINAL_EXCEPTHOOK = sys.excepthook
_ORIGINAL_THREADHOOK = getattr(threading, "excepthook", None)


def _default_log_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "MPTS-Maker" / "logs"
    else:
        base = Path.home() / ".local" / "share" / "mpts-maker" / "logs"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "MPTS-Maker" / "logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _excepthook(exc_type, exc_value, exc_tb):
    logging.getLogger("mpts").critical(
        "Необработанное исключение:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )
    try:
        _ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_tb)
    except Exception:
        pass


def _threadhook(args):
    logging.getLogger("mpts").critical(
        "Необработанное исключение в потоке %s:\n%s",
        getattr(args.thread, "name", "?"),
        "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)),
    )
    if _ORIGINAL_THREADHOOK:
        try:
            _ORIGINAL_THREADHOOK(args)
        except Exception:
            pass


def setup_logging(level: int = logging.INFO) -> Path:
    """Настраивает журнал и возвращает путь к файлу."""
    global _LOG_PATH
    if _LOG_PATH is not None:
        return _LOG_PATH

    _LOG_PATH = _default_log_dir() / LOG_NAME
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.handlers.RotatingFileHandler(
            _LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(handler)

    logging.captureWarnings(True)
    sys.excepthook = _excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _threadhook

    logging.getLogger("mpts").info("=" * 70)
    logging.getLogger("mpts").info(
        "Запуск MPTS Maker; Python %s; executable=%s; log=%s",
        sys.version.split()[0], sys.executable, _LOG_PATH,
    )
    return _LOG_PATH


def get_log_path() -> Path:
    return _LOG_PATH or (_default_log_dir() / LOG_NAME)
