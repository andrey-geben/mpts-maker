# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QLabel, QPushButton, QScrollArea, QToolBar, QMenu,
    QMessageBox, QComboBox,
)

from vlc_setup import VLC_AVAILABLE, get_vlc_instance
from m3u_parser import Channel

if VLC_AVAILABLE:
    import vlc


class VlcTile(QFrame):
    """Одна плитка мозаики — один канал (программа) из MPTS."""

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
        sep = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{sep}overrun_nonfatal=1&fifo_size=524288"

    def start_playing(self):
        if self.player is not None:
            return
        media = self.instance.media_new(self._build_url())
        media.add_option(f":program={self.program_num}")
        media.add_option(":live-caching=300")
        media.add_option(":network-caching=300")
        self.player = self.instance.media_player_new()
        self.player.set_media(media)
        self.player.set_hwnd(int(self.video.winId()))
        self.player.audio_set_mute(self._muted)
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
            "green": "#4caf50",
            "yellow": "#ffb300",
            "red": "#f44336",
            "gray": "#999999",
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

        act_mute = menu.addAction(
            "🔊 Включить звук" if self._muted else "🔇 Заглушить"
        )
        act_mute.triggered.connect(lambda: self.set_mute(not self._muted))

        menu.exec(self.mapToGlobal(pos))

    def closeEvent(self, event):
        self.stop_playing()
        self._alive_timer.stop()
        super().closeEvent(event)


class MosaicWindow(QDialog):
    """Окно мозаики: одна плитка на каждый канал MPTS."""

    def __init__(self, parent, mpts_name: str, output_url: str,
                 channels: list[Channel]):
        super().__init__(parent)
        self.setWindowTitle(f"Мозаика — {mpts_name}")
        self.resize(1280, 800)

        self.mpts_name = mpts_name
        self.output_url = output_url
        self.channels = channels

        if not VLC_AVAILABLE:
            self._show_vlc_error()
            return

        if len(channels) > 16:
            res = QMessageBox.question(
                self, "Много каналов",
                f"В мозаике будет {len(channels)} каналов одновременно.\n\n"
                f"Это серьёзная нагрузка на GPU/CPU. На слабых машинах "
                f"картинки могут дёргаться.\n\n"
                f"Продолжить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if res != QMessageBox.Yes:
                QTimer.singleShot(0, self.reject)
                return

        self.instance = get_vlc_instance()
        self.tiles: list[VlcTile] = []
        self._all_muted = True
        self._cols = self._calc_cols(len(channels))

        self._build_ui()
        self._build_tiles()
        self._start_all()

    def _show_vlc_error(self):
        QMessageBox.critical(
            self, "VLC не загружен",
            "Библиотека VLC не найдена.\n\n"
            "Проверьте, что в папке программы есть «vlc_lib» с файлами:\n"
            "  • libvlc.dll\n"
            "  • libvlccore.dll\n"
            "  • plugins/ (папка целиком)\n\n"
            "Скачайте vlc-3.0.x-win64.7z с videolan.org и скопируйте "
            "эти три позиции в vlc_lib/."
        )
        QTimer.singleShot(0, self.reject)

    @staticmethod
    def _calc_cols(n: int) -> int:
        if n <= 1:
            return 1
        if n <= 4:
            return 2
        if n <= 9:
            return 3
        if n <= 16:
            return 4
        return 5

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        tb = QToolBar()
        tb.setMovable(False)

        self.mute_action = QAction("🔇 Заглушить всех", self)
        self.mute_action.setToolTip("Переключить звук всех плиток")
        self.mute_action.triggered.connect(self._toggle_mute_all)
        tb.addAction(self.mute_action)

        restart_all = QAction("🔄 Перезапустить все", self)
        restart_all.setToolTip("Перезапустить все плееры")
        restart_all.triggered.connect(self._restart_all)
        tb.addAction(restart_all)

        tb.addSeparator()
        tb.addWidget(QLabel("  Сетка:  "))

        self.grid_combo = QComboBox()
        self.grid_combo.addItems(
            ["Авто", "1×1", "2×2", "3×3", "4×4", "5×5"]
        )
        self.grid_combo.setCurrentText("Авто")
        self.grid_combo.currentTextChanged.connect(self._on_grid_changed)
        tb.addWidget(self.grid_combo)

        v.addWidget(tb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: #1a1a1a;")

        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(4)
        scroll.setWidget(self.grid_widget)
        v.addWidget(scroll, 1)

        bottom = QWidget()
        bottom.setStyleSheet(
            "background: #f0f0f0; border-top: 1px solid #ccc;"
        )
        bh = QHBoxLayout(bottom)
        bh.setContentsMargins(8, 4, 8, 4)

        info = QLabel(
            f"<b>MPTS:</b> {self.mpts_name} &nbsp;|&nbsp; "
            f"<b>URL:</b> "
            f"<span style='font-family: monospace'>{self.output_url}</span> "
            f"&nbsp;|&nbsp; <b>Каналов:</b> {len(self.channels)}"
        )
        bh.addWidget(info, 1)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        bh.addWidget(close_btn)

        v.addWidget(bottom)

    def _build_tiles(self):
        for i, ch in enumerate(self.channels):
            tile = VlcTile(
                instance=self.instance,
                base_url=self.output_url,
                program_num=i + 1,
                channel_name=ch.name,
            )
            self.tiles.append(tile)
            row = i // self._cols
            col = i % self._cols
            self.grid.addWidget(tile, row, col)

    def _start_all(self):
        """Каскадный запуск: первая плитка через 0.5с, каждая +200 мс."""
        for i in range(len(self.tiles)):
            QTimer.singleShot(
                500 + i * 200,
                lambda idx=i: self.tiles[idx].start_playing(),
            )

    def _toggle_mute_all(self):
        self._all_muted = not self._all_muted
        for tile in self.tiles:
            tile.set_mute(self._all_muted)
        self.mute_action.setText(
            "🔇 Заглушить всех" if not self._all_muted else "🔊 Включить звук"
        )

    def _restart_all(self):
        for tile in self.tiles:
            tile.restart()

    def _on_grid_changed(self, text: str):
        cols = self._calc_cols(len(self.tiles)) if text == "Авто" \
            else int(text.split("×")[0])
        if cols == self._cols:
            return
        self._cols = cols
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // cols, i % cols)

    def closeEvent(self, event):
        for tile in self.tiles:
            tile.stop_playing()
        super().closeEvent(event)