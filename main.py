# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import logging
import sys

from logging_setup import get_log_path, setup_logging

# ВАЖНО: до PySide6 — чтобы portable VLC успел настроиться
setup_logging()
import vlc_setup  # noqa: F401

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from constants import APP_NAME, APP_AUTHOR, APP_VERSION

logger = logging.getLogger("mpts")


def _qt_message_handler(mode, context, message):
    level = logging.WARNING if str(mode).lower().endswith("warning") else logging.ERROR
    logger.log(level, "Qt: %s", message)


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_AUTHOR)
    app.setApplicationName(APP_NAME)
    qInstallMessageHandler(_qt_message_handler)

    logger.info("Версия %s; VLC=%s; журнал=%s",
                APP_VERSION, getattr(vlc_setup, "VLC_AVAILABLE", False),
                get_log_path())
    window = MainWindow()
    window.show()

    rc = app.exec()

    logger.info("Завершение приложения, код %s", rc)
    vlc_setup.release_vlc_instance()
    logging.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()