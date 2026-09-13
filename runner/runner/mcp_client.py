"""Minimal, deny-by-default stdio client for the pinned Playwright MCP server."""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from runner.errors import ConfigurationError, ExecutionError

PLAYWRIGHT_MCP_VERSION = "0.0.80"
MAX_MCP_MESSAGE_BYTES = 1_000_000
MAX_SCREENSHOT_BYTES = 5_000_000
_SCREENSHOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ALLOWED_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_select_option",
        "browser_press_key",
        "browser_wait_for",
        "browser_network_requests",
        "browser_generate_locator",
        "browser_close",
    }
)
FORBIDDEN_TOOLS = frozenset(
    {
        "browser_run_code_unsafe",
        "browser_evaluate",
        "browser_file_upload",
        "browser_take_screenshot",
        "browser_network_request",
        "browser_route",
        "browser_cookie_list",
        "browser_localstorage_list",
        "browser_sessionstorage_list",
        "browser_tabs",
    }
)


class PlaywrightMcpClient:
    """One isolated MCP process; no remote transport and no caller-selected tools."""

    def __init__(
        self,
        *,
        runtime_dir: Path,
        work_dir: Path,
        allowed_origins: tuple[str, ...],
        storage_state: Mapping[str, Any] | None = None,
        headless: bool = True,
        request_timeout_seconds: float = 45.0,
    ) -> None:
        if not runtime_dir.is_absolute() or not work_dir.is_absolute():
            raise ConfigurationError("Playwright MCP 路径必须是绝对路径")
        if not 1 <= len(allowed_origins) <= 20:
            raise ConfigurationError("Playwright MCP allowed origins 无效")
        if type(headless) is not bool:
            raise ConfigurationError("Playwright MCP 运行模式无效")
        self.runtime_dir = runtime_dir
        self.work_dir = work_dir
        self.allowed_origins = allowed_origins
        self.storage_state = dict(storage_state) if storage_state is not None else None
        self.headless = headless
        self.request_timeout_seconds = request_timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[object] = queue.Queue()
        self._stderr: list[str] = []
        self._request_id = 0
        self._tools: set[str] = set()

    def __enter__(self) -> PlaywrightMcpClient:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        if self._process is not None:
            raise ConfigurationError("Playwright MCP 已启动")
        node = shutil.which("node")
        cli = self.runtime_dir / "node_modules" / "@playwright" / "mcp" / "cli.js"
        package = self.runtime_dir / "node_modules" / "@playwright" / "mcp" / "package.json"
        if node is None or not cli.is_file() or not package.is_file():
            raise ConfigurationError(
                "Playwright MCP 未安装；请先在 runner/mcp_node 执行 npm install"
            )
        try:
            metadata = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError("Playwright MCP package metadata 无法读取") from exc
        if metadata.get("version") != PLAYWRIGHT_MCP_VERSION:
            raise ConfigurationError("Playwright MCP 版本与 Runner 安全基线不一致")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        args = [
            node,
            str(cli),
            "--isolated",
            "--browser",
            "chrome",
            "--caps",
            "testing",
            "--allowed-origins",
            ";".join(self.allowed_origins),
            "--block-service-workers",
            "--image-responses",
            "omit",
            "--codegen",
            "none",
            "--output-dir",
            str(self.work_dir),
            "--output-max-size",
            "10485760",
            "--timeout-action",
            "5000",
            "--timeout-navigation",
            "30000",
        ]
        if self.headless:
            args.insert(3, "--headless")
        if self.storage_state is not None:
            storage_path = self.work_dir / "initial-storage-state.json"
            encoded = json.dumps(self.storage_state, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 1_000_000:
                raise ConfigurationError("Playwright MCP Storage State 超过 1MB")
            storage_path.write_text(encoded, encoding="utf-8")
            args.extend(("--storage-state", str(storage_path)))
        try:
            self._process = subprocess.Popen(
                args,
                cwd=self.work_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise ConfigurationError("Playwright MCP 进程启动失败") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        initialized = self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "ai-native-test-platform-runner",
                    "version": "0.1.0",
                },
            },
        )
        if not isinstance(initialized, Mapping):
            self.close()
            raise ExecutionError("Playwright MCP initialize 响应无效")
        self._notify("notifications/initialized", {})
        tools = self._request("tools/list", {})
        listed = tools.get("tools") if isinstance(tools, Mapping) else None
        if not isinstance(listed, list):
            self.close()
            raise ExecutionError("Playwright MCP tools/list 响应无效")
        self._tools = {
            item.get("name")
            for item in listed
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }
        required = {
            "browser_navigate",
            "browser_snapshot",
            "browser_click",
            "browser_type",
            "browser_select_option",
            "browser_press_key",
            "browser_wait_for",
            "browser_network_requests",
            "browser_generate_locator",
            "browser_close",
        }
        # Presence of unsafe tools is expected in the upstream server. They remain
        # inaccessible because call_tool enforces an independent allow-list.
        if not required.issubset(self._tools):
            self.close()
            raise ExecutionError("Playwright MCP 缺少 Runner 所需的固定工具")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> str:
        if name not in ALLOWED_TOOLS or name in FORBIDDEN_TOOLS:
            raise ConfigurationError("Playwright MCP 工具不在 Runner 白名单")
        if name not in self._tools:
            raise ExecutionError("Playwright MCP 当前版本未提供所需工具")
        result = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        if not isinstance(result, Mapping):
            raise ExecutionError("Playwright MCP tool 响应无效")
        if result.get("isError") is True:
            raise ExecutionError("Playwright MCP tool 执行失败")
        content = result.get("content")
        if not isinstance(content, list):
            raise ExecutionError("Playwright MCP tool content 无效")
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, Mapping)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        output = "\n".join(texts)
        if len(output.encode("utf-8")) > MAX_MCP_MESSAGE_BYTES:
            raise ExecutionError("Playwright MCP tool 输出超过 1MB")
        return output

    def capture_screenshot(self, artifact_name: str) -> Path:
        """Capture one fixed PNG without exposing screenshot arguments to AI decisions."""

        if not _SCREENSHOT_NAME.fullmatch(artifact_name):
            raise ConfigurationError("Playwright MCP 截图名称无效")
        if "browser_take_screenshot" not in self._tools:
            raise ExecutionError("Playwright MCP 当前版本未提供截图工具")
        path = (self.work_dir / f"{artifact_name}.png").resolve()
        try:
            path.relative_to(self.work_dir.resolve())
        except ValueError as exc:
            raise ConfigurationError("Playwright MCP 截图路径越界") from exc
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ExecutionError("Playwright MCP 无法准备截图文件") from exc
        result = self._request(
            "tools/call",
            {
                "name": "browser_take_screenshot",
                "arguments": {
                    "filename": path.name,
                    "type": "png",
                    "fullPage": False,
                    "scale": "css",
                },
            },
        )
        if result.get("isError") is True:
            raise ExecutionError("Playwright MCP 截图失败")
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                signature = handle.read(len(_PNG_SIGNATURE))
        except OSError as exc:
            raise ExecutionError("Playwright MCP 未生成截图文件") from exc
        if not 0 < size <= MAX_SCREENSHOT_BYTES or signature != _PNG_SIGNATURE:
            path.unlink(missing_ok=True)
            raise ExecutionError("Playwright MCP 截图未通过本地安全校验")
        return path

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                if "browser_close" in self._tools:
                    self.call_tool("browser_close", {})
            except (ConfigurationError, ExecutionError):
                pass
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._process = None

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if len(line.encode("utf-8")) > MAX_MCP_MESSAGE_BYTES:
                self._messages.put(ExecutionError("Playwright MCP 响应超过 1MB"))
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                self._messages.put(value)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            if len(self._stderr) < 20:
                self._stderr.append(line.strip()[:500])

    def _send(self, value: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise ExecutionError("Playwright MCP 进程不可用")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_MCP_MESSAGE_BYTES:
            raise ConfigurationError("Playwright MCP 请求超过 1MB")
        try:
            process.stdin.write(encoded + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ExecutionError("Playwright MCP stdio 写入失败") from exc

    def _request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params),
            }
        )
        while True:
            try:
                message = self._messages.get(timeout=self.request_timeout_seconds)
            except queue.Empty:
                raise ExecutionError("Playwright MCP 响应超时") from None
            if isinstance(message, Exception):
                raise message
            if not isinstance(message, Mapping) or message.get("id") != request_id:
                continue
            if "error" in message:
                raise ExecutionError("Playwright MCP 返回协议错误")
            result = message.get("result")
            if not isinstance(result, Mapping):
                raise ExecutionError("Playwright MCP JSON-RPC result 无效")
            return result

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": dict(params)})
