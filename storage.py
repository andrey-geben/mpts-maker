# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import json
import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths

CONFIG_VERSION = 1


def _config_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    if not base:
        base = os.path.expanduser("~")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return _config_dir() / "config.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def save_config(data: dict):
    """Атомарная запись: сначала .tmp, потом os.replace."""
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        # Сохранение не должно мешать работе приложения
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass