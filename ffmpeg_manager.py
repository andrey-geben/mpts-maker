# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import os
import shutil
import sys
import re
import subprocess
import threading
from collections import deque

from PySide6.QtCore import QObject, Signal



def app_dir() -> str:
    """Папка приложения: рядом с main.exe (frozen) или с main.py (исходники)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_tool(name: str) -> str | None:
    """
    Ищет утилиту в такой последовательности:
      1. рядом с приложением (ffmpeg.exe / ffmpeg)
      2. в PATH
    Возвращает полный путь или None.
    """
    base = app_dir()
    candidates = [name + ".exe", name] if os.name == "nt" else [name]
    for fname in candidates:
        p = os.path.join(base, fname)
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def check_required_tools() -> tuple[bool, list[str]]:
    """Проверяет наличие ffmpeg и ffprobe. Возвращает (ok, список отсутствующих)."""
    missing: list[str] = []
    for tool in ("ffmpeg", "ffprobe"):
        if find_tool(tool) is None:
            missing.append(tool + ".exe")
    return (len(missing) == 0, missing)

# Типовые ошибки FFmpeg → человеческое объяснение на русском.
# Порядок важен: первое совпадение выигрывает.
ERROR_PATTERNS: list[tuple[str, str]] = [
    (r"Server returned 404",
     "Источник не найден (HTTP 404). Проверьте URL канала в плейлисте."),
    (r"Server returned 401",
     "Требуется авторизация (HTTP 401). Возможно, нужен логин/токен."),
    (r"Server returned 403",
     "Доступ к источнику запрещён (HTTP 403)."),
    (r"Server returned 5\d\d",
     "Сервер источника вернул ошибку (HTTP 5xx). Попробуйте позже."),
    (r"Connection refused",
     "Соединение отклонено — сервер источника недоступен."),
    (r"Connection timed out|Operation timed out|Timeout",
     "Превышено время ожидания — источник не отвечает."),
    (r"Name or service not known|nodename nor servname|getaddrinfo",
     "Не удалось разрешить имя хоста (DNS). Проверьте URL."),
    (r"Network is unreachable|No route to host",
     "Сеть недоступна или нет маршрута к хосту источника."),
    (r"Invalid data found when processing input",
     "Источник вернул некорректные данные — возможно, это не медиапоток. "
     "Проверьте URL в VLC."),
    (r"Protocol not found",
     "Протокол источника не поддерживается FFmpeg."),
    (r"Unable to find a suitable output format",
     "Некорректный URL вывода. Пример: udp://239.0.0.1:1234?pkt_size=1316"),
    (r"Address already in use",
     "Адрес вывода уже занят другим процессом. Смените порт в настройках MPTS."),
    (r"Cannot allocate memory",
     "Недостаточно памяти для запуска FFmpeg."),
    (r"Permission denied",
     "Доступ запрещён (права на ffmpeg.exe или сеть)."),
    (r"No such file or directory",
     "Файл или ресурс не найден (проверьте путь к ffmpeg.exe)."),
    (r"Error opening input.*End of file|End of file",
     "Источник закрыл соединение, не отдав данные (End of file). "
     "Проверьте доступность URL и работу сервера."),
    (r"Error opening input",
     "Не удалось открыть входной поток — источник недоступен или URL устарел."),
    (r"Conversion failed",
     "Ошибка обработки потока FFmpeg."),
    (r"Invalid argument",
     "Некорректный аргумент в команде FFmpeg. Проверьте URL вывода и параметры."),
]


def explain_error(stderr_lines: list[str], return_code: int) -> str:
    """Превращает журнал FFmpeg в короткое объяснение на русском."""
    text = "\n".join(stderr_lines)
    for pattern, explanation in ERROR_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return explanation
    for line in reversed(stderr_lines):
        line = line.strip()
        if line and not line.startswith("[") and "=" not in line[:10]:
            return "Ошибка FFmpeg: " + line[:160]
    return explain_return_code(return_code)


def explain_return_code(code: int) -> str:
    """Расшифровывает код возврата процесса FFmpeg."""
    if code == 0:
        return "Успешное завершение."

    if code == -1:
        return "Не удалось запустить процесс (ffmpeg.exe не найден или ошибка ОС)."

    # Windows NTSTATUS (бит 31 = 1, старший ниббл C/D)
    if code >= 0xC0000000:
        ntstatus = {
            0xC0000005: "Нарушение доступа к памяти (access violation). "
                        "FFmpeg аварийно завершился — вероятно, проблема в "
                        "драйвере или в самом ffmpeg.exe.",
            0xC0000135: "Не найдена DLL-зависимость.",
            0xC0000142: "Ошибка инициализации DLL.",
            0xC0000409: "Переполнение буфера (security cookie).",
            0xC00000FD: "Переполнение стека.",
            0xC000001D: "Неверная инструкция процессора.",
            0xC0000094: "Целочисленное деление на ноль.",
            0xDFC37EBB: "STATUS_STOWED_EXCEPTION — внутренний сбой FFmpeg. "
                        "Скорее всего, повреждён сам ffmpeg.exe или "
                        "несовместимый драйвер. Замените ffmpeg.exe на "
                        "свежую сборку с https://www.gyan.dev/ffmpeg/builds/",
        }
        if code in ntstatus:
            return f"Windows-код 0x{code:08X} ({code}): {ntstatus[code]}"
        return (f"Windows-код 0x{code:08X} ({code}): системный сбой FFmpeg. "
                f"Попробуйте заменить ffmpeg.exe на свежую сборку с "
                f"https://www.gyan.dev/ffmpeg/builds/")

    common = {
        1: "Общая ошибка FFmpeg (потерян источник, ошибка в URL, "
           "или несовместимый формат входного потока).",
        2: "Ошибка аргументов командной строки.",
        3: "Недостаточно памяти.",
        4: "Внутренняя ошибка FFmpeg.",
    }
    if code in common:
        return f"Код {code}: {common[code]}"
    return f"Код возврата {code} (0x{code & 0xFFFFFFFF:08X})"


def probe_url(url: str, ffmpeg_path: str = "ffmpeg",
              timeout_s: int = 5) -> tuple[bool, str]:
    """
    Быстрая проверка доступности URL через ffmpeg.
    Возвращает (ok, сообщение_об_ошибке).
    ok=True  — поток открывается (прочитано хотя бы 0.1 с).
    ok=False — вторая строка содержит причину на русском.
    """
    cmd = [
        ffmpeg_path,
        "-hide_banner", "-nostdin", "-v", "error",
        "-rw_timeout", str(timeout_s * 1_000_000),
        "-i", url,
        "-t", "0.1", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_s + 3,
            text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"Источник не ответил за {timeout_s} с."
    except FileNotFoundError:
        return False, "ffmpeg.exe не найден."
    except Exception as e:
        return False, f"Ошибка проверки: {e}"

    stderr = result.stderr or ""

    failure_signals = (
        "Error opening input",
        "End of file",
        "Invalid data found",
        "Server returned",
        "Connection refused",
        "No route to host",
        "Name or service not known",
        "Protocol not found",
    )
    if any(sig in stderr for sig in failure_signals):
        for pattern, explanation in ERROR_PATTERNS:
            if re.search(pattern, stderr, re.IGNORECASE):
                return False, explanation
        for line in stderr.splitlines():
            line = line.strip()
            if line and not line.startswith("["):
                return False, line[:160]
        return False, "Не удалось открыть поток."

    return True, ""


class FFmpegProcess(QObject):
    stats_updated = Signal(str, dict)           # (name, {fps, bitrate, ...})
    status_changed = Signal(str, str)           # (name, "Работает"/"Остановлен"/...)
    log_line = Signal(str, str)                 # (name, строка журнала)
    # qint64 — чтобы NTSTATUS-коды вроде 0xDFC37EBB (3.75 млрд) помещались в int.
    error_occurred = Signal(str, str, str, "qint64")  # (name, причина, журнал, код)

    def __init__(self, name: str, urls: list[str], output_url: str,
                 ffmpeg_path: str = "ffmpeg", parent=None):
        super().__init__(parent)
        self.name = name
        self.urls = urls
        self.output_url = output_url
        self.ffmpeg_path = ffmpeg_path
        self.process: subprocess.Popen | None = None
        self._stopping = False
        self._stderr_buffer: deque[str] = deque(maxlen=200)
        self.last_return_code: int | None = None

    def build_command(self) -> list[str]:
        cmd = [
            self.ffmpeg_path,
            "-hide_banner", "-nostdin",
            "-loglevel", "error",
            "-progress", "pipe:1",
        ]
        for url in self.urls:
            if url.startswith(("http://", "https://")):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
                        "-reconnect_delay_max", "5"]
            cmd += ["-i", url]

        for i in range(len(self.urls)):
            cmd += ["-map", f"{i}:v?", "-map", f"{i}:a?"]

        cmd += ["-c", "copy", "-f", "mpegts", self.output_url]
        return cmd

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self._stderr_buffer.clear()
        self.last_return_code = None

        cmd = self.build_command()
        self.log_line.emit(self.name, "CMD: " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            self.status_changed.emit(self.name, "Ошибка")
            self.error_occurred.emit(
                self.name,
                "Файл ffmpeg.exe не найден рядом с программой.",
                f"Не удалось запустить: {self.ffmpeg_path}\n"
                "Проверьте, что ffmpeg.exe лежит рядом с main.exe / main.py.",
                -1,
            )
            return
        except Exception as e:
            self.status_changed.emit(self.name, "Ошибка")
            self.error_occurred.emit(
                self.name, f"Не удалось запустить FFmpeg: {e}", str(e), -1
            )
            return

        self.process = proc
        self._stopping = False
        self.status_changed.emit(self.name, "Работает")

        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
        threading.Thread(target=self._wait_exit,  args=(proc,), daemon=True).start()

    def stop(self):
        if not self.process:
            return
        self._stopping = True
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        except Exception:
            pass
        self.process = None

    def _read_stdout(self, proc):
        stats: dict = {}
        try:
            for line in proc.stdout:
                line = line.strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key == "progress":
                    if stats:
                        self.stats_updated.emit(self.name, dict(stats))
                        stats = {}
                elif key in ("frame", "fps", "bitrate", "speed", "total_size"):
                    stats[key] = value
        except Exception:
            pass

    def _read_stderr(self, proc):
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if not line:
                    continue
                self._stderr_buffer.append(line)
                self.log_line.emit(self.name, line)
        except Exception:
            pass

    def _wait_exit(self, proc):
        rc = proc.wait()
        self.last_return_code = rc
        if self._stopping:
            self.status_changed.emit(self.name, "Остановлен")
        elif rc == 0:
            self.status_changed.emit(self.name, "Завершён")
        else:
            short = explain_error(list(self._stderr_buffer), rc)
            full = "\n".join(self._stderr_buffer) or f"(пусто, код возврата {rc})"
            self.status_changed.emit(self.name, "Ошибка")
            self.error_occurred.emit(self.name, short, full, rc)