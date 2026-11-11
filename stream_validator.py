# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import json
import subprocess
from dataclasses import dataclass, field
from ffmpeg_manager import find_tool

@dataclass
class StreamInfo:
    """Информация об одном элементарном потоке (видео/аудио) внутри программы."""
    codec_type: str = ""
    codec_name: str = ""
    width: int = 0
    height: int = 0
    sample_rate: str = ""
    channels: int = 0


@dataclass
class ProgramInfo:
    """Информация об одной программе (канале) внутри MPTS."""
    program_id: int = 0
    program_num: int = 0
    service_name: str = ""
    service_provider: str = ""
    pmt_pid: str = ""
    pcr_pid: str = ""
    streams: list[StreamInfo] = field(default_factory=list)
    error: str = ""


def probe_mpts(url: str, ffprobe_path: str | None = None,
               timeout_s: int = 10) -> tuple[list[ProgramInfo], str]:
    """..."""
    if ffprobe_path is None:
        ffprobe_path = find_tool("ffprobe") or "ffprobe"
    """
    Анализирует MPTS по указанному URL с помощью ffprobe.
    Возвращает (список программ, общая ошибка).
    """
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_programs",
        "-show_streams",
        "-of", "json",
        "-timeout", str(timeout_s * 1_000_000),
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s + 5,
            text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return [], f"ffprobe не ответил за {timeout_s + 5} секунд."
    except FileNotFoundError:
        return [], "ffprobe.exe не найден. Убедитесь, что он лежит рядом с ffmpeg.exe."
    except Exception as e:
        return [], f"Ошибка запуска ffprobe: {e}"

    if result.returncode != 0 and not result.stdout:
        stderr = (result.stderr or "").strip()
        return [], f"ffprobe завершился с ошибкой (код {result.returncode}):\n{stderr[:300]}"

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return [], f"Не удалось разобрать JSON от ffprobe: {e}"

    programs: list[ProgramInfo] = []
    for p in data.get("programs", []):
        # Программа без streams — обычно означает битый или неполный поток
        if not p.get("streams"):
            prog = ProgramInfo(
                program_id=p.get("program_id", 0),
                program_num=p.get("program_num", 0),
                service_name=p.get("tags", {}).get("service_name", "—"),
                service_provider=p.get("tags", {}).get("service_provider", "—"),
                error="Программа не содержит потоков (возможно, повреждена)",
            )
            programs.append(prog)
            continue

        prog = ProgramInfo(
            program_id=p.get("program_id", 0),
            program_num=p.get("program_num", 0),
            service_name=p.get("tags", {}).get("service_name", "—"),
            service_provider=p.get("tags", {}).get("service_provider", "—"),
            pmt_pid=p.get("pmt_pid", ""),
            pcr_pid=p.get("pcr_pid", ""),
        )
        for s in p.get("streams", []):
            prog.streams.append(StreamInfo(
                codec_type=s.get("codec_type", ""),
                codec_name=s.get("codec_name", ""),
                width=s.get("width", 0),
                height=s.get("height", 0),
                sample_rate=s.get("sample_rate", ""),
                channels=s.get("channels", 0),
            ))
        programs.append(prog)

    if not programs:
        return [], "ffprobe не нашёл ни одной программы в потоке."

    return programs, ""