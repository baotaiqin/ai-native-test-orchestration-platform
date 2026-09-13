import os

import pytest

from runner.config import RunnerConfig, default_state_dir
from runner.errors import PreflightError, ProtectionError, ProtectionUnavailableError
from runner.models import RunnerIdentity
from runner.protection import DpapiProtector, MemoryProtector
from runner.state import RunnerStateStore


def _config() -> RunnerConfig:
    return RunnerConfig(
        backend_url="https://example.test/",
        name="runner-test",
        tags=("Windows", "api", "api"),
    )


def test_config_and_identity_are_separate_and_identity_is_not_plaintext(tmp_path) -> None:
    store = RunnerStateStore(tmp_path, protector=MemoryProtector())
    identity = RunnerIdentity("runner-001", "credential-value-123456789")

    store.save_config(_config())
    store.save_identity(identity)

    config_text = (tmp_path / "config.json").read_text(encoding="utf-8")
    identity_text = (tmp_path / "identity.dpapi.json").read_text(encoding="utf-8")
    assert "credential-value-123456789" not in config_text
    assert "credential-value-123456789" not in identity_text
    assert store.load_config().backend_url == "https://example.test"
    assert store.load_identity() == identity
    assert not list(tmp_path.glob("*.tmp"))


def test_preflight_uses_non_sensitive_sentinel_and_leaves_no_files(tmp_path) -> None:
    store = RunnerStateStore(tmp_path, protector=MemoryProtector())

    report = store.preflight()

    assert report["ok"] is True
    assert report["dpapi"] == {"ok": True, "roundtrip": True}
    assert report["atomic_write"] == {
        "ok": True,
        "atomic_replace": True,
        "read_back": True,
        "probe_cleaned": True,
    }
    assert list(tmp_path.iterdir()) == []


def test_preflight_reports_dpapi_environment_failure_without_plaintext_fallback(tmp_path) -> None:
    class FailingProtector:
        def protect(self, value: bytes) -> bytes:
            raise ProtectionError("DPAPI system error 2")

        def unprotect(self, value: bytes) -> bytes:
            raise AssertionError("unprotect 不应被调用")

    store = RunnerStateStore(tmp_path, protector=FailingProtector())

    with pytest.raises(PreflightError, match="DPAPI/当前用户保护环境不可用"):
        store.preflight()
    assert list(tmp_path.iterdir()) == []


def test_identity_file_has_restrictive_mode_when_supported(tmp_path) -> None:
    store = RunnerStateStore(tmp_path, protector=MemoryProtector())
    store.save_identity(RunnerIdentity("runner-001", "credential-value-123456789"))
    mode = os.stat(tmp_path / "identity.dpapi.json").st_mode & 0o777
    if os.name != "nt":
        assert mode & 0o077 == 0


def test_default_protector_does_not_downgrade_to_plaintext_on_non_windows() -> None:
    if os.name == "nt":
        pytest.skip("Windows 使用 DPAPI 默认实现")
    with pytest.raises(ProtectionUnavailableError):
        DpapiProtector()


def test_default_state_dir_prefers_localappdata_on_windows(monkeypatch) -> None:
    if os.name != "nt":
        pytest.skip("仅验证 Windows 默认用户目录优先级")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\LocalAppData")
    monkeypatch.setenv("APPDATA", r"C:\RoamingAppData")

    assert str(default_state_dir()).startswith(r"C:\LocalAppData")


def test_dpapi_protector_supports_explicit_test_backend() -> None:
    class FakeBackend:
        def protect(self, value: bytes) -> bytes:
            return b"wrapped:" + value

        def unprotect(self, value: bytes) -> bytes:
            return value.removeprefix(b"wrapped:")

    protector = DpapiProtector(backend=FakeBackend())
    assert protector.unprotect(protector.protect(b"secret")) == b"secret"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅在 Windows 可用")
def test_real_current_user_dpapi_roundtrip() -> None:
    protector = DpapiProtector()
    value = b"runner credential test"
    try:
        encrypted = protector.protect(value)
    except ProtectionError as exc:
        pytest.skip(f"当前 Windows 测试账户没有可用 DPAPI 用户配置文件：{exc}")
    assert protector.unprotect(encrypted) == value
