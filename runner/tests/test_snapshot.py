from runner import snapshot as snapshot_module
from runner.slots import SlotState
from runner.snapshot import DefaultSnapshotProbe, collect_snapshot
from tests.helpers import FakeProbe


class BrokenProbe:
    def __getattribute__(self, name: str):
        if name.startswith("__"):
            return object.__getattribute__(self, name)
        raise RuntimeError("probe failed")


def test_snapshot_probe_failure_is_degraded_and_protocol_complete() -> None:
    snapshot = collect_snapshot("runner-test", probe=BrokenProbe())

    assert snapshot["hostname"] == "unknown-host"
    assert snapshot["ip_address"] is None
    assert len(snapshot["capabilities"]) == 6
    capabilities = {item["name"]: item for item in snapshot["capabilities"]}
    assert all(
        capabilities[name] == {"name": name, "status": "READY", "reason": None}
        for name in ("API", "SQL", "SCRIPT", "SSE")
    )
    assert all(
        item["status"] == "UNAVAILABLE" and item["reason"]
        for name, item in capabilities.items()
        if name in {"WEB", "JMETER"}
    )
    assert snapshot["slots"] == [
        {"type": "API", "total": 1, "available": 1},
        {"type": "WEB", "total": 0, "available": 0},
        {"type": "PERFORMANCE", "total": 0, "available": 0},
    ]


def test_snapshot_values_and_tags_are_normalized() -> None:
    snapshot = collect_snapshot(
        " runner-test ",
        tags=(" Windows ", "api", "api"),
        api_slots=2,
        web_slots=1,
        performance_slots=0,
        probe=FakeProbe(),
    )

    assert snapshot["name"] == "runner-test"
    assert snapshot["tags"] == ["windows", "api"]
    assert snapshot["ip_address"] == "10.0.0.10"
    assert snapshot["chrome"] == "Chrome Test"
    capability_status = {item["name"]: item["status"] for item in snapshot["capabilities"]}
    assert {
        name: capability_status[name] for name in ("API", "SQL", "SCRIPT", "SSE", "JMETER")
    } == {
        "API": "READY",
        "SQL": "READY",
        "SCRIPT": "READY",
        "SSE": "READY",
        "JMETER": "READY",
    }
    assert capability_status["WEB"] == "UNAVAILABLE"
    assert {item["type"]: item for item in snapshot["slots"]}["API"]["available"] == 2


def test_snapshot_ready_capabilities_do_not_change_slot_model() -> None:
    snapshot = collect_snapshot("runner-test", api_slots=1)

    capabilities = {item["name"]: item for item in snapshot["capabilities"]}
    assert [
        capabilities[name]["status"] for name in ("API", "SQL", "SCRIPT", "SSE")
    ] == [
        "READY",
        "READY",
        "READY",
        "READY",
    ]
    assert snapshot["slots"] == [
        {"type": "API", "total": 1, "available": 1},
        {"type": "WEB", "total": 0, "available": 0},
        {"type": "PERFORMANCE", "total": 0, "available": 0},
    ]


def test_snapshot_reads_dynamic_available_for_api_and_performance_slots() -> None:
    state = SlotState({"API": 1, "WEB": 2, "PERFORMANCE": 3})
    api_lease = state.try_acquire("API")
    performance_lease = state.try_acquire("PERFORMANCE")
    assert api_lease is not None
    assert performance_lease is not None

    snapshot = collect_snapshot(
        "runner-test",
        api_slots=1,
        web_slots=2,
        performance_slots=3,
        available_slots=state.available(),
        probe=FakeProbe(),
    )

    assert snapshot["slots"] == [
        {"type": "API", "total": 1, "available": 0},
        {"type": "WEB", "total": 2, "available": 0},
        {"type": "PERFORMANCE", "total": 3, "available": 2},
    ]
    api_lease.release()
    performance_lease.release()


def test_command_version_skips_warn_and_status_console_noise(monkeypatch) -> None:
    class Completed:
        stdout = "WARN StatusConsoleListener noisy warning\nApache JMeter 5.6.3\n"
        stderr = ""

    monkeypatch.setattr(snapshot_module.shutil, "which", lambda command: command)
    monkeypatch.setattr(snapshot_module.subprocess, "run", lambda *args, **kwargs: Completed())

    assert DefaultSnapshotProbe().jmeter() == "Apache JMeter 5.6.3"


def test_chrome_crashpad_error_without_version_returns_none(monkeypatch) -> None:
    class Completed:
        stdout = "[0822/120000:ERROR:third_party\\crashpad\\client\\crashpad_client_win.cc:815]"
        stderr = ""

    monkeypatch.setattr(snapshot_module.os, "name", "posix")
    monkeypatch.setattr(snapshot_module.shutil, "which", lambda command: "chrome.exe")
    monkeypatch.setattr(snapshot_module.subprocess, "run", lambda *args, **kwargs: Completed())

    assert DefaultSnapshotProbe().chrome() is None


def test_chrome_crashpad_error_before_real_version_returns_chrome(monkeypatch) -> None:
    class Completed:
        stdout = (
            "[0822/120000:ERROR:third_party\\crashpad\\client\\crashpad_client_win.cc:815]\n"
            "Google Chrome 138.0.7204.186\n"
        )
        stderr = ""

    monkeypatch.setattr(snapshot_module.os, "name", "posix")
    monkeypatch.setattr(snapshot_module.shutil, "which", lambda command: "chrome.exe")
    monkeypatch.setattr(snapshot_module.subprocess, "run", lambda *args, **kwargs: Completed())

    assert DefaultSnapshotProbe().chrome() == "Google Chrome 138.0.7204.186"


def test_windows_chrome_uses_headless_cache_without_subprocess(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_module.os, "name", "nt")
    monkeypatch.setattr(snapshot_module, "_CHROME_VERSION_CACHE", "Google Chrome 138.0.7204.186")

    def fail_run(*args, **kwargs):
        raise AssertionError("Windows Chrome version must not use subprocess")

    monkeypatch.setattr(snapshot_module.subprocess, "run", fail_run)

    assert DefaultSnapshotProbe().chrome() == "Google Chrome 138.0.7204.186"


def test_windows_chrome_without_headless_cache_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_module.os, "name", "nt")
    monkeypatch.setattr(snapshot_module, "_CHROME_VERSION_CACHE", None)

    def fail_run(*args, **kwargs):
        raise AssertionError("Windows Chrome version must not use subprocess")

    monkeypatch.setattr(snapshot_module.subprocess, "run", fail_run)

    assert DefaultSnapshotProbe().chrome() is None


def test_repeated_collect_snapshot_runs_headless_probe_once(monkeypatch) -> None:
    class Browser:
        version = "138.0.7204.186"

        def close(self) -> None:
            return None

    class Chromium:
        def __init__(self) -> None:
            self.launches = 0

        def launch(self, **kwargs: object) -> Browser:
            del kwargs
            self.launches += 1
            return Browser()

    class Playwright:
        def __init__(self, chromium: Chromium) -> None:
            self.chromium = chromium

    class PlaywrightContext:
        def __init__(self, playwright: Playwright) -> None:
            self.playwright = playwright

        def __enter__(self) -> Playwright:
            return self.playwright

        def __exit__(self, *_: object) -> None:
            return None

    chromium = Chromium()

    class CountingProbe(DefaultSnapshotProbe):
        def playwright(self) -> str:
            return "1.0"

        def java(self) -> str | None:
            return None

        def jmeter(self) -> str | None:
            return None

    monkeypatch.setattr(snapshot_module.os, "name", "nt")
    monkeypatch.setattr(snapshot_module, "_WEB_PROBE_CACHE", None)
    monkeypatch.setattr(snapshot_module, "_CHROME_VERSION_CACHE", None)
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: PlaywrightContext(Playwright(chromium)),
    )

    probe = CountingProbe()
    first = collect_snapshot("runner-test", probe=probe)
    second = collect_snapshot("runner-test", probe=probe)

    assert first["chrome"] == "Google Chrome 138.0.7204.186"
    assert second["chrome"] == "Google Chrome 138.0.7204.186"
    assert chromium.launches == 1


def test_snapshot_fields_are_bounded_to_backend_schema_lengths() -> None:
    class LongProbe(FakeProbe):
        def hostname(self) -> str:
            return "h" * 400

        def ip_address(self, hostname: str) -> str:
            return "i" * 100

        def os(self) -> str:
            return "o" * 200

        def cpu(self) -> str:
            return "c" * 200

        def ram(self) -> str:
            return "r" * 200

        def disk(self) -> str:
            return "d" * 200

        def python(self) -> str:
            return "p" * 100

        def chrome(self) -> str:
            return "c" * 100

        def playwright(self) -> str:
            return "p" * 100

        def java(self) -> str:
            return "j" * 100

        def jmeter(self) -> str:
            return "WARN StatusConsoleListener " + "w" * 300

    snapshot = collect_snapshot("n" * 300, probe=LongProbe())

    expected_limits = {
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
    for field, limit in expected_limits.items():
        assert isinstance(snapshot[field], str)
        assert len(snapshot[field]) <= limit
    assert all(
        item["reason"] is None or len(item["reason"]) <= 256
        for item in snapshot["capabilities"]
    )


def test_jmeter_long_warn_is_not_reported_as_version(monkeypatch) -> None:
    class Completed:
        stdout = "WARN StatusConsoleListener " + "x" * 400 + "\nApache JMeter 5.6.3\n"
        stderr = ""

    monkeypatch.setattr(snapshot_module.shutil, "which", lambda command: "jmeter.exe")
    monkeypatch.setattr(snapshot_module.subprocess, "run", lambda *args, **kwargs: Completed())

    assert DefaultSnapshotProbe().jmeter() == "Apache JMeter 5.6.3"
