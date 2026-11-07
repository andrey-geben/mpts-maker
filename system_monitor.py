# Copyright (c) 2026 Andrey Geben
# SPDX-License-Identifier: MIT

import time
import psutil
from PySide6.QtCore import QThread, Signal

try:
    import pynvml
    pynvml.nvmlInit()
    _NVML_OK = True
except Exception:
    _NVML_OK = False


class SystemMonitor(QThread):
    updated = Signal(dict)

    def __init__(self, interval: float = 2.0, parent=None):
        super().__init__(parent)
        self.interval = interval
        self._running = True
        # Разогреваем psutil, чтобы первый замер не был 0.0
        psutil.cpu_percent(interval=None)
        self._last_net = psutil.net_io_counters()
        self._last_time = time.time()

    def run(self):
        while self._running:
            now = time.time()
            elapsed = now - self._last_time
            net = psutil.net_io_counters()

            up_bps = down_bps = 0.0
            if elapsed > 0:
                up_bps = (net.bytes_sent - self._last_net.bytes_sent) * 8 / elapsed
                down_bps = (net.bytes_recv - self._last_net.bytes_recv) * 8 / elapsed
            self._last_net = net
            self._last_time = now

            data = {
                "cpu": psutil.cpu_percent(interval=None),
                "ram": psutil.virtual_memory().percent,
                "gpu": [],
                "net_up": up_bps,
                "net_down": down_bps,
            }

            if _NVML_OK:
                try:
                    for i in range(pynvml.nvmlDeviceGetCount()):
                        h = pynvml.nvmlDeviceGetHandleByIndex(i)
                        u = pynvml.nvmlDeviceGetUtilizationRates(h)
                        m = pynvml.nvmlDeviceGetMemoryInfo(h)
                        data["gpu"].append({
                            "index": i,
                            "load": u.gpu,
                            "mem_used": m.used / (1024 ** 2),
                            "mem_total": m.total / (1024 ** 2),
                        })
                except Exception:
                    pass

            self.updated.emit(data)
            self.msleep(int(self.interval * 1000))

    def stop(self):
        self._running = False
        self.wait(3000)


def shutdown_nvml():
    if _NVML_OK:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass