# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QDialogButtonBox, QMessageBox, QFileDialog, QFormLayout,
    QLabel, QAbstractItemView,
)

from m3u_parser import Channel


class PlaylistEditorDialog(QDialog):
    COLS = ["Рабочий", "Имя", "Группа", "URL"]

    def __init__(self, parent, all_channels: list[Channel],
                 working_urls: set[str] | None = None,
                 playlist_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Редактор плейлиста")
        self.resize(1150, 740)
        self.playlist_path = playlist_path

        self.all_channels = [replace(c) for c in all_channels]
        if working_urls is None:
            self._working_urls = {c.url for c in self.all_channels}
        else:
            self._working_urls = set(working_urls)

        self.working_channels: list[Channel] = []
        self._updating = False

        self._build_ui()
        self._populate()

    # ---------- UI -------------------------------------------------------
    def _build_ui(self):
        v = QVBoxLayout(self)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Поиск:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("по имени, группе или URL…")
        self.search_edit.textChanged.connect(self._apply_filter)
        filters.addWidget(self.search_edit, 1)

        filters.addWidget(QLabel("Группа:"))
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(200)
        self.group_combo.currentTextChanged.connect(self._apply_filter)
        filters.addWidget(self.group_combo)
        v.addLayout(filters)

        bulk = QHBoxLayout()
        for label, tip, handler in [
            ("✓ Отметить все видимые",
             "Сделать все видимые рабочими", lambda: self._set_visible(True)),
            ("✗ Снять все видимые",
             "Убрать видимые из рабочих", lambda: self._set_visible(False)),
            ("⇄ Инвертировать",
             "Инвертировать отметку видимых", self._invert_visible),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            bulk.addWidget(b)

        bulk.addStretch(1)

        add_btn = QPushButton("➕ Добавить")
        add_btn.setToolTip("Добавить новый канал вручную")
        add_btn.clicked.connect(self._add_channel)
        bulk.addWidget(add_btn)

        del_btn = QPushButton("🗑 Удалить выделенные")
        del_btn.setToolTip("Удалить выделенные строки из плейлиста")
        del_btn.clicked.connect(self._delete_selected)
        bulk.addWidget(del_btn)
        v.addLayout(bulk)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 230)
        self.table.setColumnWidth(2, 180)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.table.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.table, 1)

        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #555; padding: 4px;")
        v.addWidget(self.count_label)

        hint = QLabel(
            "💡 Двойной клик по «Имя», «Группа» или «URL» — редактирование. "
            "Галочка «Рабочий» — канал попадёт в мастер создания MPTS "
            "и в анализ железа. Снятые каналы остаются в плейлисте, но "
            "не участвуют в построении потоков."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "background:#fffbe6; border:1px solid #e0d090; "
            "border-radius:4px; padding:6px; color:#5a4a00;"
        )
        v.addWidget(hint)

        row = QHBoxLayout()
        save_btn = QPushButton("💾 Сохранить как…")
        save_btn.setToolTip("Сохранить плейлист в новый .m3u файл")
        save_btn.clicked.connect(self._save_as)
        row.addWidget(save_btn)
        row.addStretch(1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Применить")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        row.addWidget(btns)
        v.addLayout(row)

    def _populate(self):
        self._updating = True
        try:
            groups = sorted({c.group for c in self.all_channels if c.group})
            self.group_combo.blockSignals(True)
            self.group_combo.clear()
            self.group_combo.addItem("Все группы")
            for g in groups:
                self.group_combo.addItem(g)
            self.group_combo.blockSignals(False)

            self.table.setRowCount(len(self.all_channels))
            for row, ch in enumerate(self.all_channels):
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                )
                check.setCheckState(
                    Qt.Checked if ch.url in self._working_urls else Qt.Unchecked
                )
                self.table.setItem(row, 0, check)

                for col, text in [(1, ch.name), (2, ch.group), (3, ch.url)]:
                    item = QTableWidgetItem(text)
                    item.setFlags(
                        Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    )
                    self.table.setItem(row, col, item)
        finally:
            self._updating = False
            self._apply_filter()

    # ---------- фильтрация ----------------------------------------------
    def _apply_filter(self):
        text = self.search_edit.text().strip().lower()
        group = self.group_combo.currentText()
        visible = 0

        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 1)
            group_item = self.table.item(row, 2)
            url_item = self.table.item(row, 3)
            if not (name_item and group_item and url_item):
                continue
            match_text = (
                not text
                or text in name_item.text().lower()
                or text in group_item.text().lower()
                or text in url_item.text().lower()
            )
            match_group = (group == "Все группы"
                           or group_item.text() == group)
            hide = not (match_text and match_group)
            self.table.setRowHidden(row, hide)
            if not hide:
                visible += 1

        total = self.table.rowCount()
        checked = sum(
            1 for r in range(total)
            if not self.table.isRowHidden(r)
            and self.table.item(r, 0)
            and self.table.item(r, 0).checkState() == Qt.Checked
        )
        self.count_label.setText(
            f"Показано: {visible} из {total}  ·  отмечено рабочих: {checked}"
        )

    def _set_visible(self, state: bool):
        self._updating = True
        try:
            target = Qt.Checked if state else Qt.Unchecked
            for row in range(self.table.rowCount()):
                if not self.table.isRowHidden(row):
                    self.table.item(row, 0).setCheckState(target)
        finally:
            self._updating = False
            self._apply_filter()

    def _invert_visible(self):
        self._updating = True
        try:
            for row in range(self.table.rowCount()):
                if not self.table.isRowHidden(row):
                    item = self.table.item(row, 0)
                    new = (Qt.Unchecked
                           if item.checkState() == Qt.Checked
                           else Qt.Checked)
                    item.setCheckState(new)
        finally:
            self._updating = False
            self._apply_filter()

    # ---------- редактирование ------------------------------------------
    def _on_item_changed(self, item: QTableWidgetItem):
        if self._updating:
            return
        row, col = item.row(), item.column()
        if row >= len(self.all_channels):
            return
        ch = self.all_channels[row]

        if col == 0:
            return

        new_value = item.text().strip()
        if col == 1:
            if not new_value:
                QMessageBox.warning(self, "Пустое имя",
                                    "Имя канала не может быть пустым.")
                self._updating = True
                item.setText(ch.name)
                self._updating = False
                return
            ch.name = new_value
        elif col == 2:
            ch.group = new_value
        elif col == 3:
            if not new_value:
                QMessageBox.warning(self, "Пустой URL",
                                    "URL канала не может быть пустым.")
                self._updating = True
                item.setText(ch.url)
                self._updating = False
                return
            old_url = ch.url
            ch.url = new_value
            if old_url in self._working_urls:
                self._working_urls.discard(old_url)
                self._working_urls.add(new_value)

        self._apply_filter()

    def _add_channel(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Добавить канал")
        form = QFormLayout(dlg)
        name_edit = QLineEdit()
        group_edit = QLineEdit()
        url_edit = QLineEdit()
        form.addRow("Имя:", name_edit)
        form.addRow("Группа:", group_edit)
        form.addRow("URL:", url_edit)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("OK")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.Accepted:
            return
        if not name_edit.text().strip() or not url_edit.text().strip():
            QMessageBox.warning(self, "Ошибка", "Имя и URL обязательны.")
            return

        new_ch = Channel(
            name=name_edit.text().strip(),
            url=url_edit.text().strip(),
            group=group_edit.text().strip(),
        )
        self.all_channels.append(new_ch)
        self._working_urls.add(new_ch.url)
        self._populate()

    def _delete_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()},
                      reverse=True)
        if not rows:
            QMessageBox.information(
                self, "Ничего не выбрано",
                "Выделите строки (Ctrl+Click или Shift+Click), "
                "затем нажмите «Удалить выделенные»."
            )
            return

        self._updating = True
        try:
            for row in rows:
                url = self.all_channels[row].url
                del self.all_channels[row]
                self._working_urls.discard(url)
                self.table.removeRow(row)
        finally:
            self._updating = False
            self._apply_filter()

    # ---------- сохранение ----------------------------------------------
    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить плейлист как",
            self.playlist_path or "playlist.m3u",
            "M3U (*.m3u);;M3U8 (*.m3u8);;Все файлы (*)"
        )
        if not path:
            return

        lines = ["#EXTM3U"]
        for ch in self.all_channels:
            attrs = []
            if ch.tvg_id:
                attrs.append(f'tvg-id="{ch.tvg_id}"')
            if ch.logo:
                attrs.append(f'tvg-logo="{ch.logo}"')
            if ch.group:
                attrs.append(f'group-title="{ch.group}"')
            attr_str = (" " + " ".join(attrs)) if attrs else ""
            lines.append(f"#EXTINF:-1{attr_str},{ch.name}")
            lines.append(ch.url)

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            QMessageBox.information(self, "Сохранено",
                                    f"Плейлист сохранён:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения",
                                 f"Не удалось сохранить файл:\n{e}")

    # ---------- применение ----------------------------------------------
    def _apply(self):
        working: list[Channel] = []
        for row, ch in enumerate(self.all_channels):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                working.append(ch)

        if not working:
            res = QMessageBox.question(
                self, "Ни один канал не отмечен",
                "Вы не отметили ни одного канала как рабочий.\n\n"
                "Это значит, что в мастере и анализе железа будут "
                "доступны ВСЕ каналы плейлиста.\n\nПродолжить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if res != QMessageBox.Yes:
                return
            working = list(self.all_channels)

        self.working_channels = working
        self.accept()