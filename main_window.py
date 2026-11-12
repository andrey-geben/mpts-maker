# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import math
import os
from datetime import datetime

import psutil
from PySide6.QtCore import Qt, QEvent, QByteArray, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QToolBar, QFileDialog, QMessageBox, QDialog, QFormLayout,
    QLineEdit, QDialogButtonBox, QLabel, QPushButton, QFrame, QPlainTextEdit,
    QApplication, QMenu, QGroupBox, QSpinBox, QDoubleSpinBox,
)

from m3u_parser import Channel, parse_m3u
from ffmpeg_manager import (
    FFmpegProcess, explain_return_code, probe_url,
    check_required_tools, find_tool,
)
from system_monitor import SystemMonitor, shutdown_nvml
from constants import APP_NAME, APP_COPYRIGHT, APP_VERSION, APP_AUTHOR
from about_dialog import AboutDialog
from wizard import MptsWizard
from storage import load_config, save_config, CONFIG_VERSION
from stream_validator import probe_mpts, ProgramInfo
from playlist_editor import PlaylistEditorDialog
from hardware_analyzer import gather_hardware, analyze
from vlc_setup import VLC_AVAILABLE
from concurrent.futures import ThreadPoolExecutor, as_completed


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _format_speed(bps: float) -> str:
    if bps < 1000:
        return f"{bps:.0f} bps"
    if bps < 1_000_000:
        return f"{bps / 1000:.1f} Kbps"
    if bps < 1_000_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    return f"{bps / 1_000_000_000:.2f} Gbps"


def _format_uptime(start: datetime | None) -> str:
    if not start:
        return "—"
    delta = datetime.now() - start
    s = int(delta.total_seconds())
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}ч {m:02d}м {sec:02d}с"
    if m:
        return f"{m}м {sec:02d}с"
    return f"{sec}с"


def _copy_to_clipboard(text: str) -> bool:
    try:
        QGuiApplication.clipboard().setText(text)
        return True
    except Exception:
        return False


def _mpts_url(index: int) -> str:
    n = index + 1
    if n > 254:
        return f"udp://239.0.{n // 254}.{n % 254 + 1}:1234?pkt_size=1316"
    return f"udp://239.0.0.{n}:1234?pkt_size=1316"


# ---------------------------------------------------------------------------
# Нижняя панель метрик
# ---------------------------------------------------------------------------

class FooterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)

        self.cpu_label = QLabel("CPU: --")
        self.ram_label = QLabel("RAM: --")
        self.gpu_label = QLabel("GPU: --")
        self.net_label = QLabel("Сеть: --")

        for lbl in (self.cpu_label, self.ram_label,
                    self.gpu_label, self.net_label):
            lbl.setStyleSheet("padding: 0 12px;")
            layout.addWidget(lbl)
            layout.addWidget(self._make_sep())
        layout.addStretch(1)

    @staticmethod
    def _make_sep() -> QFrame:
        s = QFrame()
        s.setFrameShape(QFrame.VLine)
        s.setFrameShadow(QFrame.Sunken)
        return s

    def update_metrics(self, data: dict):
        self.cpu_label.setText(f"CPU: {data.get('cpu', 0):.1f}%")
        self.ram_label.setText(f"RAM: {data.get('ram', 0):.1f}%")

        gpus = data.get("gpu", [])
        if gpus:
            g = gpus[0]
            prefix = "GPU" if len(gpus) == 1 else f"GPU{g['index']}"
            self.gpu_label.setText(
                f"{prefix}: {g['load']}% "
                f"({g['mem_used']:.0f}/{g['mem_total']:.0f} MB)"
            )
        else:
            self.gpu_label.setText("GPU: --")

        up = data.get("net_up", 0)
        down = data.get("net_down", 0)
        self.net_label.setText(
            f"Сеть: ↑ {_format_speed(up)}  ↓ {_format_speed(down)}"
        )


# ---------------------------------------------------------------------------
# Диалог создания MPTS
# ---------------------------------------------------------------------------

class NewMptsDialog(QDialog):
    def __init__(self, default_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый MPTS-поток")
        layout = QFormLayout(self)
        self.name_edit = QLineEdit(default_name)
        self.url_edit = QLineEdit("udp://239.0.0.1:1234?pkt_size=1316")
        layout.addRow("Имя:", self.name_edit)
        layout.addRow("URL вывода:", self.url_edit)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("OK")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def values(self):
        return self.name_edit.text().strip(), self.url_edit.text().strip()


# ---------------------------------------------------------------------------
# Диалоги правки каналов MPTS
# ---------------------------------------------------------------------------

class RemoveChannelsDialog(QDialog):
    def __init__(self, mpts_name: str, channels: list[Channel], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Правка каналов «{mpts_name}»")
        self.resize(760, 540)
        v = QVBoxLayout(self)

        hint = QLabel(
            "Отметьте каналы, которые нужно убрать из этого MPTS-потока.\n"
            "Сам канал останется в плейлисте."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "background:#fffbe6; border:1px solid #e0d090; "
            "border-radius:4px; padding:8px; color:#5a4a00;"
        )
        v.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Канал", "URL"])
        self.tree.setColumnWidth(0, 320)
        v.addWidget(self.tree, 1)

        for ch in channels:
            item = QTreeWidgetItem([ch.name, ch.url])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            item.setData(0, Qt.UserRole, ch)
            self.tree.addTopLevelItem(item)

        row = QHBoxLayout()
        all_btn = QPushButton("Выбрать все")
        all_btn.clicked.connect(lambda: self._set_all(Qt.Checked))
        row.addWidget(all_btn)
        none_btn = QPushButton("Снять все")
        none_btn.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        row.addWidget(none_btn)
        row.addStretch(1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Удалить выбранные")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        row.addWidget(btns)
        v.addLayout(row)

    def _set_all(self, state):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)

    def selected_channels(self) -> list[Channel]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                ch = item.data(0, Qt.UserRole)
                if ch is not None:
                    out.append(ch)
        return out


class ReplaceChannelsDialog(QDialog):
    def __init__(self, mpts_name: str, available_channels: list[Channel],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Добавление каналов в «{mpts_name}»")
        self.resize(760, 560)
        v = QVBoxLayout(self)

        hint = QLabel(
            f"Каналы из плейлиста, которые ещё не входят в «{mpts_name}».\n"
            "Отметьте те, что хотите добавить."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "background:#e6f4ff; border:1px solid #a0c8e0; "
            "border-radius:4px; padding:8px; color:#1a4a6b;"
        )
        v.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Канал", "URL"])
        self.tree.setColumnWidth(0, 320)
        v.addWidget(self.tree, 1)

        for ch in available_channels:
            item = QTreeWidgetItem([ch.name, ch.url])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            item.setData(0, Qt.UserRole, ch)
            self.tree.addTopLevelItem(item)

        row = QHBoxLayout()
        all_btn = QPushButton("Выбрать все")
        all_btn.clicked.connect(lambda: self._set_all(Qt.Checked))
        row.addWidget(all_btn)
        none_btn = QPushButton("Снять все")
        none_btn.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        row.addWidget(none_btn)
        row.addStretch(1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Добавить выбранные")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        row.addWidget(btns)
        v.addLayout(row)

    def _set_all(self, state):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)

    def selected_channels(self) -> list[Channel]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                ch = item.data(0, Qt.UserRole)
                if ch is not None:
                    out.append(ch)
        return out


# ---------------------------------------------------------------------------
# Диалог информации о потоке
# ---------------------------------------------------------------------------

class StreamInfoDialog(QDialog):
    def __init__(self, parent, name: str, profile: dict, on_restart):
        super().__init__(parent)
        self.profile = profile
        self.on_restart = on_restart
        self.setWindowTitle(f"Информация о потоке «{name}»")
        self.resize(900, 660)

        v = QVBoxLayout(self)
        status = profile.get("last_status", "Остановлен")
        is_error = (status == "Ошибка")

        banner = QLabel()
        banner.setWordWrap(True)
        banner.setAlignment(Qt.AlignCenter)
        if is_error:
            banner.setText(f"<b>Ошибка</b> · {name}")
            banner.setStyleSheet(
                "background:#ffe1e1; color:#8b0000; font-size:14px; "
                "padding:10px; border-radius:4px;"
            )
        elif status == "Работает":
            banner.setText(f"<b>Работает</b> · {name}")
            banner.setStyleSheet(
                "background:#e6ffe6; color:#006600; font-size:14px; "
                "padding:10px; border-radius:4px;"
            )
        elif status == "Завершён":
            banner.setText(f"<b>Завершён</b> · {name}")
            banner.setStyleSheet(
                "background:#fff4d6; color:#7a5d00; font-size:14px; "
                "padding:10px; border-radius:4px;"
            )
        else:
            banner.setText(f"<b>Остановлен</b> · {name}")
            banner.setStyleSheet(
                "background:#eeeeee; color:#333333; font-size:14px; "
                "padding:10px; border-radius:4px;"
            )
        v.addWidget(banner)

        url_row = QHBoxLayout()
        url_label = QLabel(f"<b>URL вывода:</b> {profile['url']}")
        url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_label.setWordWrap(True)
        url_label.setStyleSheet(
            "background:#f4f4f4; border:1px solid #cccccc; "
            "border-radius:4px; padding:8px;"
        )
        url_row.addWidget(url_label, 1)
        self.copy_url_btn = QPushButton("📋 Копировать URL")
        self.copy_url_btn.clicked.connect(self._copy_url)
        url_row.addWidget(self.copy_url_btn, 0, Qt.AlignTop)
        v.addLayout(url_row)

        if is_error and profile.get("last_error"):
            short_reason, _ = profile["last_error"]
            reason = QLabel(f"<b>Причина:</b> {short_reason}")
            reason.setWordWrap(True)
            reason.setStyleSheet(
                "background:#fffbe6; border:1px solid #e0d090; "
                "border-radius:4px; padding:8px; color:#5a4a00;"
            )
            v.addWidget(reason)

            rc = profile.get("last_return_code")
            if rc is not None:
                rc_text = explain_return_code(rc)
                rc_label = QLabel(f"<b>Код возврата FFmpeg:</b> {rc_text}")
                rc_label.setWordWrap(True)
                rc_label.setStyleSheet(
                    "background:#f4f4f4; border:1px solid #cccccc; "
                    "border-radius:4px; padding:8px;"
                )
                v.addWidget(rc_label)

        info_form = QFormLayout()
        info_form.addRow("<b>Каналов:</b>",
                         QLabel(str(len(profile["channels"]))))
        if profile.get("started_at"):
            info_form.addRow(
                "<b>Запущен:</b>",
                QLabel(profile["started_at"].strftime("%d.%m.%Y %H:%M:%S")),
            )
            info_form.addRow(
                "<b>В работе:</b>",
                QLabel(_format_uptime(profile["started_at"])),
            )
        if profile.get("last_fps") and profile["last_fps"] != "-":
            info_form.addRow(
                "<b>Статистика FFmpeg:</b>",
                QLabel(f"FPS: {profile.get('last_fps', '—')}, "
                       f"битрейт: {profile.get('last_bitrate', '—')}"),
            )
        v.addLayout(info_form)

        log_title = QLabel("Журнал FFmpeg (последние строки):")
        log_title.setStyleSheet("font-weight: bold; margin-top: 8px;")
        v.addWidget(log_title)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlainText(profile.get("last_log_tail", "(журнал пуст)"))
        self.log_box.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 11px;"
        )
        v.addWidget(self.log_box, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        copy_log_btn = QPushButton("Копировать журнал")
        copy_log_btn.clicked.connect(self._copy_log)
        row.addWidget(copy_log_btn)
        self.restart_btn = QPushButton("Перезапустить")
        self.restart_btn.setEnabled(status != "Работает")
        self.restart_btn.clicked.connect(self._restart)
        row.addWidget(self.restart_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        v.addLayout(row)

    def _copy_url(self):
        if _copy_to_clipboard(self.profile["url"]):
            QMessageBox.information(
                self, "Скопировано",
                f"URL скопирован:\n\n{self.profile['url']}"
            )

    def _copy_log(self):
        QApplication.clipboard().setText(self.log_box.toPlainText())
        QMessageBox.information(self, "Скопировано",
                                "Журнал скопирован в буфер обмена.")

    def _restart(self):
        self.accept()
        self.on_restart()


# ---------------------------------------------------------------------------
# Визуальная проверка MPTS (лампочки)
# ---------------------------------------------------------------------------

class StreamVisualCheckDialog(QDialog):
    COLORS = {
        "ok": "#4caf50",
        "partial": "#ffb300",
        "error": "#f44336",
        "missing": "#9e9e9e",
    }
    MARKS = {
        "ok": "[OK]", "partial": "[PART]",
        "error": "[FAIL]", "missing": "[MISS]",
    }
    COLS = 8

    def __init__(self, parent, name: str, url: str,
                 channels: list[Channel], programs: list[ProgramInfo],
                 ffprobe_path: str):
        super().__init__(parent)
        self.name = name
        self.url = url
        self.channels = list(channels)
        self.programs = sorted(programs, key=lambda p: p.program_num)
        self.ffprobe_path = ffprobe_path
        self._worker = None

        self.setWindowTitle(f"Визуальная проверка «{name}»")
        self.resize(960, 620)

        v = QVBoxLayout(self)

        url_label = QLabel(f"<b>URL:</b> {url}")
        url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_label.setWordWrap(True)
        url_label.setStyleSheet(
            "background:#f4f4f4; border:1px solid #cccccc; "
            "border-radius:4px; padding:8px;"
        )
        v.addWidget(url_label)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-size: 13px; margin: 6px 0;")
        v.addWidget(self.summary_label)

        legend_row = QHBoxLayout()
        legend_row.addWidget(self._make_legend_item("#4caf50", "работает"))
        legend_row.addWidget(self._make_legend_item(
            "#ffb300", "частично (видео/аудио)"
        ))
        legend_row.addWidget(self._make_legend_item(
            "#f44336", "ошибка / нет потоков"
        ))
        legend_row.addWidget(self._make_legend_item(
            "#9e9e9e", "программа не найдена"
        ))
        legend_row.addStretch(1)
        v.addLayout(legend_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("background: #fafafa;")
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(12)
        self.scroll.setWidget(self._grid_widget)
        v.addWidget(self.scroll, 1)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 Обновить")
        self.refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self.refresh_btn)

        details_btn = QPushButton("📊 Таблица подробностей")
        details_btn.clicked.connect(self._show_details)
        btn_row.addWidget(details_btn)

        copy_btn = QPushButton("📋 Копировать отчёт")
        copy_btn.clicked.connect(self._copy_report)
        btn_row.addWidget(copy_btn)

        btn_row.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)

        self._rebuild_grid()

    def _statuses(self):
        result = []
        for i, ch in enumerate(self.channels):
            if i >= len(self.programs):
                result.append(("missing", "Программа не найдена"))
                continue
            prog = self.programs[i]
            if prog.error:
                result.append(("error", prog.error))
                continue
            has_v = any(s.codec_type == "video" for s in prog.streams)
            has_a = any(s.codec_type == "audio" for s in prog.streams)
            if has_v and has_a:
                result.append(("ok", "видео + аудио"))
            elif has_v:
                result.append(("partial", "только видео"))
            elif has_a:
                result.append(("partial", "только аудио"))
            else:
                result.append(("error", "нет потоков"))
        return result

    def _rebuild_grid(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        statuses = self._statuses()
        if not statuses:
            lbl = QLabel("В профиле нет каналов")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color: gray; font-size: 14px;")
            self._grid.addWidget(lbl, 0, 0, 1, self.COLS)
        else:
            for i, (status, detail) in enumerate(statuses):
                w = self._make_lamp_widget(status, detail, i)
                self._grid.addWidget(w, i // self.COLS, i % self.COLS,
                                     Qt.AlignTop)
        self.summary_label.setText(self._compute_summary())

    def _make_lamp_widget(self, status, detail, index):
        ch = self.channels[index]
        color = self.COLORS.get(status, "#9e9e9e")
        w = QWidget()
        w.setMinimumWidth(90)
        w.setMaximumWidth(120)
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(3)

        lamp = QLabel()
        lamp.setFixedSize(38, 38)
        lamp.setStyleSheet(
            f"background: {color}; border-radius: 19px; "
            f"border: 2px solid #444444;"
        )
        v.addWidget(lamp, 0, Qt.AlignHCenter)

        num = QLabel(str(index + 1))
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet("font-weight: bold; font-size: 11px; color: #333;")
        v.addWidget(num)

        name = QLabel(ch.name)
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        name.setStyleSheet("font-size: 10px; color: #555;")
        name.setMaximumHeight(30)
        v.addWidget(name)

        tip = f"{ch.name}\nСтатус: {detail}\nURL: {ch.url}"
        w.setToolTip(tip)
        lamp.setToolTip(tip)
        return w

    def _make_legend_item(self, color, text):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(
            f"background: {color}; border-radius: 7px; "
            f"border: 1px solid #333;"
        )
        h.addWidget(dot)
        label = QLabel(text)
        label.setStyleSheet("font-size: 11px; color: #444;")
        h.addWidget(label)
        return w

    def _compute_summary(self):
        statuses = self._statuses()
        total = len(statuses)
        ok = sum(1 for s, _ in statuses if s == "ok")
        partial = sum(1 for s, _ in statuses if s == "partial")
        bad = total - ok - partial
        header = (
            f"<b>Программ:</b> {len(self.programs)} &nbsp;/&nbsp; "
            f"<b>каналов:</b> {total}<br>"
        )
        parts = []
        if ok:
            parts.append(f"<span style='color:#2e7d32; font-weight:bold'>"
                         f"✔ работает: {ok}</span>")
        if partial:
            parts.append(f"<span style='color:#ef6c00; font-weight:bold'>"
                         f"⚠ частично: {partial}</span>")
        if bad:
            parts.append(f"<span style='color:#c62828; font-weight:bold'>"
                         f"✖ не работают: {bad}</span>")
        if not parts:
            parts.append("<span style='color:gray'>нет данных</span>")
        return header + " &nbsp;&nbsp; ".join(parts)

    def _refresh(self):
        if self._worker is not None:
            try:
                if self._worker.isRunning():
                    return
            except RuntimeError:
                self._worker = None
        self._worker = None
        self.refresh_btn.setEnabled(False)
        self.summary_label.setText("Проверка…")

        worker = _CheckWorker(self.url, self.ffprobe_path,
                              timeout_s=10, parent=self)
        worker.done.connect(self._on_refresh_done)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_worker_finished(self):
        self._worker = None

    def _on_refresh_done(self, programs, error):
        self.refresh_btn.setEnabled(True)
        if error:
            QMessageBox.warning(self, "Ошибка обновления",
                                f"Не удалось проверить поток:\n\n{error}")
            self.summary_label.setText(self._compute_summary())
            return
        self.programs = sorted(programs, key=lambda p: p.program_num)
        self._rebuild_grid()

    def _show_details(self):
        dlg = StreamCheckDialog(self, self.name, self.url, self.programs)
        dlg.exec()

    def _copy_report(self):
        lines = [
            f"Проверка MPTS «{self.name}»",
            f"URL: {self.url}",
            f"Каналов: {len(self.channels)}",
            f"Программ: {len(self.programs)}",
            "",
        ]
        for i, (status, detail) in enumerate(self._statuses()):
            mark = self.MARKS.get(status, "[?]")
            ch = self.channels[i]
            lines.append(f"{mark} {i + 1:>2}. {ch.name} — {detail}")
        if _copy_to_clipboard("\n".join(lines)):
            QMessageBox.information(self, "Скопировано",
                                    "Отчёт скопирован в буфер обмена.")

# ---------------------------------------------------------------------------
# Таблица подробностей ffprobe
# ---------------------------------------------------------------------------

class StreamCheckDialog(QDialog):
    def __init__(self, parent, name, url, programs):
        super().__init__(parent)
        self.setWindowTitle(f"Проверка потока «{name}»")
        self.resize(860, 560)

        v = QVBoxLayout(self)
        url_label = QLabel(f"<b>URL:</b> {url}")
        url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_label.setWordWrap(True)
        url_label.setStyleSheet(
            "background:#f4f4f4; border:1px solid #cccccc; "
            "border-radius:4px; padding:8px;"
        )
        v.addWidget(url_label)

        summary = QLabel(f"Программ: <b>{len(programs)}</b>")
        summary.setStyleSheet("margin: 8px 0; font-size: 13px;")
        v.addWidget(summary)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(
            ["№", "Service Name", "Provider", "Видео", "Аудио"]
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        for i, prog in enumerate(programs):
            table.insertRow(i)
            if prog.error:
                for col in range(5):
                    item = QTableWidgetItem(prog.error if col == 1 else "—")
                    item.setForeground(QBrush(QColor("#8b0000")))
                    table.setItem(i, col, item)
                continue
            video_desc = self._fmt(prog.streams, "video")
            audio_desc = self._fmt(prog.streams, "audio")
            table.setItem(i, 0, QTableWidgetItem(str(prog.program_num)))
            table.setItem(i, 1, QTableWidgetItem(prog.service_name))
            table.setItem(i, 2, QTableWidgetItem(prog.service_provider))
            table.setItem(i, 3, QTableWidgetItem(video_desc or "—"))
            table.setItem(i, 4, QTableWidgetItem(audio_desc or "—"))

        v.addWidget(table, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.button(QDialogButtonBox.Ok).setText("Закрыть")
        btns.accepted.connect(self.accept)
        v.addWidget(btns)

    @staticmethod
    def _fmt(streams, codec_type):
        parts = []
        for s in streams:
            if s.codec_type != codec_type:
                continue
            if codec_type == "video":
                if s.width and s.height:
                    parts.append(f"{s.codec_name} {s.width}x{s.height}")
                else:
                    parts.append(s.codec_name or "video")
            else:
                ch = f" {s.channels}ch" if s.channels else ""
                sr = f" {s.sample_rate}Hz" if s.sample_rate else ""
                parts.append(f"{s.codec_name}{ch}{sr}".strip())
        return ", ".join(parts) if parts else "—"


# ---------------------------------------------------------------------------
# Диалог анализа железа
# ---------------------------------------------------------------------------

class HardwareAnalysisDialog(QDialog):
    def __init__(self, parent, channels: list[Channel]):
        super().__init__(parent)
        self.setWindowTitle("Анализ железа и рекомендации")
        self.resize(860, 760)
        self.channels = channels
        self.created_profiles: list[tuple[str, str, list[Channel]]] = []
        self.hw = gather_hardware()

        v = QVBoxLayout(self)

        hw_group = QGroupBox("Текущее железо")
        hw_form = QFormLayout(hw_group)
        hw_form.addRow("CPU (физических / логических):",
                       QLabel(f"{self.hw.cpu_cores} / {self.hw.cpu_logical}"))
        hw_form.addRow("Текущая загрузка CPU:",
                       QLabel(f"{self.hw.cpu_load:.1f}%"))
        hw_form.addRow(
            "RAM (всего / свободно):",
            QLabel(f"{self.hw.ram_total_mb} / "
                   f"{self.hw.ram_total_mb - self.hw.ram_used_mb} МБ"),
        )
        net_str = ", ".join(f"{i['name']}={i['speed_mbps']} Mbps"
                            for i in self.hw.net_interfaces) or "нет данных"
        hw_form.addRow("Сеть:", QLabel(net_str))
        v.addWidget(hw_group)

        params_group = QGroupBox("Параметры расчёта")
        params_form = QFormLayout(params_group)

        self.total_edit = QSpinBox()
        self.total_edit.setRange(1, 1000)
        self.total_edit.setValue(min(len(self.channels), 60))
        self.total_edit.valueChanged.connect(self._recalc)

        self.bitrate_edit = QDoubleSpinBox()
        self.bitrate_edit.setRange(0.5, 50.0)
        self.bitrate_edit.setDecimals(1)
        self.bitrate_edit.setValue(4.0)
        self.bitrate_edit.setSuffix(" Mbps")
        self.bitrate_edit.valueChanged.connect(self._recalc)

        self.per_mpts_edit = QSpinBox()
        self.per_mpts_edit.setRange(1, 20)
        self.per_mpts_edit.setValue(10)
        self.per_mpts_edit.valueChanged.connect(self._recalc)

        params_form.addRow("Каналов запустить:", self.total_edit)
        params_form.addRow("Средний битрейт канала:", self.bitrate_edit)
        params_form.addRow("Каналов в одном MPTS:", self.per_mpts_edit)
        v.addWidget(params_group)

        result_group = QGroupBox("Расчёт и рекомендации")
        rl = QVBoxLayout(result_group)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.RichText)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.setStyleSheet(
            "background:#f5f5f5; border:1px solid #ccc; "
            "border-radius:4px; padding:10px;"
        )
        rl.addWidget(self.result_label)
        v.addWidget(result_group, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        apply_btn = QPushButton("✅ Создать профили")
        apply_btn.setToolTip(
            "Создать MPTS-профили по расчёту и взять каналы из плейлиста"
        )
        apply_btn.clicked.connect(self._apply)
        row.addWidget(apply_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        v.addLayout(row)

        self._recalc()

    def _recalc(self):
        num = self.total_edit.value()
        bitrate = self.bitrate_edit.value()
        per = self.per_mpts_edit.value()
        rec = analyze(self.hw, num, bitrate)
        num_profiles = math.ceil(num / per) if num > 0 else 0

        html = "<table cellspacing='4'>"
        html += (f"<tr><td>Потянет по <b>CPU</b>:</td>"
                 f"<td>{rec.max_channels_by_cpu} кан.</td></tr>")
        html += (f"<tr><td>Потянет по <b>RAM</b>:</td>"
                 f"<td>{rec.max_channels_by_ram} кан.</td></tr>")
        html += (f"<tr><td>Потянет по <b>сети</b>:</td>"
                 f"<td>{rec.max_channels_by_net} кан.</td></tr>")
        html += (f"<tr><td><b>Итоговый предел:</b></td>"
                 f"<td><b>{rec.max_channels_overall} кан.</b></td></tr>")
        html += "</table><br>"
        html += (f"<b>План:</b> {num_profiles} профил(ей) × {per} каналов "
                 f"= {num_profiles * per} мест<br>")
        html += (f"<b>Оценка:</b> CPU ~{rec.estimated_cpu_load:.1f}%, "
                 f"RAM ~{rec.estimated_ram_mb} МБ, "
                 f"сеть ~{rec.estimated_out_mbps:.0f} Mbps<br><br>")

        if rec.warnings:
            html += "<b style='color:#b71c1c'>⚠ Предупреждения:</b><ul>"
            for w in rec.warnings:
                html += f"<li>{w}</li>"
            html += "</ul>"
        else:
            html += ("<span style='color:#2e7d32'>"
                     "<b>✔ Всё в пределах нормы</b></span>")

        self.result_label.setText(html)

    def _unique_name(self, base, existing):
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    def _apply(self):
        num = min(self.total_edit.value(), len(self.channels))
        per = self.per_mpts_edit.value()
        if num < self.total_edit.value():
            QMessageBox.information(
                self, "Мало каналов",
                f"В плейлисте только {len(self.channels)} каналов. "
                f"Будет использовано {num}."
            )
        if num == 0:
            return

        existing = set(getattr(self.parent(), "profiles", {}).keys())
        profiles = []
        for i in range(0, num, per):
            chunk = self.channels[i:i + per]
            idx = len(profiles)
            letter = chr(ord('A') + idx) if idx < 26 else f"A{idx - 25}"
            name = self._unique_name(f"MPTS-{letter}", existing)
            existing.add(name)
            profiles.append((name, _mpts_url(idx), chunk))

        self.created_profiles = profiles
        self.accept()


# ---------------------------------------------------------------------------
# Фоновые воркеры
# ---------------------------------------------------------------------------

class _ProbeWorker(QThread):
    done = Signal(list)

    def __init__(self, channels, ffmpeg_path, timeout_s=5, parent=None):
        super().__init__(parent)
        self.channels = channels
        self.ffmpeg_path = ffmpeg_path
        self.timeout_s = timeout_s

    def run(self):
        results = [None] * len(self.channels)
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(probe_url, ch.url, self.ffmpeg_path,
                          self.timeout_s): i
                for i, ch in enumerate(self.channels)
            }
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    ok, err = fut.result()
                except Exception as e:
                    ok, err = False, str(e)
                results[i] = (self.channels[i], ok, err)
        self.done.emit(results)


class _CheckWorker(QThread):
    done = Signal(list, str)

    def __init__(self, url, ffprobe_path, timeout_s=10, parent=None):
        super().__init__(parent)
        self.url = url
        self.ffprobe_path = ffprobe_path
        self.timeout_s = timeout_s

    def run(self):
        programs, error = probe_mpts(self.url, self.ffprobe_path,
                                     self.timeout_s)
        self.done.emit(programs, error)


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    COLS = ["MPTS", "URL вывода", "Каналы", "Статус", "FPS", "Битрейт",
            "Действия"]
    COL_TIPS = [
        "Имя MPTS-потока",
        "URL вывода (UDP multicast)",
        "Сколько каналов",
        "Текущее состояние. Клик — подробности",
        "FPS (сбрасывается при остановке)",
        "Битрейт (сбрасывается при остановке)",
        "▶ запуск · ■ стоп · ℹ подробности · ✎ правка · 📋 URL · "
        "🔍 ffprobe · 🎬 мозаика · ✖ удалить",
    ]
    ACTION_COL = 6
    STATUS_COL = 3
    FPS_COL = 4
    BITRATE_COL = 5
    URL_COL = 1

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} — {APP_AUTHOR}")
        self.resize(1500, 800)

        self.channels: list[Channel] = []
        self.working_channels: list[Channel] = []
        self.profiles: dict[str, dict] = {}
        self.current_mpts: str | None = None
        self._updating_tree = False
        self._restoring = False
        self._last_playlist: str = ""
        self._probe_workers: list[_ProbeWorker] = []
        self._check_workers: list[_CheckWorker] = []

        self._build_ui()
        self._build_menu()
        self._check_tools_at_startup()

        self.sys_monitor = SystemMonitor(interval=2.0)
        self.sys_monitor.updated.connect(self._on_system)
        self.sys_monitor.start()

        self._hint("Загрузите M3U-плейлист или запустите мастер создания MPTS")
        self._restore_session()

    # ---------------- UI ------------------------------------------------
    def _build_ui(self):
        tb = QToolBar("Панель")
        tb.setMovable(False)
        self.addToolBar(tb)

        for text, tip, slot in [
            ("Загрузить M3U", "Открыть файл плейлиста", self.on_load_m3u),
            ("Добавить MPTS", "Создать пустой профиль вручную",
             self.on_add_mpts),
            ("Мастер…", "Пошаговое создание MPTS", self.on_wizard),
            ("Анализ железа…",
             "Оценить предел системы и создать профили", self.on_hardware_analysis),
        ]:
            a = QAction(text, self)
            a.setToolTip(tip)
            a.setStatusTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)

        tb.addSeparator()
        for text, tip, slot in [
            ("▶ Запустить выделенные",
             "Запустить все выделенные MPTS", self.on_start_selected),
            ("■ Остановить выделенные",
             "Остановить все выделенные MPTS", self.on_stop_selected),
            ("🎬 Мозаика",
             "Открыть мозаику для выделенного MPTS", self.on_mosaic_selected),
        ]:
            a = QAction(text, self)
            a.setToolTip(tip)
            a.setStatusTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)

        central = QWidget()
        self.setCentralWidget(central)
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self.splitter = QSplitter(Qt.Horizontal)
        vbox.addWidget(self.splitter, 1)

        # Левая панель
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        pl_bar = QWidget()
        pl_layout = QHBoxLayout(pl_bar)
        pl_layout.setContentsMargins(8, 4, 8, 4)
        pl_layout.setSpacing(6)

        self.playlist_label = QLabel("Плейлист не загружен")
        self.playlist_label.setStyleSheet("color: gray;")
        self.playlist_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pl_layout.addWidget(self.playlist_label, 1)

        self.edit_playlist_btn = QPushButton("✎")
        self.edit_playlist_btn.setFixedSize(30, 26)
        self.edit_playlist_btn.setToolTip("Редактор плейлиста")
        self.edit_playlist_btn.setEnabled(False)
        self.edit_playlist_btn.clicked.connect(self.on_edit_playlist)
        pl_layout.addWidget(self.edit_playlist_btn)

        self.delete_playlist_btn = QPushButton("✖")
        self.delete_playlist_btn.setFixedSize(30, 26)
        self.delete_playlist_btn.setToolTip("Удалить (закрыть) плейлист")
        self.delete_playlist_btn.setEnabled(False)
        self.delete_playlist_btn.clicked.connect(self.on_delete_playlist)
        pl_layout.addWidget(self.delete_playlist_btn)

        pl_bar.setStyleSheet(
            "background: #f5f5f5; border-bottom: 1px solid #d0d0d0;"
        )
        left_layout.addWidget(pl_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Канал", "URL"])
        self.tree.setColumnWidth(0, 280)
        self.tree.itemChanged.connect(self._on_channel_check)
        left_layout.addWidget(self.tree, 1)

        self.splitter.addWidget(left)

        # Правая панель
        self.table = QTableWidget(0, len(self.COLS))
        for c, (name, tip) in enumerate(zip(self.COLS, self.COL_TIPS)):
            item = QTableWidgetItem(name)
            item.setToolTip(tip)
            self.table.setHorizontalHeaderItem(c, item)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.URL_COL, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.STATUS_COL, QHeaderView.Interactive)
        self.table.setColumnWidth(self.STATUS_COL, 240)
        header.setSectionResizeMode(self.FPS_COL, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.BITRATE_COL,
                                    QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.ACTION_COL, QHeaderView.Fixed)
        self.table.setColumnWidth(self.ACTION_COL, 275)
        self.table.verticalHeader().setDefaultSectionSize(34)

        self.table.itemSelectionChanged.connect(self._on_mpts_selected)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_dblclick)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            self._on_table_context_menu
        )
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)

        self.splitter.addWidget(self.table)
        self.splitter.setSizes([550, 950])

        self.log = QLabel("Готово")
        self.log.setStyleSheet("padding: 4px 8px;")
        vbox.addWidget(self.log)

        self.footer = FooterWidget()
        self.footer.setStyleSheet(
            "background: #f0f0f0; border-top: 1px solid #d0d0d0;"
        )
        vbox.addWidget(self.footer)

        self.copyright_label = QLabel(APP_COPYRIGHT)
        self.copyright_label.setStyleSheet(
            "color: gray; padding-right: 6px;"
        )
        self.statusBar().addPermanentWidget(self.copyright_label)

    def _build_menu(self):
        menubar = self.menuBar()
        help_menu = menubar.addMenu("&Справка")
        about_action = QAction("О &программе", self)
        about_action.setShortcut("F1")
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        help_menu.addSeparator()
        about_qt_action = QAction("О &Qt", self)
        about_qt_action.triggered.connect(
            lambda: QMessageBox.aboutQt(self, f"О Qt — {APP_NAME}")
        )
        help_menu.addAction(about_qt_action)

    def _show_about(self):
        AboutDialog(self).exec()

    def _hint(self, text: str):
        self.statusBar().showMessage(text, 8000)
        self.log.setText(text)

    # ---------------- проверка утилит ----------------------------------
    def _check_tools_at_startup(self):
        ok, missing = check_required_tools()
        if ok and VLC_AVAILABLE:
            return

        parts = []
        if missing:
            parts.append("Не найдены утилиты FFmpeg:\n  • "
                         + "\n  • ".join(missing))
        if not VLC_AVAILABLE:
            parts.append("Библиотека VLC не загружена (мозаика недоступна).\n"
                         "Скачайте vlc-3.0.x-win64.7z и положите в vlc_lib/ "
                         "три позиции: libvlc.dll, libvlccore.dll, plugins/")

        if not parts:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Неполная комплектация")
        box.setIcon(QMessageBox.Warning)
        box.setText("\n\n".join(parts))
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _update_playlist_bar(self):
        n_total = len(self.channels)
        n_work = len(self.working_channels) or n_total
        suffix = f"каналов: {n_total}"
        if n_work < n_total:
            suffix += f" (рабочих: {n_work})"

        if self.channels and self._last_playlist:
            name = os.path.basename(self._last_playlist)
            self.playlist_label.setText(f"📄 {name} — {suffix}")
            self.playlist_label.setStyleSheet(
                "color: #333; font-weight: bold;"
            )
            self.edit_playlist_btn.setEnabled(True)
            self.delete_playlist_btn.setEnabled(True)
        elif self.channels:
            self.playlist_label.setText(f"Плейлист — {suffix}")
            self.playlist_label.setStyleSheet(
                "color: #333; font-weight: bold;"
            )
            self.edit_playlist_btn.setEnabled(True)
            self.delete_playlist_btn.setEnabled(True)
        else:
            self.playlist_label.setText("Плейлист не загружен")
            self.playlist_label.setStyleSheet("color: gray;")
            self.edit_playlist_btn.setEnabled(False)
            self.delete_playlist_btn.setEnabled(False)

    # ---------------- сессия -------------------------------------------
    def _restore_session(self):
        cfg = load_config()

        geo = cfg.get("window_geometry")
        if geo:
            try:
                self.restoreGeometry(
                    QByteArray.fromBase64(geo.encode("ascii"))
                )
            except Exception:
                pass

        split = cfg.get("splitter_state")
        if split:
            try:
                self.splitter.restoreState(
                    QByteArray.fromBase64(split.encode("ascii"))
                )
            except Exception:
                pass

        last_pl = cfg.get("last_playlist") or ""
        if last_pl and os.path.exists(last_pl):
            self._load_playlist_from_path(last_pl, silent=True)

        saved_urls = cfg.get("working_urls")
        if saved_urls and self.channels:
            allowed = set(saved_urls)
            restored = [c for c in self.channels if c.url in allowed]
            self.working_channels = restored or list(self.channels)
            self._rebuild_tree()
        elif self.channels:
            self.working_channels = list(self.channels)

        self._restoring = True
        try:
            for pdata in cfg.get("profiles", []):
                try:
                    name = pdata["name"]
                    url = pdata["url"]
                    channels = [
                        Channel(
                            name=c.get("name", "Unnamed"),
                            url=c.get("url", ""),
                            group=c.get("group", ""),
                            logo=c.get("logo", ""),
                            tvg_id=c.get("tvg_id", ""),
                        )
                        for c in pdata.get("channels", [])
                    ]
                    self._add_profile(name, url, channels)
                except Exception:
                    continue
        finally:
            self._restoring = False

        if self.profiles and self.table.rowCount() > 0:
            self.table.selectRow(0)

        n = len(self.profiles)
        if n or self._last_playlist:
            pl_note = (f"плейлист: {os.path.basename(self._last_playlist)}"
                       if self._last_playlist else "плейлист не загружен")
            self._hint(f"Восстановлено MPTS: {n}, {pl_note}.")
            try:
                from storage import config_path
                self.copyright_label.setToolTip(
                    f"Настройки сохраняются автоматически.\n"
                    f"Файл конфигурации: {config_path()}"
                )
            except Exception:
                pass

    def _save_session(self):
        if self._restoring:
            return
        profiles_data = []
        for name, prof in self.profiles.items():
            profiles_data.append({
                "name": name,
                "url": prof["url"],
                "channels": [
                    {
                        "name": c.name, "url": c.url,
                        "group": c.group, "logo": c.logo,
                        "tvg_id": c.tvg_id,
                    }
                    for c in prof["channels"]
                ],
            })
        try:
            data = {
                "version": CONFIG_VERSION,
                "last_playlist": self._last_playlist,
                "window_geometry":
                    self.saveGeometry().toBase64().data().decode("ascii"),
                "splitter_state":
                    self.splitter.saveState().toBase64().data().decode("ascii"),
                "profiles": profiles_data,
                "working_urls": [c.url for c in self.working_channels],
            }
            save_config(data)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QEvent.MouseMove:
            idx = self.table.indexAt(event.pos())
            if idx.isValid() and idx.column() == self.STATUS_COL:
                self.table.viewport().setCursor(Qt.PointingHandCursor)
            else:
                self.table.viewport().setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)

    # ---------------- M3U ----------------------------------------------
    def on_load_m3u(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить плейлист", "",
            "Плейлисты (*.m3u *.m3u8);;Все файлы (*)"
        )
        if not path:
            return
        if self._load_playlist_from_path(path):
            self._save_session()

    def _load_playlist_from_path(self, path: str,
                                 silent: bool = False) -> bool:
        try:
            self.channels = parse_m3u(path)
        except Exception as e:
            if not silent:
                QMessageBox.critical(
                    self, "Ошибка",
                    f"Не удалось разобрать плейлист:\n{e}"
                )
            return False

        self._last_playlist = path
        self.working_channels = list(self.channels)
        self._rebuild_tree()
        self._update_playlist_bar()

        if not silent:
            self._hint(
                f"Загружено каналов: {len(self.channels)}. "
                f"Создайте MPTS кнопкой «Мастер…» или «Добавить MPTS»."
            )

        if self.current_mpts:
            self._sync_tree_with_profile(self.current_mpts)
        return True

    def _rebuild_tree(self):
        self._updating_tree = True
        try:
            self.tree.clear()
            groups: dict[str, QTreeWidgetItem] = {}
            for ch in (self.working_channels or self.channels):
                g = ch.group or "Без группы"
                if g not in groups:
                    item = QTreeWidgetItem([g, ""])
                    item.setFlags(
                        item.flags() & ~Qt.ItemIsUserCheckable
                    )
                    self.tree.addTopLevelItem(item)
                    groups[g] = item
                child = QTreeWidgetItem([ch.name, ch.url])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                child.setData(0, Qt.UserRole, ch)
                groups[g].addChild(child)
            self.tree.expandAll()
        finally:
            self._updating_tree = False

    def _sync_tree_with_profile(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        urls = {c.url for c in prof["channels"]}
        self._updating_tree = True
        try:
            for i in range(self.tree.topLevelItemCount()):
                g = self.tree.topLevelItem(i)
                for j in range(g.childCount()):
                    c = g.child(j)
                    ch = c.data(0, Qt.UserRole)
                    if ch is None:
                        continue
                    c.setCheckState(
                        0,
                        Qt.Checked if ch.url in urls else Qt.Unchecked
                    )
        finally:
            self._updating_tree = False

    def _on_channel_check(self, item, column):
        if self._updating_tree or column != 0:
            return
        ch = item.data(0, Qt.UserRole)
        if ch is None:
            return
        if self.current_mpts is None:
            self._updating_tree = True
            item.setCheckState(0, Qt.Unchecked)
            self._updating_tree = False
            self._hint("Сначала выделите MPTS справа, затем отмечайте")
            return
        prof = self.profiles[self.current_mpts]
        urls = {c.url for c in prof["channels"]}
        if item.checkState(0) == Qt.Checked:
            if ch.url not in urls:
                prof["channels"].append(ch)
        else:
            prof["channels"] = [c for c in prof["channels"]
                                if c.url != ch.url]
        self.table.item(prof["row"], 2).setText(
            str(len(prof["channels"]))
        )
        self._save_session()

    # ---------------- плейлист -----------------------------------------
    def on_edit_playlist(self):
        if not self.channels:
            QMessageBox.information(self, "Нет плейлиста",
                                    "Сначала загрузите M3U.")
            return
        working_urls = ({c.url for c in self.working_channels}
                        if self.working_channels else None)
        dlg = PlaylistEditorDialog(
            self, self.channels,
            working_urls=working_urls,
            playlist_path=self._last_playlist,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        self.channels = dlg.all_channels
        self.working_channels = dlg.working_channels
        self._rebuild_tree()
        self._update_playlist_bar()
        self._save_session()
        self._hint(
            f"Плейлист: всего {len(self.channels)}, "
            f"рабочих {len(self.working_channels)}. "
            f"Мастер и анализ используют только рабочих."
        )

    def on_delete_playlist(self):
        if not self.channels:
            return
        name = (os.path.basename(self._last_playlist)
                if self._last_playlist else "плейлист")
        res = QMessageBox.question(
            self, "Удалить плейлист",
            f"Удалить плейлист «{name}» из программы?\n\n"
            f"Файл на диске не изменяется. MPTS-профили не затрагиваются.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            return
        self.channels = []
        self.working_channels = []
        self._last_playlist = ""
        self._rebuild_tree()
        self._update_playlist_bar()
        self._save_session()
        self._hint("Плейлист удалён из программы")

    # ---------------- MPTS ---------------------------------------------
    def on_add_mpts(self):
        default = f"MPTS-{len(self.profiles) + 1}"
        dlg = NewMptsDialog(default, self)
        if dlg.exec() != QDialog.Accepted:
            return
        name, url = dlg.values()
        if not name or not url:
            return
        self._add_profile(name, url, [])
        self._save_session()

    def on_wizard(self):
        available = self.working_channels or self.channels
        if not available:
            QMessageBox.information(self, "Нет каналов",
                                    "Сначала загрузите M3U.")
            return
        existing = {name: list(p["channels"])
                    for name, p in self.profiles.items()}
        wiz = MptsWizard(self, existing, preset_channels=available)
        wiz.exec()
        if wiz.created_profiles:
            for name, url, channels in wiz.created_profiles:
                self._add_profile(name, url, channels)
            self._hint(
                f"Мастер создал {len(wiz.created_profiles)} поток(ов)."
            )
            self._save_session()

    def on_hardware_analysis(self):
        available = self.working_channels or self.channels
        if not available:
            QMessageBox.warning(self, "Нет плейлиста",
                                "Сначала загрузите M3U.")
            return
        dlg = HardwareAnalysisDialog(self, available)
        if dlg.exec() != QDialog.Accepted:
            return
        if not dlg.created_profiles:
            return
        for name, url, channels in dlg.created_profiles:
            self._add_profile(name, url, channels)
        self._save_session()
        total = sum(len(c) for _, _, c in dlg.created_profiles)
        self._hint(
            f"Создано {len(dlg.created_profiles)} профил(ей), "
            f"каналов: {total}. Нажмите ▶."
        )

    def _add_profile(self, name, url, channels):
        if name in self.profiles:
            QMessageBox.warning(self, "Имя занято",
                                f"«{name}» уже существует.")
            return

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(url))
        self.table.setItem(row, 2, QTableWidgetItem(str(len(channels))))
        self.table.setItem(row, self.STATUS_COL,
                           QTableWidgetItem("Остановлен"))
        self.table.setItem(row, self.FPS_COL, QTableWidgetItem("-"))
        self.table.setItem(row, self.BITRATE_COL, QTableWidgetItem("-"))

        (play_btn, stop_btn, info_btn, edit_btn,
         copy_btn, check_btn, mosaic_btn,
         delete_btn) = self._create_action_cell(row, name)

        self.profiles[name] = {
            "url": url,
            "channels": list(channels),
            "process": None,
            "row": row,
            "play_btn": play_btn,
            "stop_btn": stop_btn,
            "info_btn": info_btn,
            "edit_btn": edit_btn,
            "copy_btn": copy_btn,
            "check_btn": check_btn,
            "mosaic_btn": mosaic_btn,
            "delete_btn": delete_btn,
            "last_status": "Остановлен",
            "last_error": None,
            "last_return_code": None,
            "last_log_tail": "(журнал пуст)",
            "started_at": None,
            "last_fps": "-",
            "last_bitrate": "-",
        }

    def _create_action_cell(self, row, name):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(3)

        def mk(symbol, tip, handler, enabled=True):
            b = QPushButton(symbol)
            b.setFixedSize(28, 26)
            b.setToolTip(tip)
            b.setEnabled(enabled)
            b.clicked.connect(handler)
            h.addWidget(b)
            return b

        play_btn = mk("▶", f"Запустить «{name}»",
                      lambda _=False, n=name: self._start_mpts(n))
        stop_btn = mk("■", f"Остановить «{name}»",
                      lambda _=False, n=name: self._stop_mpts(n),
                      enabled=False)
        info_btn = mk("ℹ", f"Подробности «{name}»",
                      lambda _=False, n=name: self._show_stream_info(n))
        edit_btn = mk("✎", f"Правка каналов «{name}»",
                      lambda _=False, n=name:
                      self._remove_channels_from_profile(n))
        copy_btn = mk("📋", f"Копировать URL «{name}»",
                      lambda _=False, n=name: self._copy_output_url(n))
        check_btn = mk("🔍", f"Проверить ffprobe «{name}»",
                       lambda _=False, n=name: self._check_mpts_stream(n))
        mosaic_btn = mk("🎬", f"Мозаика «{name}»",
                        lambda _=False, n=name: self._open_mosaic(n))
        delete_btn = mk("✖", f"Удалить «{name}»",
                        lambda _=False, n=name:
                        self._delete_profile_with_confirm(n))

        h.addStretch(1)
        self.table.setCellWidget(row, self.ACTION_COL, w)
        return (play_btn, stop_btn, info_btn, edit_btn,
                copy_btn, check_btn, mosaic_btn, delete_btn)

    def _on_mpts_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.current_mpts = None
            return
        item = self.table.item(rows[0].row(), 0)
        self.current_mpts = item.text() if item else None
        if self.current_mpts:
            self._sync_tree_with_profile(self.current_mpts)
            n = len(self.profiles[self.current_mpts]["channels"])
            self._hint(
                f"Выбран «{self.current_mpts}» ({n} каналов). "
                f"🎬 — мозаика, 📋 — URL, 🔍 — проверка."
            )

    def on_start_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._hint("Сначала выделите строку MPTS")
            return
        for r in rows:
            self._start_mpts(self.table.item(r.row(), 0).text())

    def on_stop_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._hint("Сначала выделите строку MPTS")
            return
        for r in rows:
            self._stop_mpts(self.table.item(r.row(), 0).text())

    def on_mosaic_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._hint("Сначала выделите строку MPTS")
            return
        if len(rows) > 1:
            self._hint("Выберите один профиль для мозаики")
            return
        name = self.table.item(rows[0].row(), 0).text()
        self._open_mosaic(name)

    # ---------------- мозаика ------------------------------------------
    def _open_mosaic(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        if not prof["channels"]:
            QMessageBox.warning(
                self, "Нет каналов",
                f"В MPTS «{name}» нет ни одного канала."
            )
            return

        if not VLC_AVAILABLE:
            QMessageBox.critical(
                self, "VLC не загружен",
                "Мозаика требует portable VLC.\n\n"
                "Скачайте vlc-3.0.x-win64.7z с videolan.org, "
                "распакуйте и положите в папку «vlc_lib»:\n"
                "  • libvlc.dll\n"
                "  • libvlccore.dll\n"
                "  • plugins/ (папка целиком)\n\n"
                "Перезапустите программу."
            )
            return

        if prof["last_status"] != "Работает":
            res = QMessageBox.question(
                self, "Поток не запущен",
                f"«{name}» сейчас не работает, мозаика покажет "
                f"чёрные экраны.\n\nЗапустить поток перед открытием?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if res == QMessageBox.Yes:
                self._start_mpts(name)
                QTimer.singleShot(
                    3000, lambda: self._do_open_mosaic(name)
                )
                return
        self._do_open_mosaic(name)

    def _do_open_mosaic(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        try:
            from mosaic import MosaicWindow
        except ImportError as e:
            QMessageBox.critical(
                self, "Модуль мозаики не найден",
                f"Не удалось импортировать mosaic.py:\n{e}"
            )
            return
        try:
            dlg = MosaicWindow(
                self, name, prof["url"], prof["channels"]
            )
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка мозаики",
                f"Не удалось открыть мозаику:\n{e}"
            )

    # ---------------- URL / контекстное меню ---------------------------
    def _copy_output_url(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        if _copy_to_clipboard(prof["url"]):
            self._hint(f"URL «{name}»: {prof['url']}")

    def _on_table_context_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        name_item = self.table.item(idx.row(), 0)
        if not name_item:
            return
        name = name_item.text()
        prof = self.profiles.get(name)
        if not prof:
            return

        is_running = (prof["process"] and prof["process"].process
                      and prof["process"].process.poll() is None)

        menu = QMenu(self.table)
        act_url = menu.addAction("📋 Копировать URL")
        act_url.triggered.connect(lambda: self._copy_output_url(name))

        menu.addSeparator()
        act_mosaic = menu.addAction("🎬 Открыть мозаику")
        act_mosaic.triggered.connect(lambda: self._open_mosaic(name))

        act_check = menu.addAction("🔍 Проверить (ffprobe)")
        act_check.triggered.connect(lambda: self._check_mpts_stream(name))

        act_info = menu.addAction("ℹ Подробности")
        act_info.triggered.connect(lambda: self._show_stream_info(name))

        act_edit = menu.addAction("✎ Правка каналов")
        act_edit.setEnabled(not is_running)
        act_edit.triggered.connect(
            lambda: self._remove_channels_from_profile(name)
        )

        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ---------------- удаление профиля ---------------------------------
    def _delete_profile_with_confirm(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        if prof["process"] and prof["process"].process \
                and prof["process"].process.poll() is None:
            QMessageBox.warning(
                self, "Поток работает",
                f"«{name}» запущен. Остановите кнопкой ■, затем удалите."
            )
            return
        n_ch = len(prof["channels"])
        ch_note = (f"Каналы ({n_ch} шт.) останутся в плейлисте."
                   if n_ch else "Профиль пуст.")
        res = QMessageBox.question(
            self, "Удалить MPTS",
            f"Удалить «{name}»?\n\n{ch_note}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if res == QMessageBox.Yes:
            self._remove_profile(name)

    def _remove_profile(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        row = prof["row"]
        if prof["process"] and prof["process"].process \
                and prof["process"].process.poll() is None:
            prof["process"].stop()
        self.table.removeRow(row)
        del self.profiles[name]
        for _, p in self.profiles.items():
            if p["row"] > row:
                p["row"] -= 1
        if self.current_mpts == name:
            self.current_mpts = None
            self._updating_tree = True
            try:
                for i in range(self.tree.topLevelItemCount()):
                    g = self.tree.topLevelItem(i)
                    for j in range(g.childCount()):
                        g.child(j).setCheckState(0, Qt.Unchecked)
            finally:
                self._updating_tree = False
        self._hint(f"MPTS «{name}» удалён")
        self._save_session()

    # ---------------- правка каналов -----------------------------------
    def _remove_channels_from_profile(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        if not prof["channels"]:
            QMessageBox.information(
                self, "Нет каналов",
                f"В MPTS «{name}» нет каналов."
            )
            return
        if prof["process"] and prof["process"].process \
                and prof["process"].process.poll() is None:
            QMessageBox.warning(
                self, "Поток работает",
                f"«{name}» запущен. Остановите кнопкой ■."
            )
            return

        dlg = RemoveChannelsDialog(name, prof["channels"], self)
        if dlg.exec() != QDialog.Accepted:
            return
        to_remove = dlg.selected_channels()
        if not to_remove:
            return

        removed_urls = {c.url for c in to_remove}
        prof["channels"] = [c for c in prof["channels"]
                            if c.url not in removed_urls]
        removed_count = len(to_remove)
        remaining = len(prof["channels"])
        self.table.item(prof["row"], 2).setText(str(remaining))

        self._updating_tree = True
        try:
            for i in range(self.tree.topLevelItemCount()):
                g = self.tree.topLevelItem(i)
                for j in range(g.childCount()):
                    ci = g.child(j)
                    ch = ci.data(0, Qt.UserRole)
                    if ch is not None and ch.url in removed_urls:
                        ci.setCheckState(0, Qt.Unchecked)
        finally:
            self._updating_tree = False

        self._save_session()

        if remaining == 0:
            res = QMessageBox.question(
                self, "Все каналы удалены",
                f"Из «{name}» удалены все каналы. Удалить сам профиль?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if res == QMessageBox.Yes:
                self._remove_profile(name)
            return

        current_urls = {c.url for c in prof["channels"]}
        available = [c for c in self.channels
                     if c.url not in current_urls]
        if removed_count > 0 and available:
            res = QMessageBox.question(
                self, "Заменить?",
                f"Удалено {removed_count}. Заменить из плейлиста "
                f"(доступно {len(available)})?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if res == QMessageBox.Yes:
                dlg2 = ReplaceChannelsDialog(name, available, self)
                if dlg2.exec() == QDialog.Accepted:
                    added = dlg2.selected_channels()
                    if added:
                        prof["channels"].extend(added)
                        self.table.item(prof["row"], 2).setText(
                            str(len(prof["channels"]))
                        )
                        self._sync_tree_with_profile(name)
                        self._save_session()

    # ---------------- проверка ffprobe ---------------------------------
    def _check_mpts_stream(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        url = prof["url"]
        ffprobe_path = find_tool("ffprobe")
        if not ffprobe_path:
            QMessageBox.warning(
                self, "ffprobe не найден",
                "Положите ffprobe.exe рядом с программой."
            )
            return

        self._hint(f"Проверка «{name}» через ffprobe…")
        worker = _CheckWorker(url, ffprobe_path, timeout_s=10, parent=self)
        worker.done.connect(
            lambda progs, err, n=name, u=url:
            self._on_check_done(n, u, progs, err)
        )
        worker.finished.connect(worker.deleteLater)
        self._check_workers.append(worker)
        worker.start()

    def _on_check_done(self, name, url, programs, error):
        self._check_workers = self._clean_workers(self._check_workers)
        if error:
            QMessageBox.warning(
                self, "Ошибка проверки",
                f"Не удалось проверить поток «{name}»:\n\n{error}"
            )
            return
        prof = self.profiles.get(name)
        if not prof:
            return
        ffprobe_path = find_tool("ffprobe") or ""
        dlg = StreamVisualCheckDialog(
            self, name, url, prof["channels"], programs, ffprobe_path
        )
        dlg.exec()

    @staticmethod
    def _clean_workers(workers: list) -> list:
        alive = []
        for w in workers:
            try:
                if w.isRunning():
                    alive.append(w)
            except RuntimeError:
                pass
        return alive

    # ---------------- запуск / остановка --------------------------------
    def _start_mpts(self, name: str):
        prof = self.profiles.get(name)
        if not prof:
            return
        if not prof["channels"]:
            QMessageBox.warning(self, "Нет каналов",
                                f"В «{name}» нет каналов.")
            return
        if prof["process"] and prof["process"].process \
                and prof["process"].process.poll() is None:
            self._hint(f"«{name}» уже запущен")
            return

        ffmpeg_path = find_tool("ffmpeg")
        if not ffmpeg_path:
            QMessageBox.critical(
                self, "ffmpeg.exe не найден",
                "Положите ffmpeg.exe рядом с программой."
            )
            return

        prof["last_error"] = None
        prof["last_return_code"] = None
        prof["started_at"] = None
        self.table.item(prof["row"], self.FPS_COL).setText("-")
        self.table.item(prof["row"], self.BITRATE_COL).setText("-")
        prof["last_fps"] = "-"
        prof["last_bitrate"] = "-"

        status_item = self.table.item(prof["row"], self.STATUS_COL)
        status_item.setText("Проверка каналов…")
        status_item.setBackground(QBrush(QColor("#f0f0f0")))
        status_item.setForeground(QBrush())
        status_item.setToolTip("Идёт pre-flight")

        self._hint(f"Проверка {len(prof['channels'])} каналов для «{name}»…")
        worker = _ProbeWorker(prof["channels"], ffmpeg_path,
                              timeout_s=5, parent=self)
        worker.done.connect(
            lambda results, n=name: self._apply_probe_results(n, results)
        )
        worker.finished.connect(worker.deleteLater)
        self._probe_workers.append(worker)
        worker.start()

    def _apply_probe_results(self, name, results):
        prof = self.profiles.get(name)
        if not prof:
            return
        self._probe_workers = self._clean_workers(self._probe_workers)

        ok_channels = [ch for ch, ok, _ in results if ok]
        failed = [(ch, err) for ch, ok, err in results if not ok]

        status_item = self.table.item(prof["row"], self.STATUS_COL)
        if not ok_channels:
            status_item.setText("Ошибка: все каналы недоступны")
            status_item.setBackground(QBrush(QColor("#ffe1e1")))
            status_item.setForeground(QBrush(QColor("#8b0000")))
            details = "\n".join(f"• {ch.name}: {err}"
                                for ch, err in failed)
            QMessageBox.critical(
                self, "Все каналы недоступны",
                f"Ни один канал не открылся:\n\n{details}"
            )
            return

        if failed:
            lines = "\n".join(f"• {ch.name}: {err}"
                              for ch, err in failed)
            res = QMessageBox.question(
                self, "Часть каналов недоступна",
                f"Не открылось {len(failed)} из "
                f"{len(prof['channels'])}:\n\n{lines}\n\n"
                f"Запустить с {len(ok_channels)} рабочими?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if res != QMessageBox.Yes:
                status_item.setText("Остановлен")
                status_item.setBackground(QBrush())
                status_item.setForeground(QBrush())
                return
            prof["channels"] = ok_channels
            self.table.item(prof["row"], 2).setText(
                str(len(ok_channels))
            )
            removed_urls = {c.url for c, _ in failed}
            self._updating_tree = True
            try:
                for i in range(self.tree.topLevelItemCount()):
                    g = self.tree.topLevelItem(i)
                    for j in range(g.childCount()):
                        ci = g.child(j)
                        ch = ci.data(0, Qt.UserRole)
                        if ch is not None and ch.url in removed_urls:
                            ci.setCheckState(0, Qt.Unchecked)
            finally:
                self._updating_tree = False
            self._save_session()

        self._launch_ffmpeg(name)

    def _launch_ffmpeg(self, name):
        prof = self.profiles.get(name)
        if not prof or not prof["channels"]:
            return
        ffmpeg_path = find_tool("ffmpeg")
        if not ffmpeg_path:
            status_item = self.table.item(prof["row"], self.STATUS_COL)
            status_item.setText("Ошибка: ffmpeg.exe не найден")
            status_item.setBackground(QBrush(QColor("#ffe1e1")))
            status_item.setForeground(QBrush(QColor("#8b0000")))
            return

        prof["started_at"] = datetime.now()
        prof["last_log_tail"] = "(журнал пуст)"
        self.table.item(prof["row"], self.FPS_COL).setText("-")
        self.table.item(prof["row"], self.BITRATE_COL).setText("-")

        status_item = self.table.item(prof["row"], self.STATUS_COL)
        status_item.setText("Запуск…")
        status_item.setBackground(QBrush())
        status_item.setForeground(QBrush())
        status_item.setToolTip("")

        proc = FFmpegProcess(
            name=name,
            urls=[c.url for c in prof["channels"]],
            output_url=prof["url"],
            ffmpeg_path=ffmpeg_path,
        )
        proc.stats_updated.connect(self._on_stats)
        proc.status_changed.connect(self._on_status)
        proc.log_line.connect(self._on_log)
        proc.error_occurred.connect(self._on_error)
        proc.start()
        prof["process"] = proc
        self._hint(f"Запуск «{name}»… URL: {prof['url']}")

    def _stop_mpts(self, name):
        prof = self.profiles.get(name)
        if prof and prof["process"]:
            prof["process"].stop()
            prof["process"] = None
            prof["started_at"] = None
            self._hint(f"Остановлен «{name}»")

   # ---------------- FFmpeg -> UI -------------------------------------
    def _on_stats(self, name, stats):
        prof = self.profiles.get(name)
        if not prof:
            return
        row = prof["row"]
        if "fps" in stats:
            self.table.item(row, self.FPS_COL).setText(stats["fps"])
            prof["last_fps"] = stats["fps"]
        if "bitrate" in stats:
            self.table.item(row, self.BITRATE_COL).setText(stats["bitrate"])
            prof["last_bitrate"] = stats["bitrate"]

    def _on_status(self, name, status):
        prof = self.profiles.get(name)
        if not prof:
            return
        prof["last_status"] = status
        item = self.table.item(prof["row"], self.STATUS_COL)
        item.setText(status)
        is_running = (status == "Работает")

        if is_running:
            item.setBackground(QBrush(QColor("#e6ffe6")))
            item.setForeground(QBrush(QColor("#006600")))
            item.setToolTip(
                f"Поток работает.\nURL: {prof['url']}\n"
                f"🎬 — мозаика, 📋 — URL."
            )
            prof["last_error"] = None
        else:
            self.table.item(prof["row"], self.FPS_COL).setText("-")
            self.table.item(prof["row"], self.BITRATE_COL).setText("-")
            prof["last_fps"] = "-"
            prof["last_bitrate"] = "-"
            if status == "Ошибка":
                pass
            elif status == "Завершён":
                item.setBackground(QBrush(QColor("#fff4d6")))
                item.setForeground(QBrush(QColor("#7a5d00")))
                prof["started_at"] = None
            else:
                item.setBackground(QBrush())
                item.setForeground(QBrush())
                prof["started_at"] = None

        for k in ("play_btn", "stop_btn", "edit_btn", "delete_btn"):
            b = prof.get(k)
            if b is None:
                continue
            if k == "play_btn":
                b.setEnabled(not is_running)
            elif k == "stop_btn":
                b.setEnabled(is_running)
            else:
                b.setEnabled(not is_running)

    def _on_error(self, name, short_reason, full_log, return_code):
        prof = self.profiles.get(name)
        if not prof:
            return
        prof["last_error"] = (short_reason, full_log)
        prof["last_return_code"] = return_code
        prof["last_log_tail"] = (full_log[-4000:]
                                 if full_log else "(журнал пуст)")
        prof["started_at"] = None

        item = self.table.item(prof["row"], self.STATUS_COL)
        short_display = (short_reason if len(short_reason) <= 70
                         else short_reason[:67] + "…")
        item.setText(f"Ошибка: {short_display}")
        item.setBackground(QBrush(QColor("#ffe1e1")))
        item.setForeground(QBrush(QColor("#8b0000")))
        item.setToolTip(
            f"{short_reason}\n\nКлик — подробности и рекомендации."
        )
        self._hint(f"«{name}»: {short_reason}")

    def _on_log(self, name, line):
        prof = self.profiles.get(name)
        if prof:
            tail = prof.get("_log_tail_list", [])
            tail.append(line)
            if len(tail) > 200:
                tail = tail[-200:]
            prof["_log_tail_list"] = tail
            prof["last_log_tail"] = "\n".join(tail)
        if line.startswith("CMD:"):
            return
        self.log.setText(f"[{name}] {line[:140]}")

    # ---------------- клики по таблице ---------------------------------
    def _on_cell_clicked(self, row, col):
        if col != self.STATUS_COL:
            return
        item = self.table.item(row, 0)
        if item:
            self._show_stream_info(item.text())

    def _on_cell_dblclick(self, row, col):
        if col == self.STATUS_COL:
            item = self.table.item(row, 0)
            if item:
                self._show_stream_info(item.text())

    def _show_stream_info(self, name):
        prof = self.profiles.get(name)
        if not prof:
            return

        def restart():
            self._start_mpts(name)

        dlg = StreamInfoDialog(self, name, prof, restart)
        dlg.exec()

    # ---------------- system monitor -----------------------------------
    def _on_system(self, data):
        self.footer.update_metrics(data)

    # ---------------- завершение ---------------------------------------
    def closeEvent(self, event):
        self._save_session()
        self.sys_monitor.stop()
        shutdown_nvml()
        for prof in self.profiles.values():
            if prof["process"]:
                prof["process"].stop()
        super().closeEvent(event)