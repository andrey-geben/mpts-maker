# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QFormLayout,
    QDialogButtonBox, QFrame,
)

from constants import (
    APP_NAME, APP_VERSION, APP_AUTHOR, APP_COPYRIGHT,
    APP_GITHUB, APP_LICENSE, APP_DESCRIPTION, APP_CREDITS,
)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"О программе — {APP_NAME}")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(f"<h2 style='margin:0'>{APP_NAME}</h2>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        version = QLabel(f"Версия {APP_VERSION}")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet("color: gray;")
        layout.addWidget(version)

        desc = QLabel(APP_DESCRIPTION)
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        form = QFormLayout()
        form.addRow("<b>Автор:</b>",      QLabel(APP_AUTHOR))
        form.addRow("<b>Копирайт:</b>",   QLabel(APP_COPYRIGHT))
        form.addRow("<b>Лицензия:</b>",   QLabel(APP_LICENSE))

        link = QLabel(f'<a href="{APP_GITHUB}">{APP_GITHUB}</a>')
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        form.addRow("<b>GitHub:</b>", link)
        layout.addLayout(form)

        credits = QLabel(APP_CREDITS)
        credits.setWordWrap(True)
        credits.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(credits)

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.button(QDialogButtonBox.Ok).setText("Закрыть")
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)