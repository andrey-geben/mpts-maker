# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import sys

# ВАЖНО: до PySide6 — чтобы portable VLC успел настроиться
import vlc_setup  # noqa: F401

from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from constants import APP_NAME, APP_AUTHOR


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_AUTHOR)
    app.setApplicationName(APP_NAME)

    window = MainWindow()
    window.show()

    rc = app.exec()

    vlc_setup.release_vlc_instance()
    sys.exit(rc)


if __name__ == "__main__":
    main()