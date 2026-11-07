# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import sys
from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from constants import APP_NAME, APP_AUTHOR


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_AUTHOR)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()