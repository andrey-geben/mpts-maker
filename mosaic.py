# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QLabel, QPushButton, QToolBar, QMenu,
    QMessageBox, QComboBox,
)

from vlc_setup import VLC_AVAILABLE, get_vlc_instance
from m3u_parser import Channel

if VLC_AVAILABLE:
    import vlc


PAGE_SIZE = 4
DECODE_WIDTH = 480
DECODE_HEIGHT = 270


class VlcTile(QFrame):
    """Одна плитка мозаики."""

    def __init__(self, instance, base_url: str, program_num: int,
                 channel_name: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "VlcTile { background: #000; border: 1px solid #444; }"
        )
        self.instance = instance
        self.base_url = base_url
        self.program_num = program_num
        self.channel_name = channel_name
        self.player = None
        self._muted = True

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.video = QWidget(self)
        self.video.setStyleSheet("background: black;")
        v.addWidget(self.video, 1)

        bottom = QWidget(self)
        bottom.setStyleSheet("background: rgba(0,0,0,180);")
        h = QHBoxLayout(bottom)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)

        self.indicator = QLabel()
        self.indicator.setFixedSize(10, 10)
        self.indicator.setStyleSheet(
            "background: #999; border-radius: 5px;"
        )
        h.addWidget(self.indicator)

        self.name_label = QLabel(channel_name)
        self.name_label.setStyleSheet(
            "color: white; font-size: 11px; font-weight: bold;"
        )
        h.addWidget(self.name_label, 1)

        self.program_label = QLabel(f"#{program_num}")
        self.program_label.setStyleSheet(
            "color: #aaa; font-size: 10px;"
        )
        h.addWidget(self.program_label)

        v.addWidget(bottom)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

        self._alive_timer = QTimer(self)
        self._alive_timer.timeout.connect(self._update_indicator)
        self._alive_timer.start(1000)

    def _build_url(self) -> str:
        url = self.base_url
        if "?" in url:
            url = url.split("?")[0]
        if url.startswith("udp://") and not url.startswith("udp://@"):
            url = "udp://@" + url[len("udp://"):]
        return f"{url}?overrun_nonfatal=1&fifo_size=524288"

    def start_playing(self):
        if self.player is not None:
            return

        media = self.instance.media_new(self._build_url())
        media.add_option(f":program={self.program_num}")

        # === КРИТИЧНЫЕ ОПТИМИЗАЦИИ ===
        # Полностью отключить аудио-декодер (глушит не только вывод,
        # но и прекращает декодирование AAC — экономия CPU/GPU)
        media.add_option(":no-audio")
        # Отключить субтитры и OSD
        media.add_option(":no-sub-autodetect-file")
        # Ограничить разрешение видео-вывода
        media.add_option(f":vdec-width={DECODE_WIDTH}")
        media.add_option(f":vdec-height={DECODE_HEIGHT}")
        # Меньше буферов — быстрее старт
        media.add_option(":live-caching=200")
        media.add_option(":network-caching=200")
        # Синхронизация отключена для превью (для «смотрю, что идёт»)
        media.add_option(":clock-jitter=0")
        media.add_option(":clock-synchro=0")

        self.player = self.instance.media_player_new()
        self.player.set_media(media)
        self.player.set_hwnd(int(self.video.winId()))
        self.player.audio_set_mute(True)
        self.player.play()

    def stop_playing(self):
        if self.player is not None:
            try:
                self.player.stop()
                self.player.release()
            except Exception:
                pass
            self.player = None
        self._set_indicator("gray")

    def restart(self):
        self.stop_playing()
        QTimer.singleShot(500, self.start_playing)

    def set_mute(self, muted: bool):
        self._muted = muted
        if self.player is not None:
            self.player.audio_set_mute(muted)

    def _set_indicator(self, color: str):
        colors = {
            "green": "#4caf50", "yellow": "#ffb300",
            "red": "#f44336", "gray": "#999999",
        }
        self.indicator.setStyleSheet(
            f"background: {colors.get(color, '#999')}; border-radius: 5px;"
        )

    def _update_indicator(self):
        if self.player is None or not VLC_AVAILABLE:
            self._set_indicator("gray")
            return
        state = self.player.get_state()
        if state in (vlc.State.Playing, vlc.State.Buffering):
            self._set_indicator("green")
        elif state == vlc.State.Paused:
            self._set_indicator("yellow")
        elif state == vlc.State.Error:
            self._set_indicator("red")
        else:
            self._set_indicator("gray")

    def _context_menu(self, pos):
        menu = QMenu(self)
        act_restart = menu.addAction("🔄 Перезапустить")
        act_restart.triggered.connect(self.restart)
        menu.exec(self.mapToGlobal(pos))

    def closeEvent(self, event):
        self.stop_playing()
        self._alive_timer.stop()
        super().closeEvent(event)


class MosaicWindow(QDialog):
    """Окно мозаики с пагинацией и переключателем сетки."""

    def __init__(self, parent, mpts_name: str, output_url: str,
                 channels: list[Channel]):
        super().__init__(parent)
        self.setWindowTitle(f"Мозаика — {mpts_name}")
        self.resize(1280, 800)

        self.mpts_name = mpts_name
        self.output_url = output_url
        self.channels = list(channels)
        self.page = 0
        self.tiles: list[VlcTile] = []
        self._all_muted = True
        self._page_buttons: list[QPushButton] = []
        self._grid_cols = 2   # 2 = 2×2 (4 плитки), 1 = 1×1, 2 = 1×2
        self._grid_rows = 2

        if not VLC_AVAILABLE:
            self._show_vlc_error()
            return

        self.instance = get_vlc_instance()
        self._build_ui()
        QTimer.singleShot(300, lambda: self._load_page(0))

    def _show_vlc_error(self):
        QMessageBox.critical(
            self, "VLC не загружен",
            "Библиотека VLC не найдена.\n\n"
            "Проверьте, что в папке программы есть «vlc_lib» с файлами:\n"
            "  • libvlc.dll\n  • libvlccore.dll\n  • plugins/ (папка целиком)"
        )
        QTimer.singleShot(0, self.reject)

    @property
    def total_pages(self) -> int:
        n = len(self.channels)
        if n == 0:
            return 1
        return (n + PAGE_SIZE - 1) // PAGE_SIZE

    def _visible_count(self) -> int:
        return self._grid_cols * self._grid_rows

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        tb = QToolBar()
        tb.setMovable(False)

        self.mute_action = QAction("🔇 Заглушить всех", self)
        self.mute_action.triggered.connect(self._toggle_mute_all)
        tb.addAction(self.mute_action)

        restart_all = QAction("🔄 Перезапустить", self)
        restart_all.triggered.connect(self._restart_all)
        tb.addAction(restart_all)

        tb.addSeparator()
        tb.addWidget(QLabel("  Сетка:  "))

        self.grid_combo = QComboBox()
        self.grid_combo.addItems(["1 плитка", "2 плитки (1×2)", "4 плитки (2×2)"])
        self.grid_combo.setCurrentIndex(2)   # по умолчанию 4
        self.grid_combo.setToolTip(
            "Если 4 плитки тормозят — выберите 1 или 2. "
            "При 1 плитке декодирование максимальное, при 4 — минимальное."
        )
        self.grid_combo.currentIndexChanged.connect(self._on_grid_changed)
        tb.addWidget(self.grid_combo)

        v.addWidget(tb)

        self.grid_host = QWidget()
        self.grid_host.setStyleSheet("background: #1a1a1a;")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(4)
        v.addWidget(self.grid_host, 1)

        bottom = QWidget()
        bottom.setStyleSheet("background: #f0f0f0; border-top: 1px solid #ccc;")
        bh = QVBoxLayout(bottom)
        bh.setContentsMargins(8, 4, 8, 6)
        bh.setSpacing(4)

        info_row = QHBoxLayout()
        info = QLabel(
            f"<b>MPTS:</b> {self.mpts_name} &nbsp;|&nbsp; "
            f"<b>Каналов:</b> {len(self.channels)}"
        )
        info.setStyleSheet("color: #333;")
        info_row.addWidget(info, 1)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        info_row.addWidget(close_btn)
        bh.addLayout(info_row)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)

        self.prev_btn = QPushButton("◀ Назад")
        self.prev_btn.setMinimumWidth(90)
        self.prev_btn.clicked.connect(self._prev_page)
        nav_row.addWidget(self.prev_btn)

        self.page_label = QLabel()
        self.page_label.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #333; "
            "padding: 0 12px;"
        )
        nav_row.addWidget(self.page_label)

        self.next_btn = QPushButton("Вперёд ▶")
        self.next_btn.setMinimumWidth(90)
        self.next_btn.clicked.connect(self._next_page)
        nav_row.addWidget(self.next_btn)

        nav_row.addSpacing(16)
        nav_row.addWidget(QLabel("Страница:"))

        self.page_buttons_host = QWidget()
        self.page_buttons_layout = QHBoxLayout(self.page_buttons_host)
        self.page_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.page_buttons_layout.setSpacing(4)
        nav_row.addWidget(self.page_buttons_host)

        nav_row.addStretch(1)
        bh.addLayout(nav_row)

        v.addWidget(bottom)

    def _on_grid_changed(self, idx: int):
        if idx == 0:
            self._grid_cols, self._grid_rows = 1, 1
        elif idx == 1:
            self._grid_cols, self._grid_rows = 2, 1
        else:
            self._grid_cols, self._grid_rows = 2, 2
        self._load_page(self.page)

    def _chunk_for_page(self, page: int) -> list[Channel]:
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(self.channels))
        return self.channels[start:end]

    def _load_page(self, page: int):
        """Полная перезагрузка страницы: освободить старые, подождать, создать новые."""
        page = max(0, min(page, self.total_pages - 1))
        self.page = page

        # 1. Останавливаем и удаляем старые плитки
        for tile in self.tiles:
            tile.stop_playing()
            tile.setParent(None)
            tile.deleteLater()
        self.tiles.clear()

        # 2. Очищаем сетку
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._refresh_pagination_ui()

        # 3. Создаём новые плитки с задержкой — даём Qt/VLC освободить HWND
        QTimer.singleShot(300, self._create_tiles_for_current_page)

    def _create_tiles_for_current_page(self):
        chunk = self._chunk_for_page(self.page)
        start_num = self.page * PAGE_SIZE + 1
        visible = self._visible_count()
        chunk = chunk[:visible]

        for i, ch in enumerate(chunk):
            tile = VlcTile(
                instance=self.instance,
                base_url=self.output_url,
                program_num=start_num + i,
                channel_name=ch.name,
            )
            self.tiles.append(tile)
            row, col = divmod(i, self._grid_cols)
            self.grid.addWidget(tile, row, col)

        # Каскадный запуск с задержкой
        for i in range(len(self.tiles)):
            QTimer.singleShot(
                200 + i * 200,
                lambda idx=i: self.tiles[idx].start_playing()
                if idx < len(self.tiles) else None,
            )

    def _refresh_pagination_ui(self):
        total = self.total_pages
        cur = self.page + 1
        start_num = self.page * PAGE_SIZE + 1
        end_num = min(start_num + PAGE_SIZE - 1, len(self.channels))
        n_here = end_num - start_num + 1
        n_total = len(self.channels)

        self.page_label.setText(
            f"Страница {cur} / {total}  ·  "
            f"каналы {start_num}–{end_num} из {n_total} ({n_here} шт.)"
        )

        self.prev_btn.setEnabled(cur > 1)
        self.next_btn.setEnabled(cur < total)

        for btn in self._page_buttons:
            btn.setParent(None)
            btn.deleteLater()
        self._page_buttons.clear()
        while self.page_buttons_layout.count():
            item = self.page_buttons_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if total <= 10:
            pages = list(range(1, total + 1))
        else:
            half = 3
            lo = max(1, cur - half)
            hi = min(total, cur + half)
            if hi - lo < 6:
                if lo == 1:
                    hi = min(total, 7)
                elif hi == total:
                    lo = max(1, total - 6)
            pages = list(range(lo, hi + 1))
            if lo > 1:
                pages = [1, "…"] + pages
            if hi < total:
                pages = pages + ["…", total]

        for p in pages:
            if p == "…":
                lbl = QLabel("…")
                lbl.setStyleSheet("color: #888; padding: 0 4px;")
                self.page_buttons_layout.addWidget(lbl)
                continue
            btn = QPushButton(str(p))
            btn.setFixedSize(30, 26)
            btn.setCheckable(True)
            if p == cur:
                btn.setChecked(True)
                btn.setStyleSheet(
                    "QPushButton { background: #1976d2; color: white; "
                    "border: 1px solid #0d47a1; font-weight: bold; }"
                )
            btn.clicked.connect(
                lambda _=False, target=p - 1: self._go_to_page(target)
            )
            self._page_buttons.append(btn)
            self.page_buttons_layout.addWidget(btn)

    def _go_to_page(self, page: int):
        if page < 0 or page >= self.total_pages:
            return
        if page == self.page:
            return
        self._load_page(page)

    def _prev_page(self):
        self._go_to_page(self.page - 1)

    def _next_page(self):
        self._go_to_page(self.page + 1)

    def _toggle_mute_all(self):
        self._all_muted = not self._all_muted
        for tile in self.tiles:
            tile.set_mute(self._all_muted)
        self.mute_action.setText(
            "🔊 Включить звук" if self._all_muted else "🔇 Заглушить всех"
        )

    def _restart_all(self):
        for tile in self.tiles:
            tile.restart()

    def closeEvent(self, event):
        for tile in self.tiles:
            tile.stop_playing()
        super().closeEvent(event)