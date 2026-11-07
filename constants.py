# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

from datetime import date

APP_NAME        = "MPTS Maker"
APP_VERSION     = "1.0.0"
APP_AUTHOR      = "Andrey Geben"
APP_YEAR        = str(date.today().year)
APP_COPYRIGHT   = f"© {APP_YEAR} {APP_AUTHOR}"
APP_GITHUB      = "https://github.com/andrey-geben/VHS-Sat-Lite"
APP_LICENSE     = "MIT"
APP_DESCRIPTION = (
    "Преобразует M3U-плейлист в один или несколько MPTS-потоков "
    "(Multi Program Transport Stream) и отправляет их по UDP multicast."
)
APP_CREDITS = (
    "Работает на FFmpeg (https://ffmpeg.org) и PySide6 (https://www.qt.io). "
    "FFmpeg используется как внешний исполняемый файл и распространяется под лицензией GPL."
)