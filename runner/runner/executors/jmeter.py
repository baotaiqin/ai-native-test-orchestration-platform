"""Minimal, bounded JMeter CLI adapter for ordinary HTTP performance targets."""

# ruff: noqa: E501 -- XML fragments intentionally mirror JMeter element names.

from __future__ import annotations

import base64
import csv
import json
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import escape, quoteattr

from runner.errors import ExecutionError
from runner.executors.api import ApiRequestTemplate


@dataclass(frozen=True)
class JMeterExecutionResult:
    wall_duration_ms: int
    samples: tuple[Mapping[str, Any], ...]


class JMeterExecutor:
    def __init__(
        self,
        *,
        executable_finder: Callable[[str], str | None] = shutil.which,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._find = executable_finder
        self._run = process_runner
        self._clock = clock

    def execute(
        self,
        request: ApiRequestTemplate,
        *,
        concurrency: int,
        iterations: int,
        warmup_iterations: int,
        load_mode: str,
        target_rps: float | None,
        request_timeout_ms: int,
        total_timeout_ms: int,
    ) -> JMeterExecutionResult:
        executable = self._find("jmeter") or self._find("jmeter.bat")
        if executable is None:
            raise ExecutionError("Runner 未安装 JMeter", error_type="JMETER_UNAVAILABLE")
        if load_mode not in {"FIXED_ITERATIONS", "FIXED_RPS"}:
            raise ExecutionError(
                "JMeter 当前仅支持固定迭代和固定 RPS", error_type="JMETER_LOAD_UNSUPPORTED"
            )
        with TemporaryDirectory(prefix="runner-jmeter-") as directory:
            root = Path(directory)
            plan_path = root / "plan.jmx"
            result_path = root / "result.jtl"
            plan_path.write_text(
                build_jmx(
                    request,
                    concurrency=concurrency,
                    iterations=iterations,
                    warmup_iterations=warmup_iterations,
                    target_rps=target_rps if load_mode == "FIXED_RPS" else None,
                    request_timeout_ms=request_timeout_ms,
                ),
                encoding="utf-8",
            )
            started = self._clock()
            try:
                completed = self._run(
                    [
                        executable,
                        "-n",
                        "-t",
                        str(plan_path),
                        "-l",
                        str(result_path),
                        "-Jjmeter.save.saveservice.output_format=csv",
                        "-Jjmeter.save.saveservice.print_field_names=true",
                        "-Jjmeter.save.saveservice.default_delimiter=,",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=max(1, total_timeout_ms / 1000),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ExecutionError("JMeter 执行超时", error_type="TOTAL_TIMEOUT") from exc
            except OSError as exc:
                raise ExecutionError("JMeter 无法启动", error_type="JMETER_UNAVAILABLE") from exc
            if completed.returncode != 0 or not result_path.exists():
                raise ExecutionError("JMeter 执行失败", error_type="JMETER_EXECUTION_FAILED")
            samples = _read_jtl(result_path, expected=iterations)
            if len(samples) != iterations:
                raise ExecutionError("JMeter 样本数不完整", error_type="JMETER_RESULT_INVALID")
            return JMeterExecutionResult(
                wall_duration_ms=max(1, int((self._clock() - started) * 1000)),
                samples=tuple(samples),
            )


def build_jmx(
    request: ApiRequestTemplate,
    *,
    concurrency: int,
    iterations: int,
    warmup_iterations: int,
    target_rps: float | None,
    request_timeout_ms: int,
) -> str:
    split = urlsplit(request.url)
    if split.scheme not in {"http", "https"} or not split.hostname:
        raise ExecutionError("JMeter 目标 URL 无效", error_type="REQUEST_INVALID")
    query = list(parse_qsl(split.query, keep_blank_values=True))
    query.extend((item.name, item.value) for item in request.query_params if item.enabled)
    headers = {item.name: item.value for item in request.headers if item.enabled}
    cookies = {item.name: item.value for item in request.cookies if item.enabled}
    _apply_auth(request, headers, query)
    url_path = urlunsplit(("", "", split.path or "/", urlencode(query), ""))
    body = _body_text(request)
    groups = []
    if warmup_iterations:
        groups.append(
            _thread_group(
                "warmup",
                min(concurrency, warmup_iterations),
                warmup_iterations,
                request,
                split,
                url_path,
                headers,
                cookies,
                body,
                request_timeout_ms,
                None,
                setup=True,
            )
        )
    quotient, remainder = divmod(iterations, concurrency)
    if quotient:
        groups.append(
            _thread_group(
                "main",
                concurrency,
                quotient * concurrency,
                request,
                split,
                url_path,
                headers,
                cookies,
                body,
                request_timeout_ms,
                (
                    target_rps * (quotient * concurrency) / iterations
                    if target_rps is not None
                    else None
                ),
            )
        )
    if remainder:
        groups.append(
            _thread_group(
                "main-remainder",
                remainder,
                remainder,
                request,
                split,
                url_path,
                headers,
                cookies,
                body,
                request_timeout_ms,
                target_rps * remainder / iterations if target_rps is not None else None,
            )
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6">'
        '<hashTree><TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Performance" enabled="true">'
        '<boolProp name="TestPlan.serialize_threadgroups">false</boolProp>'
        "</TestPlan><hashTree>" + "".join(groups) + "</hashTree></hashTree></jmeterTestPlan>"
    )


def _thread_group(
    label: str,
    threads: int,
    total_iterations: int,
    request: ApiRequestTemplate,
    split: Any,
    path: str,
    headers: Mapping[str, str],
    cookies: Mapping[str, str],
    body: str | None,
    timeout_ms: int,
    target_rps: float | None,
    *,
    setup: bool = False,
) -> str:
    loops = max(1, total_iterations // threads)
    group_class = "SetupThreadGroup" if setup else "ThreadGroup"
    timer = ""
    if target_rps is not None:
        timer = (
            '<ConstantThroughputTimer guiclass="TestBeanGUI" testclass="ConstantThroughputTimer" '
            'testname="Target RPS" enabled="true">'
            f"<doubleProp><name>throughput</name><value>{target_rps * 60}</value>"
            "<savedValue>0.0</savedValue></doubleProp>"
            '<intProp name="calcMode">1</intProp></ConstantThroughputTimer><hashTree/>'
        )
    header_items = "".join(
        '<elementProp name="" elementType="Header"><stringProp name="Header.name">'
        f'{escape(name)}</stringProp><stringProp name="Header.value">{escape(value)}</stringProp>'
        "</elementProp>"
        for name, value in headers.items()
    )
    cookie_items = "".join(
        '<elementProp name="" elementType="Cookie"><stringProp name="Cookie.name">'
        f'{escape(name)}</stringProp><stringProp name="Cookie.value">{escape(value)}</stringProp>'
        f'<stringProp name="Cookie.domain">{escape(split.hostname or "")}</stringProp>'
        '<stringProp name="Cookie.path">/</stringProp><boolProp name="Cookie.secure">false</boolProp>'
        "</elementProp>"
        for name, value in cookies.items()
    )
    body_argument = ""
    post_raw = "false"
    if body is not None:
        post_raw = "true"
        body_argument = (
            '<elementProp name="" elementType="HTTPArgument"><boolProp name="HTTPArgument.always_encode">false</boolProp>'
            f'<stringProp name="Argument.value">{escape(body)}</stringProp>'
            '<stringProp name="Argument.metadata">=</stringProp></elementProp>'
        )
    port = split.port or (443 if split.scheme == "https" else 80)
    return (
        f'<{group_class} guiclass="ThreadGroupGui" testclass="{group_class}" testname={quoteattr(label)} enabled="true">'
        f'<stringProp name="ThreadGroup.num_threads">{threads}</stringProp>'
        '<stringProp name="ThreadGroup.ramp_time">0</stringProp>'
        '<elementProp name="ThreadGroup.main_controller" elementType="LoopController">'
        f'<stringProp name="LoopController.loops">{loops}</stringProp><boolProp name="LoopController.continue_forever">false</boolProp>'
        f"</elementProp></{group_class}><hashTree>{timer}"
        '<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Headers" enabled="true">'
        f'<collectionProp name="HeaderManager.headers">{header_items}</collectionProp></HeaderManager><hashTree/>'
        '<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="Cookies" enabled="true">'
        f'<collectionProp name="CookieManager.cookies">{cookie_items}</collectionProp></CookieManager><hashTree/>'
        '<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" '
        f'testname={quoteattr(label)} enabled="true">'
        '<elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments">'
        f'{body_argument}</collectionProp></elementProp><stringProp name="HTTPSampler.domain">{escape(split.hostname or "")}</stringProp>'
        f'<stringProp name="HTTPSampler.port">{port}</stringProp><stringProp name="HTTPSampler.protocol">{escape(split.scheme)}</stringProp>'
        f'<stringProp name="HTTPSampler.path">{escape(path)}</stringProp><stringProp name="HTTPSampler.method">{escape(request.method)}</stringProp>'
        f'<boolProp name="HTTPSampler.follow_redirects">{str(request.follow_redirects).lower()}</boolProp>'
        f'<boolProp name="HTTPSampler.postBodyRaw">{post_raw}</boolProp><stringProp name="HTTPSampler.connect_timeout">{timeout_ms}</stringProp>'
        f'<stringProp name="HTTPSampler.response_timeout">{timeout_ms}</stringProp></HTTPSamplerProxy><hashTree/>'
        "</hashTree>"
    )


def _apply_auth(
    request: ApiRequestTemplate, headers: dict[str, str], query: list[tuple[str, str]]
) -> None:
    auth = request.auth
    if auth.type == "BEARER" and auth.token:
        headers["Authorization"] = f"Bearer {auth.token}"
    elif auth.type == "BASIC" and auth.username is not None and auth.password is not None:
        encoded = base64.b64encode(f"{auth.username}:{auth.password}".encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    elif auth.type == "API_KEY" and auth.key_name and auth.key_value:
        if auth.placement == "QUERY":
            query.append((auth.key_name, auth.key_value))
        else:
            headers[auth.key_name] = auth.key_value


def _body_text(request: ApiRequestTemplate) -> str | None:
    body = request.body
    if body.type == "NONE":
        return None
    if body.type == "JSON":
        return json.dumps(body.content, ensure_ascii=False, separators=(",", ":"))
    if body.type == "FORM_URLENCODED" and isinstance(body.content, Mapping):
        return urlencode([(str(key), str(value)) for key, value in body.content.items()])
    if body.type == "RAW" and isinstance(body.content, str):
        return body.content
    raise ExecutionError("JMeter 不支持当前请求体", error_type="JMETER_BODY_UNSUPPORTED")


def _read_jtl(path: Path, *, expected: int) -> list[Mapping[str, Any]]:
    rows: list[dict[str, str]]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    main = [row for row in rows if not row.get("label", "").startswith("warmup")]
    if not main:
        return []
    timestamps = [int(row.get("timeStamp", "0")) for row in main]
    baseline = min(timestamps)
    samples: list[Mapping[str, Any]] = []
    for row, timestamp in zip(main[:expected], timestamps, strict=False):
        status_text = row.get("responseCode", "")
        status_code = int(status_text) if status_text.isdigit() else None
        success = row.get("success", "").lower() == "true" and status_code is not None
        sample: dict[str, Any] = {
            "duration_ms": max(0, int(row.get("elapsed", "0") or 0)),
            "bytes_received": max(0, int(row.get("bytes", "0") or 0)),
            "bytes_sent": max(0, int(row.get("sentBytes", "0") or 0)),
            "started_offset_ms": max(0, timestamp - baseline),
        }
        if success:
            sample["status_code"] = status_code
        else:
            sample["error_type"] = (
                f"HTTP_{status_code}" if status_code is not None else "JMETER_SAMPLE_FAILED"
            )
        samples.append(sample)
    return samples
