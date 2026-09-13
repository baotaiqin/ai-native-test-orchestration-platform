"""Windows Runner 本机环境快照。"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Lock
from typing import Protocol

from runner.config import normalize_tags

Snapshot = dict[str, object]

_CAPABILITY_NAMES = ("API", "WEB", "SQL", "SCRIPT", "SSE", "JMETER")
_READY_CAPABILITIES = frozenset({"API", "SQL", "SCRIPT", "SSE"})
_UNAVAILABLE_CAPABILITY_REASON = "当前 Runner 未开放该类型 Executor"
_SNAPSHOT_FIELD_LIMITS = {
    "name": 128,
    "hostname": 255,
    "ip_address": 64,
    "os": 128,
    "cpu": 128,
    "ram": 128,
    "disk": 128,
    "python": 64,
    "chrome": 64,
    "playwright": 64,
    "java": 64,
    "jmeter": 64,
}
_COMMAND_LOG_LEVEL_RE = re.compile(
    r"(?:^|[\s\[\]():,;/\\-])(?:WARN(?:ING)?|ERROR|INFO|DEBUG|TRACE)"
    r"(?=$|[\s\[\]():,;/\\-])",
    re.IGNORECASE,
)
_STATUS_CONSOLE_LISTENER_RE = re.compile(r"\bStatusConsoleListener\b", re.IGNORECASE)
_CHROME_VERSION_RE = re.compile(
    r"\b(?:Google\s+)?Chrome\b.*\b\d+(?:\.\d+)+(?!\d)"
    r"|\bChromium\b.*\b\d+(?:\.\d+)+(?!\d)",
    re.IGNORECASE,
)
_JAVA_VERSION_RE = re.compile(
    r"^\s*(?:java|openjdk)(?:\s+version)?\s+[\"']?\d+(?:\.\d+)+",
    re.IGNORECASE,
)
_JMETER_VERSION_RE = re.compile(
    r"\bApache\s+JMeter\s+\d+(?:\.\d+)+(?!\d)",
    re.IGNORECASE,
)
_WEB_PROBE_LOCK = Lock()
_WEB_PROBE_CACHE: bool | None = None
_CHROME_VERSION_CACHE: str | None = None


class SnapshotProbe(Protocol):
    def hostname(self) -> str: ...

    def ip_address(self, hostname: str) -> str | None: ...

    def os(self) -> str | None: ...

    def cpu(self) -> str | None: ...

    def ram(self) -> str | None: ...

    def disk(self) -> str | None: ...

    def python(self) -> str | None: ...

    def chrome(self) -> str | None: ...

    def playwright(self) -> str | None: ...

    def web_ready(self) -> bool: ...

    def java(self) -> str | None: ...

    def jmeter(self) -> str | None: ...


def _is_command_noise(line: str) -> bool:
    return bool(_COMMAND_LOG_LEVEL_RE.search(line) or _STATUS_CONSOLE_LISTENER_RE.search(line))


def _command_version(
    commands: tuple[str, ...],
    *,
    validator: Callable[[str], bool],
) -> str | None:
    with tempfile.TemporaryDirectory(prefix="runner-probe-") as probe_dir:
        for command in commands:
            executable = shutil.which(command)
            if not executable:
                continue
            try:
                completed = subprocess.run(
                    [executable, "--version"],
                    capture_output=True,
                    check=False,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=probe_dir,
                    timeout=2,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            output_lines = (completed.stdout or "").splitlines()
            output_lines.extend((completed.stderr or "").splitlines())
            clean_lines = [line.strip() for line in output_lines if line.strip()]
            clean_lines = [line for line in clean_lines if not _is_command_noise(line)]
            version = next(
                (line for line in clean_lines if validator(line)),
                None,
            )
            if version:
                return version
    return None


def _is_chrome_version(line: str) -> bool:
    return bool(_CHROME_VERSION_RE.search(line))


def _is_java_version(line: str) -> bool:
    return bool(_JAVA_VERSION_RE.search(line))


def _is_jmeter_version(line: str) -> bool:
    return bool(_JMETER_VERSION_RE.search(line))


def _known_chrome_commands() -> tuple[str, ...]:
    commands = ["chrome", "chrome.exe", "google-chrome", "chromium", "chromium-browser"]
    if os.name == "nt":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                commands.append(
                    str(Path(root) / "Google/Chrome/Application/chrome.exe"),
                )
    return tuple(commands)


class DefaultSnapshotProbe:
    def hostname(self) -> str:
        return socket.gethostname() or "unknown-host"

    def ip_address(self, hostname: str) -> str | None:
        try:
            return socket.gethostbyname(hostname)
        except OSError:
            return None

    def os(self) -> str | None:
        value = f"{platform.system()} {platform.release()}".strip()
        return value or None

    def cpu(self) -> str | None:
        processor = platform.processor().strip()
        logical = os.cpu_count()
        if processor and logical:
            return f"{processor} ({logical} logical CPUs)"
        return processor or (f"{logical} logical CPUs" if logical else None)

    def ram(self) -> str | None:
        total_bytes: int | None = None
        if os.name == "nt":
            try:
                import ctypes

                class MemoryStatus(ctypes.Structure):
                    _fields_ = [
                        ("length", ctypes.c_ulong),
                        ("memory_load", ctypes.c_ulong),
                        ("total_physical", ctypes.c_ulonglong),
                        ("available_physical", ctypes.c_ulonglong),
                        ("total_page_file", ctypes.c_ulonglong),
                        ("available_page_file", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong),
                        ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended_virtual", ctypes.c_ulonglong),
                    ]

                status = MemoryStatus()
                status.length = ctypes.sizeof(MemoryStatus)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                    total_bytes = int(status.total_physical)
            except (AttributeError, OSError, TypeError):
                total_bytes = None
        else:
            try:
                page_size = os.sysconf("SC_PAGE_SIZE")
                page_count = os.sysconf("SC_PHYS_PAGES")
                total_bytes = int(page_size) * int(page_count)
            except (AttributeError, OSError, ValueError):
                total_bytes = None
        return _format_bytes(total_bytes) if total_bytes else None

    def disk(self) -> str | None:
        try:
            usage = shutil.disk_usage(Path.cwd())
        except OSError:
            return None
        return f"total {_format_bytes(usage.total)}, free {_format_bytes(usage.free)}"

    def python(self) -> str:
        return platform.python_version()

    def chrome(self) -> str | None:
        if os.name == "nt":
            with _WEB_PROBE_LOCK:
                version = _CHROME_VERSION_CACHE
            return version[: _SNAPSHOT_FIELD_LIMITS["chrome"]] if version else None
        version = _command_version(
            _known_chrome_commands(),
            validator=_is_chrome_version,
        )
        if version:
            return version[: _SNAPSHOT_FIELD_LIMITS["chrome"]]
        return None

    def playwright(self) -> str | None:
        try:
            return importlib.metadata.version("playwright")[:64]
        except importlib.metadata.PackageNotFoundError:
            return None
        except Exception:
            return None

    def web_ready(self) -> bool:
        """Probe Playwright plus the installed Chrome channel without downloading."""

        global _CHROME_VERSION_CACHE, _WEB_PROBE_CACHE
        with _WEB_PROBE_LOCK:
            if _WEB_PROBE_CACHE is not None:
                return _WEB_PROBE_CACHE
        if self.playwright() is None:
            with _WEB_PROBE_LOCK:
                _WEB_PROBE_CACHE = False
            return False
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                chromium = playwright.chromium
                executable = _find_chrome_executable()
                kwargs: dict[str, object] = {"headless": True}
                if executable:
                    kwargs["executable_path"] = executable
                else:
                    kwargs["channel"] = "chrome"
                browser = chromium.launch(**kwargs)
                version = getattr(browser, "version", None)
                browser.close()
            with _WEB_PROBE_LOCK:
                if isinstance(version, str) and version.strip():
                    _CHROME_VERSION_CACHE = f"Google Chrome {version.strip()}"[:64]
                _WEB_PROBE_CACHE = True
            return True
        except Exception:
            with _WEB_PROBE_LOCK:
                _WEB_PROBE_CACHE = False
            return False

    def java(self) -> str | None:
        return _command_version(
            ("java", "java.exe"),
            validator=_is_java_version,
        )

    def jmeter(self) -> str | None:
        version = _command_version(
            ("jmeter", "jmeter.bat", "jmeter.cmd"),
            validator=_is_jmeter_version,
        )
        if version:
            return version
        for command in ("jmeter", "jmeter.bat", "jmeter.cmd"):
            executable = shutil.which(command)
            if not executable:
                continue
            match = re.search(r"apache-jmeter[-_/]([0-9]+(?:\.[0-9]+)+)", executable, re.I)
            if match:
                return f"Apache JMeter {match.group(1)}"
        return None


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(max(value, 0))
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TB"


def _probe_call(
    probe: SnapshotProbe,
    method_name: str,
    *arguments: object,
    fallback: object = None,
) -> object:
    try:
        method = getattr(probe, method_name)
        return method(*arguments)
    except Exception:
        return fallback


def _find_chrome_executable() -> str | None:
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    if os.name == "nt":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                candidates.append(str(Path(root) / "Google/Chrome/Application/chrome.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def collect_snapshot(
    name: str,
    *,
    tags: tuple[str, ...] | list[str] = (),
    api_slots: int = 1,
    web_slots: int = 0,
    performance_slots: int = 0,
    available_slots: Mapping[str, int] | None = None,
    probe: SnapshotProbe | None = None,
) -> Snapshot:
    """构造符合后端 RunnerSnapshotFields 的严格字段集合。"""

    normalized_name = name.strip() if isinstance(name, str) else ""
    if not normalized_name:
        raise ValueError("Runner name 不能为空")
    normalized_name = normalized_name[: _SNAPSHOT_FIELD_LIMITS["name"]]
    for value in (api_slots, web_slots, performance_slots):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10000:
            raise ValueError("Runner slot 必须在 0 到 10000 之间")
    available_api = api_slots
    available_web = web_slots
    available_performance = performance_slots
    if available_slots is not None:
        candidate = available_slots.get("API", api_slots)
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            raise ValueError("Runner API available slot 必须是整数")
        if not 0 <= candidate <= api_slots:
            raise ValueError("Runner API available slot 必须在 0 到 total 之间")
        available_api = candidate
        web_candidate = available_slots.get("WEB", web_slots)
        if isinstance(web_candidate, bool) or not isinstance(web_candidate, int):
            raise ValueError("Runner WEB available slot 必须是整数")
        if not 0 <= web_candidate <= web_slots:
            raise ValueError("Runner WEB available slot 必须在 0 到 total 之间")
        available_web = web_candidate
        performance_candidate = available_slots.get("PERFORMANCE", performance_slots)
        if isinstance(performance_candidate, bool) or not isinstance(
            performance_candidate, int
        ):
            raise ValueError("Runner PERFORMANCE available slot 必须是整数")
        if not 0 <= performance_candidate <= performance_slots:
            raise ValueError("Runner PERFORMANCE available slot 必须在 0 到 total 之间")
        available_performance = performance_candidate
    normalized_tags = normalize_tags(tags)
    active_probe = probe or DefaultSnapshotProbe()
    web_ready = bool(_probe_call(active_probe, "web_ready", fallback=False))
    hostname = (
        _bounded_text(
            _probe_call(active_probe, "hostname", fallback="unknown-host"),
            _SNAPSHOT_FIELD_LIMITS["hostname"],
        )
        or "unknown-host"
    )
    chrome_value = _probe_call(active_probe, "chrome")
    if chrome_value is None and web_ready and isinstance(active_probe, DefaultSnapshotProbe):
        chrome_value = _CHROME_VERSION_CACHE
    optional_values = {
        "ip_address": _probe_call(active_probe, "ip_address", hostname),
        "os": _probe_call(active_probe, "os"),
        "cpu": _probe_call(active_probe, "cpu"),
        "ram": _probe_call(active_probe, "ram"),
        "disk": _probe_call(active_probe, "disk"),
        "python": _probe_call(active_probe, "python"),
        "chrome": chrome_value,
        "playwright": _probe_call(active_probe, "playwright"),
        "java": _probe_call(active_probe, "java"),
        "jmeter": _probe_call(active_probe, "jmeter"),
    }
    for key, value in optional_values.items():
        if value is not None:
            optional_values[key] = _bounded_text(value, _SNAPSHOT_FIELD_LIMITS[key])
    capability_reason = _bounded_text(_UNAVAILABLE_CAPABILITY_REASON, 256)
    ready_capabilities = set(_READY_CAPABILITIES)
    if web_ready:
        ready_capabilities.add("WEB")
    if optional_values["jmeter"] is not None:
        ready_capabilities.add("JMETER")
    capabilities = [
        {
            "name": capability,
            "status": "READY" if capability in ready_capabilities else "UNAVAILABLE",
            "reason": None if capability in ready_capabilities else capability_reason,
        }
        for capability in _CAPABILITY_NAMES
    ]
    return {
        "name": normalized_name,
        "hostname": hostname,
        **optional_values,
        "tags": list(normalized_tags),
        "capabilities": capabilities,
        "slots": [
            {"type": "API", "total": api_slots, "available": available_api},
            {"type": "WEB", "total": web_slots, "available": available_web if web_ready else 0},
            {
                "type": "PERFORMANCE",
                "total": performance_slots,
                "available": available_performance,
            },
        ],
    }


def _bounded_text(value: object, limit: int) -> str | None:
    text = str(value).strip()
    return text[:limit] or None
