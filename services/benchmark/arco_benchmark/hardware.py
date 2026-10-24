"""`HardwareInfo` read from the machine. Nothing here is a constant."""

from __future__ import annotations

import platform
import shutil
import socket
import subprocess
from pathlib import Path

import psutil

from arco_schemas import HardwareInfo


def _run(cmd: list[str], timeout: float = 3.0) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    return text or None


def cpu_model() -> str:
    system = platform.system()
    if system == "Darwin":
        brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if brand:
            return brand
    if system == "Linux":
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            for line in cpuinfo.read_text().splitlines():
                if line.lower().startswith(("model name", "hardware", "processor\t")):
                    _, _, value = line.partition(":")
                    if value.strip():
                        return value.strip()
    return platform.processor() or platform.machine() or "unknown"


def gpu_model(providers: list[str]) -> str | None:
    """The GPU onnxruntime can actually use. None when inference is CPU-only — an Apple
    GPU that ORT's CPU provider never touches is not reported as the benchmark's GPU."""
    if any(p in providers for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider")):
        name = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        return name.splitlines()[0] if name else "CUDA device (nvidia-smi unavailable)"
    if "CoreMLExecutionProvider" in providers:
        return "Apple GPU/ANE via CoreML EP"
    return None


def ort_providers() -> list[str]:
    try:
        import onnxruntime as ort  # noqa: PLC0415 - optional at import time for the CLI

        return list(ort.get_available_providers())
    except ImportError:  # pragma: no cover - onnxruntime is a hard dependency here
        return []


def read_hardware(active_providers: list[str] | None = None) -> HardwareInfo:
    """`active_providers` = the providers the sessions actually ran with (from
    `session.get_providers()`), which is what the GPU field should reflect."""
    available = ort_providers()
    active = active_providers if active_providers is not None else available
    return HardwareInfo(
        hostname=socket.gethostname(),
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        cpu=cpu_model(),
        cpu_count=psutil.cpu_count(logical=True) or 1,
        memory_gb=round(psutil.virtual_memory().total / 2**30, 1),
        gpu=gpu_model(active),
        onnxruntime_providers=active,
        python=platform.python_version(),
    )
