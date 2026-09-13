import asyncio
import base64
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core import secret_cipher
from app.core.config import Settings
from app.core.exceptions import SecurityConfigurationError
from app.core.fernet_secret_provider import (
    FERNET_PREFIX,
    MAX_FERNET_KEY_FILE_BYTES,
    MAX_FERNET_KEYS,
    MAX_SECRET_PLAINTEXT_BYTES,
    validate_fernet_key_file,
)


def _settings(provider: str, key_file: Path | None = None) -> SimpleNamespace:
    return SimpleNamespace(secret_provider=provider, secret_fernet_key_file=key_file)


def _write_key_file(directory: Path, *keys: bytes) -> Path:
    key_file = directory / "fernet.keys"
    key_file.write_bytes(b"\n".join(keys) + b"\n")
    return key_file


def _synthetic_key(marker: bytes) -> bytes:
    return base64.urlsafe_b64encode(marker * 32)


def _cryptography_fernet():
    return pytest.importorskip(
        "cryptography.fernet",
        reason="cryptography dependency is declared but unavailable in this local environment",
    )


def test_settings_keep_dpapi_default_and_require_explicit_production_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_SECRET_PROVIDER", raising=False)
    development = Settings(_env_file=None)
    assert development.secret_provider == "dpapi"
    assert development.secret_fernet_key_file is None
    assert development.auth_token_issuer == "ai-test-platform"
    assert development.auth_token_audience == "ai-test-platform-api"
    assert development.demo_available is True
    assert development.demo_public_url == "http://127.0.0.1:8765"

    with pytest.raises(ValidationError, match="生产环境必须显式配置 Secret Provider"):
        Settings(
            _env_file=None,
            env="production",
            secret_key="production-signing-key",
            dev_admin_password="production-admin-password",
        )
    production = Settings(
        _env_file=None,
        env="production",
        secret_key="production-signing-key",
        dev_admin_password="production-admin-password",
        secret_provider="FERNET",
    )
    assert production.secret_provider == "fernet"
    assert production.demo_available is False

    production_demo = Settings(
        _env_file=None,
        env="production",
        secret_key="production-signing-key",
        dev_admin_password="production-admin-password",
        secret_provider="fernet",
        demo_enabled=True,
        demo_public_url="https://demo.example.com/",
    )
    assert production_demo.demo_available is True
    assert production_demo.demo_public_url == "https://demo.example.com"


def test_fernet_key_file_missing_invalid_duplicate_and_bounded() -> None:
    with tempfile.TemporaryDirectory(prefix="v1-d2-") as temp_dir:
        directory = Path(temp_dir)
        with pytest.raises(SecurityConfigurationError, match="密钥文件未配置"):
            secret_cipher.validate_secret_provider_configuration(_settings("fernet"))
        missing = directory / "missing.keys"
        with pytest.raises(SecurityConfigurationError) as missing_error:
            validate_fernet_key_file(missing)
        assert str(missing) not in str(missing_error.value)

        invalid = _write_key_file(directory, b"not-a-fernet-key")
        with pytest.raises(SecurityConfigurationError, match="格式无效"):
            validate_fernet_key_file(invalid)

        key = _synthetic_key(b"a")
        duplicate = _write_key_file(directory, key, key)
        with pytest.raises(SecurityConfigurationError, match="重复密钥") as duplicate_error:
            validate_fernet_key_file(duplicate)
        assert key.decode("ascii") not in str(duplicate_error.value)

        excessive = _write_key_file(
            directory, *(_synthetic_key(bytes([index])) for index in range(MAX_FERNET_KEYS + 1))
        )
        with pytest.raises(SecurityConfigurationError, match="数量超过限制"):
            validate_fernet_key_file(excessive)

        oversized = directory / "oversized.keys"
        oversized.write_bytes(b"x" * (MAX_FERNET_KEY_FILE_BYTES + 1))
        with pytest.raises(SecurityConfigurationError, match="大小限制"):
            validate_fernet_key_file(oversized)


def test_valid_fernet_configuration_rejects_missing_dependency_safely() -> None:
    if importlib.util.find_spec("cryptography") is not None:
        pytest.skip("dependency is installed; round-trip tests cover this path")
    with tempfile.TemporaryDirectory(prefix="v1-d2-") as temp_dir:
        key = _synthetic_key(b"k")
        key_file = _write_key_file(Path(temp_dir), key)
        with pytest.raises(SecurityConfigurationError, match="依赖不可用") as error:
            validate_fernet_key_file(key_file)
        assert key.decode("ascii") not in str(error.value)


def test_fernet_unicode_randomized_and_maximum_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fernet = _cryptography_fernet()
    with tempfile.TemporaryDirectory(prefix="v1-d2-") as temp_dir:
        key_file = _write_key_file(Path(temp_dir), fernet.Fernet.generate_key())
        monkeypatch.setattr(secret_cipher, "get_settings", lambda: _settings("fernet", key_file))

        unicode_plaintext = "数据库口令🔐\nsecond-line"
        first = secret_cipher.encrypt_secret(unicode_plaintext, "application-context")
        second = secret_cipher.encrypt_secret(unicode_plaintext, "application-context")
        assert first.startswith(FERNET_PREFIX)
        assert first != second
        assert unicode_plaintext not in first
        assert secret_cipher.decrypt_secret(first, "application-context") == unicode_plaintext

        maximum_plaintext = "x" * MAX_SECRET_PLAINTEXT_BYTES
        maximum_ciphertext = secret_cipher.encrypt_secret(
            maximum_plaintext, "application-context"
        )
        assert secret_cipher.decrypt_secret(
            maximum_ciphertext, "application-context"
        ) == maximum_plaintext

        with pytest.raises(SecurityConfigurationError, match="明文超过大小限制"):
            secret_cipher.encrypt_secret(
                maximum_plaintext + "x", "application-context"
            )


def test_fernet_wrong_key_context_tamper_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fernet = _cryptography_fernet()
    with tempfile.TemporaryDirectory(prefix="v1-d2-") as temp_dir:
        directory = Path(temp_dir)
        old_key = fernet.Fernet.generate_key()
        new_key = fernet.Fernet.generate_key()
        unrelated_key = fernet.Fernet.generate_key()
        key_file = _write_key_file(directory, old_key)
        monkeypatch.setattr(secret_cipher, "get_settings", lambda: _settings("fernet", key_file))

        plaintext = "synthetic-rotation-marker"
        old_ciphertext = secret_cipher.encrypt_secret(plaintext, "context-a")
        original_old_ciphertext = old_ciphertext

        _write_key_file(directory, new_key, old_key)
        assert secret_cipher.decrypt_secret(old_ciphertext, "context-a") == plaintext
        assert old_ciphertext == original_old_ciphertext
        new_ciphertext = secret_cipher.encrypt_secret(plaintext, "context-a")
        token = new_ciphertext.removeprefix(FERNET_PREFIX).encode("ascii")
        fernet.Fernet(new_key).decrypt(token)
        with pytest.raises(fernet.InvalidToken):
            fernet.Fernet(old_key).decrypt(token)

        with pytest.raises(SecurityConfigurationError, match="应用上下文不匹配"):
            secret_cipher.decrypt_secret(new_ciphertext, "context-b")

        invalid_payload_token = fernet.Fernet(new_key).encrypt(
            json.dumps(
                {
                    "application_context": "0" * 64,
                    "plaintext": plaintext,
                    "purpose": "wrong-purpose",
                    "version": 99,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        with pytest.raises(SecurityConfigurationError, match="内部载荷无效"):
            secret_cipher.decrypt_secret(
                FERNET_PREFIX + invalid_payload_token.decode("ascii"), "context-a"
            )
        with pytest.raises(SecurityConfigurationError, match="格式不受支持"):
            secret_cipher.decrypt_secret("fernet:v2:synthetic", "context-a")

        tamper_index = len(FERNET_PREFIX) + 20
        replacement = "A" if new_ciphertext[tamper_index] != "A" else "B"
        tampered = (
            new_ciphertext[:tamper_index]
            + replacement
            + new_ciphertext[tamper_index + 1 :]
        )
        with pytest.raises(SecurityConfigurationError, match="无法认证"):
            secret_cipher.decrypt_secret(tampered, "context-a")

        _write_key_file(directory, new_key)
        with pytest.raises(SecurityConfigurationError, match="无法认证"):
            secret_cipher.decrypt_secret(old_ciphertext, "context-a")

        _write_key_file(directory, unrelated_key)
        with pytest.raises(SecurityConfigurationError, match="无法认证") as wrong_key_error:
            secret_cipher.decrypt_secret(new_ciphertext, "context-a")
        error_text = str(wrong_key_error.value)
        assert plaintext not in error_text
        assert unrelated_key.decode("ascii") not in error_text
        assert str(key_file) not in error_text


def test_dpapi_ciphertext_is_rejected_before_windows_library_on_other_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secret_cipher.sys, "platform", "linux")
    with pytest.raises(SecurityConfigurationError, match="仅支持 Windows"):
        secret_cipher.decrypt_secret("dpapi:v1:c3ludGhldGlj", "context")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI compatibility is Windows-only")
def test_windows_dpapi_historical_prefix_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secret_cipher, "get_settings", lambda: _settings("dpapi"))
    plaintext = "windows-dpapi-兼容"
    ciphertext = secret_cipher.encrypt_secret(plaintext, "application-context")
    assert ciphertext.startswith("dpapi:v1:")
    assert plaintext not in ciphertext
    assert secret_cipher.decrypt_secret(ciphertext, "application-context") == plaintext
    with pytest.raises(SecurityConfigurationError) as context_error:
        secret_cipher.decrypt_secret(ciphertext, "different-context")
    assert plaintext not in str(context_error.value)
    monkeypatch.setattr(secret_cipher, "get_settings", lambda: _settings("fernet"))
    assert secret_cipher.decrypt_secret(ciphertext, "application-context") == plaintext


def test_lifespan_checks_provider_before_starting_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    coordinator_started = False

    class Coordinator:
        def start(self) -> None:
            nonlocal coordinator_started
            coordinator_started = True

        def stop(self) -> None:
            pass

    def reject_provider(_settings: Settings) -> None:
        raise SecurityConfigurationError("synthetic provider rejection")

    monkeypatch.setattr(main, "validate_secret_provider_configuration", reject_provider)
    application = SimpleNamespace(
        dependency_overrides={main.build_disconnect_coordinator: Coordinator}
    )

    async def enter_lifespan() -> None:
        async with main.lifespan(application):
            raise AssertionError("lifespan must not start")

    with pytest.raises(SecurityConfigurationError, match="synthetic provider rejection"):
        asyncio.run(enter_lifespan())
    assert coordinator_started is False
