# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import math
from dataclasses import dataclass, field

import psutil


@dataclass
class HardwareProfile:
    cpu_cores: int = 0
    cpu_logical: int = 0
    cpu_load: float = 0.0
    ram_total_mb: int = 0
    ram_used_mb: int = 0
    ram_load: float = 0.0
    net_interfaces: list[dict] = field(default_factory=list)
    max_net_mbps: float = 0.0


@dataclass
class Recommendation:
    total_channels: int = 0
    max_channels_by_cpu: int = 0
    max_channels_by_ram: int = 0
    max_channels_by_net: int = 0
    max_channels_overall: int = 0
    channels_per_mpts: int = 0
    num_mpts: int = 0
    warnings: list[str] = field(default_factory=list)
    avg_channel_mbps: float = 4.0
    estimated_out_mbps: float = 0.0
    estimated_cpu_load: float = 0.0
    estimated_ram_mb: int = 0


# Эмпирические константы (HLS → MPTS -c copy, SD 720×576)
CPU_PERCENT_PER_CHANNEL = 1.8
RAM_MB_PER_CHANNEL = 80
DEFAULT_CHANNEL_MBPS = 4.0
CPU_SAFE_LOAD = 0.70
RAM_SAFE_LOAD = 0.50
NET_SAFE_LOAD = 0.80
MAX_CHANNELS_PER_MPTS = 10
VIRTUAL_KEYWORDS = ("virtual", "vethernet", "vmware", "virtualbox",
                    "hyper-v", "loopback", "vpn", "tap", "tun",
                    "wireguard", "wsl", "bluetooth", "pseudo")


def gather_hardware() -> HardwareProfile:
    hw = HardwareProfile()
    hw.cpu_cores = psutil.cpu_count(logical=False) or 1
    hw.cpu_logical = psutil.cpu_count(logical=True) or 1
    hw.cpu_load = psutil.cpu_percent(interval=0.4)

    mem = psutil.virtual_memory()
    hw.ram_total_mb = int(mem.total / (1024 ** 2))
    hw.ram_used_mb = int((mem.total - mem.available) / (1024 ** 2))
    hw.ram_load = mem.percent

    for name, st in psutil.net_if_stats().items():
        if not st.isup or st.speed <= 0:
            continue
        low = name.lower()
        if any(k in low for k in VIRTUAL_KEYWORDS):
            continue
        hw.net_interfaces.append({
            "name": name,
            "speed_mbps": st.speed,
            "is_up": st.isup,
        })
        hw.max_net_mbps = max(hw.max_net_mbps, st.speed)

    return hw


def analyze(hw: HardwareProfile, num_channels: int,
            avg_channel_mbps: float = DEFAULT_CHANNEL_MBPS) -> Recommendation:
    rec = Recommendation()
    rec.total_channels = num_channels
    rec.avg_channel_mbps = avg_channel_mbps
    if num_channels <= 0:
        return rec

    cpu_capacity = hw.cpu_logical * CPU_SAFE_LOAD * 100
    rec.max_channels_by_cpu = max(1, int(cpu_capacity / CPU_PERCENT_PER_CHANNEL))

    ram_capacity = hw.ram_total_mb * RAM_SAFE_LOAD
    rec.max_channels_by_ram = max(1, int(ram_capacity / RAM_MB_PER_CHANNEL))

    if hw.max_net_mbps > 0:
        net_capacity = hw.max_net_mbps * NET_SAFE_LOAD
        rec.max_channels_by_net = max(1, int(net_capacity / avg_channel_mbps))
    else:
        rec.max_channels_by_net = 999

    rec.max_channels_overall = min(
        rec.max_channels_by_cpu,
        rec.max_channels_by_ram,
        rec.max_channels_by_net,
    )

    if num_channels <= MAX_CHANNELS_PER_MPTS:
        rec.num_mpts = 1
        rec.channels_per_mpts = num_channels
    else:
        rec.num_mpts = math.ceil(num_channels / MAX_CHANNELS_PER_MPTS)
        rec.channels_per_mpts = math.ceil(num_channels / rec.num_mpts)

    rec.estimated_cpu_load = (num_channels * CPU_PERCENT_PER_CHANNEL) / \
                             (hw.cpu_logical * 100) * 100
    rec.estimated_ram_mb = num_channels * RAM_MB_PER_CHANNEL
    rec.estimated_out_mbps = num_channels * avg_channel_mbps

    if num_channels > rec.max_channels_by_cpu:
        rec.warnings.append(
            f"Каналов больше, чем потянет CPU ({rec.max_channels_by_cpu}). "
            f"Ожидайте >{CPU_SAFE_LOAD * 100:.0f}% загрузки процессора."
        )
    if num_channels > rec.max_channels_by_ram:
        rec.warnings.append(
            f"Каналов больше, чем потянет RAM ({rec.max_channels_by_ram}). "
            f"Может не хватить памяти."
        )
    if num_channels > rec.max_channels_by_net:
        rec.warnings.append(
            f"Каналов больше, чем потянет сеть ({rec.max_channels_by_net}). "
            f"Нужен более быстрый NIC или снижение битрейта."
        )

    return rec