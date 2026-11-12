# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QStackedWidget, QWidget,
    QLabel, QPushButton, QFileDialog, QTreeWidget, QTreeWidgetItem,
    QFormLayout, QLineEdit, QMessageBox,
)

from m3u_parser import Channel, parse_m3u


class MptsWizard(QDialog):
    """
    Пошаговый мастер:
      Шаг 1 — загрузка M3U
      Шаг 2 — выбор каналов (с пометками «уже в …»)
      Шаг 3 — имя + URL → Создать MPTS
      Затем вопрос «Продолжим?» → снова шаг 2 с тем же плейлистом.
    """

    def __init__(self, parent, existing_profiles: dict[str, list],
                 preset_channels: list[Channel] | None = None):
        super().__init__(parent)
        self.existing_profiles: dict[str, list[Channel]] = {
            name: list(chans) for name, chans in existing_profiles.items()
        }
        self.created_profiles: list[tuple[str, str, list[Channel]]] = []

        # Если передан готовый список из редактора — используем его
        self.channels: list[Channel] = list(preset_channels) if preset_channels else []
        self._preset_used: bool = bool(preset_channels)
        self.playlist_path: str = "(рабочие каналы)" if preset_channels else ""

        self.setWindowTitle("Мастер создания MPTS")
        self.resize(900, 680)
        self._build_ui()
        if self.channels:
            self._populate_tree()
            self.stack.setCurrentIndex(1)
        self._update_buttons()

    # ---------- UI -------------------------------------------------------
    def _build_ui(self):
        v = QVBoxLayout(self)

        # Заголовок + подсказка сверху
        self.title = QLabel("")
        self.title.setStyleSheet("font-size: 16px; font-weight: bold;")
        v.addWidget(self.title)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(
            "background: #fffbe6; border: 1px solid #e0d090; "
            "border-radius: 4px; padding: 6px; color: #5a4a00;"
        )
        v.addWidget(self.hint)

        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color: gray;")
        v.addWidget(self.subtitle)

        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)

        self.stack.addWidget(self._page_load())
        self.stack.addWidget(self._page_channels())
        self.stack.addWidget(self._page_settings())

        row = QHBoxLayout()
        row.addStretch(1)

        self.back_btn = QPushButton("Назад")
        self.back_btn.setToolTip("Вернуться на предыдущий шаг")
        self.back_btn.clicked.connect(self._on_back)
        row.addWidget(self.back_btn)

        self.next_btn = QPushButton("Далее")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._on_next)
        row.addWidget(self.next_btn)

        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.setToolTip(
            "Закрыть мастер. Уже созданные MPTS сохранятся в главном окне."
        )
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)

        v.addLayout(row)

    def _page_load(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        row = QHBoxLayout()
        self.browse_btn = QPushButton("Выбрать файл…")
        self.browse_btn.setToolTip("Открыть плейлист в формате .m3u или .m3u8")
        self.browse_btn.clicked.connect(self._on_browse)
        row.addWidget(self.browse_btn)

        self.path_label = QLabel("(файл не выбран)")
        self.path_label.setStyleSheet("color: gray;")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.path_label, 1)
        v.addLayout(row)

        self.load_info = QLabel("")
        self.load_info.setWordWrap(True)
        v.addWidget(self.load_info)
        v.addStretch(1)
        return w

    def _page_channels(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Канал", "URL"])
        self.tree.setColumnWidth(0, 380)
        self.tree.setToolTip(
            "Отметьте галочками каналы, которые войдут в этот MPTS-поток.\n"
            "Жёлтым помечены каналы, уже используемые в других MPTS — "
            "при их добавлении появится подтверждение."
        )
        self.tree.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.tree, 1)

        self.count_label = QLabel("Выбрано: 0")
        v.addWidget(self.count_label)
        return w

    def _page_settings(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setToolTip(
            "Уникальное имя MPTS-потока. Используется для выбора в главной таблице."
        )
        self.url_edit = QLineEdit("udp://239.0.0.1:1234?pkt_size=1316")
        self.url_edit.setToolTip(
            "UDP-адрес вывода. Пример: udp://239.0.0.1:1234?pkt_size=1316\n"
            "• 239.x.x.x — multicast-адрес\n"
            "• pkt_size=1316 — рекомендуемый размер UDP-пакета"
        )
        form.addRow("Имя MPTS:", self.name_edit)
        form.addRow("URL вывода:", self.url_edit)
        v.addLayout(form)

        v.addStretch(1)
        return w

    # ---------- helpers --------------------------------------------------
    def _update_buttons(self):
        i = self.stack.currentIndex()
        idx = len(self.existing_profiles) + len(self.created_profiles) + 1
        self.back_btn.setEnabled(i > 0 and not (self._preset_used and i == 1))

        if i == 0:
            self.title.setText("Шаг 1 — загрузка плейлиста")
            self.hint.setText(
                "Нажмите «Выбрать файл…» и укажите M3U/M3U8-плейлист. "
                "Из него будут браться каналы для MPTS-потоков."
            )
            self.subtitle.setText(
                "После загрузки плейлиста нажмите «Далее», чтобы перейти к выбору каналов."
            )
            self.next_btn.setText("Далее")
            self.next_btn.setToolTip("Перейти к выбору каналов")
            self.next_btn.setEnabled(bool(self.channels))

        elif i == 1:
            self.title.setText(f"Шаг 2 — выбор каналов для MPTS #{idx}")
            self.hint.setText(
                "Отметьте каналы, которые войдут в этот MPTS. "
                "Каналы, уже включённые в другие MPTS, подсвечены жёлтым — "
                "при повторном использовании мастер спросит подтверждение."
            )
            if self._preset_used:
                self.subtitle.setText(
                    f"Каналы из рабочего плейлиста ({len(self.channels)}). "
                    f"Отметьте те, что войдут в этот MPTS."
                )
            else:
                self.subtitle.setText(
                    "Выбрано 0 каналов. Чтобы продолжить, выберите хотя бы один канал."
                )
            self.next_btn.setText("Далее")
            self.next_btn.setToolTip("Перейти к настройке имени и адреса вывода")
            self.next_btn.setEnabled(self._selected_count() > 0)

        else:
            self.title.setText(f"Шаг 3 — имя и адрес вывода для MPTS #{idx}")
            self.hint.setText(
                "Задайте уникальное имя и UDP-адрес вывода, затем нажмите "
                "«Создать MPTS». После создания можно сразу перейти к следующему потоку."
            )
            self.subtitle.setText(
                "Все выбранные каналы будут скопированы без перекодирования (copy)."
            )
            self.next_btn.setText("Создать MPTS")
            self.next_btn.setToolTip("Создать MPTS и спросить о следующем")
            self.next_btn.setEnabled(True)

    def _selected_count(self) -> int:
        n = 0
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                if g.child(j).checkState(0) == Qt.Checked:
                    n += 1
        return n

    def _selected_channels(self) -> list[Channel]:
        out: list[Channel] = []
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                c = g.child(j)
                if c.checkState(0) == Qt.Checked:
                    ch = c.data(0, Qt.UserRole)
                    if ch is not None:
                        out.append(ch)
        return out

    def _usage_map(self) -> dict[str, list[str]]:
        usage: dict[str, list[str]] = {}
        for name, chans in self.existing_profiles.items():
            for c in chans:
                usage.setdefault(c.url, []).append(name)
        for name, _url, chans in self.created_profiles:
            for c in chans:
                usage.setdefault(c.url, []).append(name)
        return usage

    def _populate_tree(self):
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            usage = self._usage_map()
            groups: dict[str, QTreeWidgetItem] = {}

            for ch in self.channels:
                g = ch.group or "Без группы"
                if g not in groups:
                    gi = QTreeWidgetItem([g, ""])
                    gi.setFlags(gi.flags() & ~Qt.ItemIsUserCheckable)
                    self.tree.addTopLevelItem(gi)
                    groups[g] = gi

                used_in = usage.get(ch.url, [])
                label = ch.name
                if used_in:
                    label += f"   [уже в: {', '.join(used_in)}]"

                ci = QTreeWidgetItem([label, ch.url])
                ci.setFlags(ci.flags() | Qt.ItemIsUserCheckable)
                ci.setCheckState(0, Qt.Unchecked)
                ci.setData(0, Qt.UserRole, ch)
                if used_in:
                    ci.setForeground(0, Qt.GlobalColor.darkYellow)
                groups[g].addChild(ci)

            self.tree.expandAll()
        finally:
            self.tree.blockSignals(False)

        self.count_label.setText("Выбрано: 0")

    # ---------- events ---------------------------------------------------
    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить плейлист", "",
            "Плейлисты (*.m3u *.m3u8);;Все файлы (*)"
        )
        if not path:
            return
        try:
            self.channels = parse_m3u(path)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка разбора", str(e))
            return

        self.playlist_path = path
        self.path_label.setText(path)
        self.load_info.setText(f"Загружено каналов: {len(self.channels)}.")
        self._populate_tree()
        self._update_buttons()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        if column != 0 or item.data(0, Qt.UserRole) is None:
            return
        n = self._selected_count()
        self.count_label.setText(f"Выбрано: {n}")
        if self.stack.currentIndex() == 1:
            self.subtitle.setText(
                f"Выбрано {n} канал(ов). Чтобы продолжить, выберите хотя бы один."
                if n == 0 else
                f"Выбрано {n} канал(ов). Нажмите «Далее», чтобы задать имя и адрес."
            )
        self._update_buttons()

    def _on_back(self):
        i = self.stack.currentIndex()
        if i > 0:
            self.stack.setCurrentIndex(i - 1)
            self._update_buttons()

    def _on_next(self):
        i = self.stack.currentIndex()

        if i == 0:
            if not self.channels:
                return
            idx = len(self.existing_profiles) + len(self.created_profiles) + 1
            if not self.name_edit.text().strip():
                self.name_edit.setText(f"MPTS-{idx}")
            self.stack.setCurrentIndex(1)
            self._update_buttons()
            return

        if i == 1:
            if self._selected_count() == 0:
                return
            self.stack.setCurrentIndex(2)
            self._update_buttons()
            return

        # i == 2 — создание
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()

        if not name or not url:
            QMessageBox.warning(self, "Не хватает данных",
                                "Укажите имя и URL вывода.")
            return

        taken = set(self.existing_profiles.keys()) | {n for n, _, _ in self.created_profiles}
        if name in taken:
            QMessageBox.warning(self, "Имя занято",
                                f"Имя «{name}» уже используется. Выберите другое.")
            return

        selected = self._selected_channels()
        if not selected:
            QMessageBox.warning(self, "Нет каналов",
                                "Выберите хотя бы один канал.")
            return

        usage = self._usage_map()
        duplicates = [c for c in selected if c.url in usage]
        if duplicates:
            lines = "\n".join(
                f"  • {c.name}  — уже в: {', '.join(usage[c.url])}"
                for c in duplicates
            )
            res = QMessageBox.question(
                self,
                "Каналы уже используются",
                "Следующие каналы уже входят в другие MPTS-потоки:\n\n"
                f"{lines}\n\n"
                "Всё равно добавить их в этот MPTS?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if res != QMessageBox.Yes:
                return

        self.created_profiles.append((name, url, selected))
        QMessageBox.information(
            self, "Готово",
            f"«{name}» создан. Каналов в потоке: {len(selected)}."
        )

        res = QMessageBox.question(
            self, "Продолжим?",
            "Добавить ещё один MPTS-поток из этого же плейлиста?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if res == QMessageBox.Yes:
            self._populate_tree()
            next_idx = len(self.existing_profiles) + len(self.created_profiles) + 1
            self.name_edit.setText(f"MPTS-{next_idx}")
            self.url_edit.setText(f"udp://239.0.0.{next_idx}:1234?pkt_size=1316")
            self.stack.setCurrentIndex(1)
            self._update_buttons()
        else:
            self.accept()