# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import os
import sys
from pathlib import Path


def _setup_portable_vlc() -> Path | None:
    """Настраивает переменные окружения для portable VLC."""
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).parent

    vlc_dir = base_dir / "vlc_lib"
    libvlc = vlc_dir / "libvlc.dll"
    plugins = vlc_dir / "plugins"

    if not vlc_dir.is_dir() or not libvlc.exists() or not plugins.is_dir():
        return None

    os.environ["PYTHON_VLC_LIB_PATH"] = str(libvlc)
    os.environ["PYTHON_VLC_MODULE_PATH"] = str(plugins)
    os.environ["PATH"] = str(vlc_dir) + os.pathsep + os.environ.get("PATH", "")
    return vlc_dir


_vlc_dir = _setup_portable_vlc()

VLC_AVAILABLE: bool = False
_vlc_instance = None

if _vlc_dir is not None:
    try:
        import vlc as _vlc  # noqa: F401
        _vlc.libvlc_get_version()
        VLC_AVAILABLE = True
    except Exception:
        VLC_AVAILABLE = False


def get_vlc_instance():
    """Возвращает singleton-инстанс VLC с оптимизациями для мозаики."""
    global _vlc_instance
    if not VLC_AVAILABLE:
        return None
    if _vlc_instance is None:
        import vlc
        _vlc_instance = vlc.Instance(
            # Аппаратное декодирование D3D11 (Intel/AMD/NVIDIA)
            "--avcodec-hw=d3d11va",
            # АГРЕССИВНАЯ разгрузка декодера: пропуск loop filter (×2 быстрее,
            # качество чуть хуже — на 480×270 в плитке не видно)
            "--avcodec-skiploopfilter=all",
            # Быстрый режим libavcodec
            "--avcodec-fast",
            # Явно указать видео-выход и отключить всё лишнее
            "--no-osd",
            "--no-video-title-show",
            "--no-snapshot-preview",
            "--no-sub-autodetect-file",
            # Сетевые буферы: меньше — быстрее старт и меньше памяти
            "--network-caching=200",
            "--live-caching=200",
            # Отключить лишние логи
            "--quiet",
        )
    return _vlc_instance


def release_vlc_instance():
    """Корректно освобождает singleton-инстанс при выходе."""
    global _vlc_instance
    if _vlc_instance is not None:
        try:
            _vlc_instance.release()
        except Exception:
            pass
        _vlc_instance = None