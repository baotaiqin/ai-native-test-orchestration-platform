"""非敏感配置与 DPAPI 加密身份的原子持久化。"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runner.config import RunnerConfig
from runner.errors import (
    ConfigurationError,
    PreflightError,
    ProtectionError,
    ProtectionUnavailableError,
    RunnerError,
    StateError,
    StateNotFoundError,
)
from runner.models import RunnerIdentity
from runner.protection import DpapiProtector, Protector
from runner.redaction import redact_text

_PREFLIGHT_SENTINEL = b"AI_NATIVE_TEST_RUNNER_PREFLIGHT_V1"


@dataclass(frozen=True)
class StatePaths:
    state_dir: Path

    @property
    def config_file(self) -> Path:
        return self.state_dir / "config.json"

    @property
    def identity_file(self) -> Path:
        return self.state_dir / "identity.dpapi.json"


def _restrict_permissions(path: Path, *, directory: bool = False) -> None:
    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        # Windows 的真正访问控制由用户目录 ACL 和 DPAPI 提供；chmod 只是 Unix
        # 上的额外收紧，不能因不支持而阻断正常运行。
        pass


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path.parent, directory=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        _restrict_permissions(temporary_path)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _restrict_permissions(path)
    except OSError as exc:
        raise StateError("Runner 本地状态原子写入失败") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


class RunnerStateStore:
    """保存配置 JSON 和加密身份，二者使用不同文件。"""

    def __init__(self, state_dir: Path, protector: Protector | None = None) -> None:
        self.paths = StatePaths(Path(state_dir))
        self._protector: Protector | None = protector
        self._protection_init_error: RunnerError | None = None
        if protector is None:
            try:
                self._protector = DpapiProtector()
            except RunnerError as exc:
                # 保留初始化失败，让 doctor 仍可独立执行原子写检查；
                # 任何正式身份操作仍会明确拒绝，不会降级为明文。
                self._protection_init_error = exc
            except Exception as exc:
                self._protection_init_error = StateError("DPAPI/当前用户保护环境不可用")
                self._protection_init_error.__cause__ = exc

    def _require_protector(self) -> Protector:
        if self._protector is None:
            raise StateError("DPAPI/当前用户保护环境不可用") from self._protection_init_error
        return self._protector

    def save_config(self, config: RunnerConfig) -> None:
        content = json.dumps(
            config.to_mapping(), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        _atomic_write(self.paths.config_file, content)

    def check_dpapi(self) -> dict[str, object]:
        """独立检查当前用户 DPAPI round-trip，不接触真实身份。"""

        try:
            protector = self._require_protector()
            protected = protector.protect(_PREFLIGHT_SENTINEL)
            if protected == _PREFLIGHT_SENTINEL:
                raise ProtectionError("保护器未产生加密结果")
            restored = protector.unprotect(protected)
            if restored != _PREFLIGHT_SENTINEL:
                raise ProtectionError("保护器往返结果不一致")
        except (ProtectionError, ProtectionUnavailableError, StateError) as exc:
            raise PreflightError("DPAPI/当前用户保护环境不可用，请检查用户配置环境") from exc
        except Exception as exc:
            raise PreflightError("DPAPI/当前用户保护环境往返检查失败") from exc
        return {"ok": True, "roundtrip": True}

    def check_atomic_write(self) -> dict[str, object]:
        """独立检查状态目录原子替换、回读和 probe 清理。"""

        probe_path = self.paths.state_dir / f".runner-preflight-{secrets.token_hex(8)}"
        check_error: Exception | None = None
        probe_cleaned = False
        try:
            _atomic_write(probe_path, b"runner-state-preflight")
            if probe_path.read_bytes() != b"runner-state-preflight":
                raise StateError("状态目录原子写入回读不一致")
        except Exception as exc:
            check_error = exc
        finally:
            try:
                probe_path.unlink(missing_ok=True)
                probe_cleaned = not probe_path.exists()
            except OSError as exc:
                check_error = check_error or exc
        if check_error is not None or not probe_cleaned:
            raise PreflightError("Runner 状态目录原子写入、回读或清理检查失败") from check_error
        return {
            "ok": True,
            "atomic_replace": True,
            "read_back": True,
            "probe_cleaned": probe_cleaned,
        }

    def preflight_report(self) -> dict[str, object]:
        """独立执行两项检查并返回不短路的准确报告。"""

        report: dict[str, object] = {"ok": False}
        for name, checker in (
            ("dpapi", self.check_dpapi),
            ("atomic_write", self.check_atomic_write),
        ):
            try:
                report[name] = checker()
            except PreflightError as exc:
                report[name] = {"ok": False, "error": redact_text(exc)}
        dpapi_report = report.get("dpapi")
        atomic_report = report.get("atomic_write")
        report["ok"] = bool(
            isinstance(dpapi_report, dict)
            and isinstance(atomic_report, dict)
            and dpapi_report.get("ok") is True
            and atomic_report.get("ok") is True
        )
        return report

    def preflight(self) -> dict[str, object]:
        """注册前执行两项检查，任一失败都阻止 Token prompt 和网络请求。"""

        report = self.preflight_report()
        if report["ok"] is not True:
            failed: list[str] = []
            for name in ("dpapi", "atomic_write"):
                check = report.get(name)
                if isinstance(check, dict) and check.get("ok") is not True:
                    detail = check.get("error")
                    failed.append(f"{name}: {detail}" if isinstance(detail, str) else name)
            raise PreflightError(f"Runner 本地预检失败：{', '.join(failed)}")
        return report

    def load_config(self) -> RunnerConfig:
        try:
            raw = self.paths.config_file.read_bytes()
        except FileNotFoundError as exc:
            raise StateNotFoundError("Runner 配置不存在，请先执行 register") from exc
        except OSError as exc:
            raise StateError("Runner 配置读取失败") from exc
        try:
            return RunnerConfig.from_mapping(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, ConfigurationError) as exc:
            raise StateError("Runner 配置损坏或不符合当前版本") from exc

    def save_identity(self, identity: RunnerIdentity) -> None:
        if not identity.runner_id or not identity.credential:
            raise StateError("Runner 身份字段不完整")
        payload = json.dumps(
            {"version": 1, "runner_id": identity.runner_id, "credential": identity.credential},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            encrypted = self._require_protector().protect(payload)
        except Exception as exc:
            if isinstance(exc, StateError):
                raise
            raise StateError("Runner credential 加密失败") from exc
        envelope = {
            "version": 1,
            "protector": "windows-dpapi",
            "payload": base64.b64encode(encrypted).decode("ascii"),
        }
        _atomic_write(
            self.paths.identity_file,
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )

    def load_identity(self) -> RunnerIdentity:
        try:
            raw = self.paths.identity_file.read_bytes()
        except FileNotFoundError as exc:
            raise StateNotFoundError("Runner 身份不存在，请先执行 register") from exc
        except OSError as exc:
            raise StateError("Runner 身份读取失败") from exc
        try:
            envelope: Any = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(envelope, dict)
                or envelope.get("version") != 1
                or envelope.get("protector") != "windows-dpapi"
                or not isinstance(envelope.get("payload"), str)
            ):
                raise ValueError
            encrypted = base64.b64decode(envelope["payload"], validate=True)
            payload = self._require_protector().unprotect(encrypted)
            identity = json.loads(payload.decode("utf-8"))
            if (
                not isinstance(identity, dict)
                or not isinstance(identity.get("runner_id"), str)
                or not isinstance(identity.get("credential"), str)
                or not identity["runner_id"]
                or not identity["credential"]
            ):
                raise ValueError
            return RunnerIdentity(identity["runner_id"], identity["credential"])
        except Exception as exc:
            if isinstance(exc, StateError):
                raise
            raise StateError("Runner 身份损坏、无法解密或不属于当前用户") from exc
