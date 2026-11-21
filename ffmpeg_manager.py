# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import logging
import os
import shutil
import sys
import re
import subprocess
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("mpts.ffmpeg")



def app_dir() -> str:
    """Папка приложения: рядом с main.exe (frozen) или с main.py (исходники)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _search_dirs() -> list[str]:
    """Каталоги, где могут лежать утилиты (включая _internal PyInstaller 6)."""
    dirs = [app_dir(), os.path.join(app_dir(), "_internal")]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        dirs.append(meipass)
    result: list[str] = []
    for d in dirs:
        if d and d not in result:
            result.append(d)
    return result


def find_tool(name: str) -> str | None:
    """
    Ищет утилиту рядом с приложением, в _internal, _MEIPASS и PATH.
    Возвращает полный путь или None.
    """
    candidates = [name + ".exe", name] if os.name == "nt" else [name]
    for base in _search_dirs():
        for fname in candidates:
            p = os.path.join(base, fname)
            if os.path.isfile(p):
                return p
    return shutil.which(name)


def redact_url(url: str) -> str:
    """Маскирует логин/пароль в URL для журналов и ошибок."""
    return re.sub(r"(://[^/@:]+:)[^/@]+(@)", r"\1***\2", url)


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
    logger.debug("probe: %s", " ".join(redact_url(x) for x in cmd))
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

    if result.returncode == 0:
        return True, ""

    # Ненулевой код нельзя считать успехом, даже если stderr пуст.
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
    if any(sig.lower() in stderr.lower() for sig in failure_signals) or stderr.strip():
        for pattern, explanation in ERROR_PATTERNS:
            if re.search(pattern, stderr, re.IGNORECASE):
                return False, explanation
        for line in stderr.splitlines():
            line = line.strip()
            if line and not line.startswith("["):
                return False, line[:160]
        if stderr.strip():
            return False, "Не удалось открыть поток."
    return False, explain_return_code(result.returncode)


class FFmpegProcess(QObject):
    stats_updated = Signal(str, dict)           # (name, {fps, bitrate, ...})
    status_changed = Signal(str, str)           # (name, "Работает"/"Остановлен"/...)
    log_line = Signal(str, str)                 # (name, строка журнала)
    # qint64 — чтобы NTSTATUS-коды вроде 0xDFC37EBB (3.75 млрд) помещались в int.
    error_occurred = Signal(str, str, str, "qint64")  # (name, причина, журнал, код)

    def __init__(self, name: str, urls: list[str], output_url: str,
                 ffmpeg_path: str = "ffmpeg", ffprobe_path: str | None = None,
                 parent=None):
        super().__init__(parent)
        self.name = name
        self.urls = urls
        self.output_url = output_url
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path or find_tool("ffprobe") or "ffprobe"
        self._stream_types: list[set[str]] | None = None
        self.process: subprocess.Popen | None = None
        self._stopping = False
        self._stderr_buffer: deque[str] = deque(maxlen=200)
        self.last_return_code: int | None = None

    def build_command(self, stream_types: list[set[str]] | None = None) -> list[str]:
        cmd = [
            self.ffmpeg_path,
            "-hide_banner", "-nostdin",
            "-loglevel", "error",
            "-progress", "pipe:1",
            "-fflags", "+genpts+igndts",
            "-avoid_negative_ts", "make_zero",
        ]

        for url in self.urls:
            if url.startswith(("http://", "https://")):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
                        "-reconnect_delay_max", "5"]
            cmd += ["-i", url]

        # st=... задаёт индексы потоков внутри конкретной программы,
        # а не глобальные индексы выхода. Видео и аудио опциональны.
        if stream_types is None:
            stream_types = self._probe_stream_types()
        output_index = 0
        for i, url in enumerate(self.urls):
            types = stream_types[i] if i < len(stream_types) else {"video", "audio"}
            maps: list[str] = []
            stream_indexes: list[str] = []
            if "video" in types:
                maps += [f"{i}:v?"]
                stream_indexes.append(str(output_index))
                output_index += 1
            if "audio" in types:
                maps += [f"{i}:a?"]
                stream_indexes.append(str(output_index))
                output_index += 1
            if not maps:
                maps = [f"{i}:v?"]
                stream_indexes = [str(output_index)]
                output_index += 1
            cmd.append("-program")
            cmd.append(f"title=Channel_{i + 1}:" +
                       ":".join(f"st={idx}" for idx in stream_indexes))
            for m in maps:
                cmd += ["-map", m]

        cmd += ["-c", "copy"]
        # dump_extra несовместим с несколькими -program (ломает muxing);
        # resend_headers достаточно для MPTS-клиентов.
        cmd += ["-mpegts_flags", "+resend_headers+initial_discontinuity"]
        cmd += ["-muxdelay", "0.1", "-muxpreload", "0.1"]
        cmd += ["-f", "mpegts", self.output_url]

        return cmd

    def _probe_stream_types(self) -> list[set[str]]:
        """Определяет наличие видео/аудио, чтобы корректно собрать программы."""
        if self._stream_types is not None:
            return self._stream_types
        def probe_one(url: str) -> set[str]:
            try:
                proc = subprocess.run(
                    [self.ffprobe_path, "-v", "error",
                     "-show_entries", "stream=codec_type", "-of", "csv=p=0", url],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=5,
                    text=True, encoding="utf-8", errors="replace",
                )
                return {line.strip().lower() for line in proc.stdout.splitlines()
                        if line.strip()}
            except Exception as exc:
                logger.warning("ffprobe не смог определить потоки %s: %s",
                               redact_url(url), exc)
                return set()

        with ThreadPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(probe_one, self.urls))
        result = [t or {"video", "audio"} for t in result]
        self._stream_types = result
        return result

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self._stderr_buffer.clear()
        self.last_return_code = None

        cmd = self.build_command(self._probe_stream_types())
        display_cmd = " ".join(redact_url(x) for x in cmd)
        self.log_line.emit(self.name, "CMD: " + display_cmd)
        logger.info("CMD [%s]: %s", self.name, display_cmd)
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
        logger.info("Запущен FFmpeg [%s], PID=%s", self.name, proc.pid)
        self.status_changed.emit(self.name, "Работает")

        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
        threading.Thread(target=self._wait_exit,  args=(proc,), daemon=True).start()

    def stop(self):
        proc = self.process
        if not proc:
            return
        self._stopping = True
        self.process = None
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass
        logger.info("Запрошена остановка FFmpeg [%s]", self.name)
        self.status_changed.emit(self.name, "Остановлен")

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
                logger.warning("FFmpeg [%s]: %s", self.name, line)
                self.log_line.emit(self.name, line)
        except Exception:
            pass

    def _wait_exit(self, proc):
        rc = proc.wait()
        # Старый процесс после stop()+start() не должен перезаписывать статус нового.
        if self.process is not proc:
            return
        self.last_return_code = rc
        if self._stopping:
            logger.info("FFmpeg [%s] остановлен", self.name)
            self.status_changed.emit(self.name, "Остановлен")
        elif rc == 0:
            logger.info("FFmpeg [%s] завершён с кодом 0", self.name)
            self.status_changed.emit(self.name, "Завершён")
        else:
            short = explain_error(list(self._stderr_buffer), rc)
            logger.error("FFmpeg [%s] код %s: %s", self.name, rc, short)
            full = "\n".join(self._stderr_buffer) or f"(пусто, код возврата {rc})"
            self.status_changed.emit(self.name, "Ошибка")
            self.error_occurred.emit(self.name, short, full, rc)