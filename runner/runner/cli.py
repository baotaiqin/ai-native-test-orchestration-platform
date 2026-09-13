"""Windows Runner 前台 CLI。"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from threading import Event

from runner.broker import PikaBroker, RabbitMqConfig, is_transient_rabbitmq_error
from runner.config import RunnerConfig, default_state_dir, default_tags
from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.errors import (
    AuthenticationError,
    RegistrationPersistenceError,
    RunnerError,
)
from runner.executors.api import ApiExecutor
from runner.isolation import spawn_api_task_process, spawn_performance_task_process
from runner.lifecycle import HeartbeatService, heartbeat_loop
from runner.models import RunnerIdentity
from runner.performance_store import PerformanceResultStore
from runner.protocol import RunnerClient
from runner.redaction import redact_text
from runner.slots import SlotState
from runner.snapshot import collect_snapshot
from runner.state import RunnerStateStore
from runner.transport import HttpxTransport
from runner.worker import (
    RabbitMqReconnectPolicy,
    RunnerWorker,
    SignalBoundary,
    WorkerResources,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test-platform-runner",
        description="AI 原生智能测试平台 Windows Runner 注册与心跳客户端",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=default_state_dir(),
        help="本地配置和加密身份目录（默认使用当前用户应用数据目录）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="只输出脱敏后的本机环境快照")
    inspect_parser.add_argument("--name", default=None, help="快照中的 Runner 名称")
    inspect_parser.add_argument(
        "--tag", action="append", default=None, help="Runner tag，可重复指定"
    )

    register_parser = subparsers.add_parser("register", help="使用一次性 Token 注册 Runner")
    register_parser.add_argument("--backend-url", required=True, help="FastAPI 后端基础地址")
    register_parser.add_argument("--name", required=True, help="Runner 名称")
    register_parser.add_argument(
        "--tag", action="append", default=None, help="Runner tag，可重复指定"
    )
    _add_slot_arguments(register_parser)
    _add_http_arguments(register_parser)

    doctor_parser = subparsers.add_parser(
        "doctor", help="只检查状态目录原子写和当前用户 DPAPI 往返"
    )
    heartbeat_parser = subparsers.add_parser("heartbeat-once", help="发送一次心跳，便于验收")
    run_parser = subparsers.add_parser("run", help="加载本地身份并持续运行可停止的心跳")
    consume_parser = subparsers.add_parser(
        "consume-once",
        help="从 RabbitMQ 消费一条任务并执行 claim（不执行任务）",
        description=(
            "从 RabbitMQ 消费一条任务并执行 claim；本入口不执行 Task、不上报 RUNNING/SUCCESS。"
        ),
    )
    consume_parser.add_argument(
        "--rabbitmq-url",
        default=None,
        help=(
            "RabbitMQ URL；未指定时读取 AI_TEST_RABBITMQ_URL"
            "（命令行可能暴露凭据，优先使用环境变量）"
        ),
    )
    execute_parser = subparsers.add_parser(
        "execute-once",
        help="消费一条 API_CASE、WEB_CASE 或 WEB_RECORDING 并完成一次执行闭环",
        description=(
            "消费一条 API_CASE、WEB_CASE 或 WEB_RECORDING，依次 claim、获取执行计划、"
            "开始、执行和完成上报；"
            "只有完成上报成功才确认消息。"
        ),
    )
    execute_parser.add_argument(
        "--rabbitmq-url",
        default=None,
        help=(
            "RabbitMQ URL；未指定时读取 AI_TEST_RABBITMQ_URL"
            "（命令行可能暴露凭据，优先使用环境变量）"
        ),
    )
    worker_parser = subparsers.add_parser(
        "worker",
        help="前台持续心跳并消费执行 API_CASE（Ctrl+C 安全停止）",
        description=(
            "前台运行 Runner：先发送一次心跳，再配置 RabbitMQ topology，"
            "持续消费并执行 API_CASE；不是 Windows Service。"
        ),
    )
    worker_parser.add_argument(
        "--rabbitmq-url",
        default=None,
        help=(
            "RabbitMQ URL；未指定时读取 AI_TEST_RABBITMQ_URL"
            "（命令行可能暴露凭据，优先使用环境变量）"
        ),
    )
    _add_slot_arguments(worker_parser, suppress_defaults=True)
    worker_parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="空队列轮询间隔（秒，默认 1.0）",
    )
    worker_parser.add_argument(
        "--rabbitmq-reconnect-base",
        type=float,
        default=1.0,
        help="RabbitMQ 重连初始退避（秒，默认 1.0）",
    )
    worker_parser.add_argument(
        "--rabbitmq-reconnect-max",
        type=float,
        default=30.0,
        help="RabbitMQ 重连退避上限（秒，默认 30.0）",
    )
    worker_parser.add_argument(
        "--force-stop-poll-seconds",
        type=float,
        default=1.0,
        help="执行期间 Force Stop 检查间隔（秒，默认 1.0）",
    )
    for command_parser in (
        inspect_parser,
        register_parser,
        doctor_parser,
        heartbeat_parser,
        run_parser,
        consume_parser,
        execute_parser,
        worker_parser,
    ):
        command_parser.add_argument(
            "--state-dir",
            type=Path,
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )
    return parser


def _add_slot_arguments(
    parser: argparse.ArgumentParser, *, suppress_defaults: bool = False
) -> None:
    defaults: tuple[object, object, object]
    if suppress_defaults:
        defaults = (argparse.SUPPRESS, argparse.SUPPRESS, argparse.SUPPRESS)
    else:
        defaults = (1, 0, 0)
    parser.add_argument("--api-slots", type=int, default=defaults[0])
    parser.add_argument("--web-slots", type=int, default=defaults[1])
    parser.add_argument("--performance-slots", type=int, default=defaults[2])


def _add_http_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--read-timeout", type=float, default=15.0)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--backoff-base", type=float, default=0.5)
    parser.add_argument("--backoff-max", type=float, default=8.0)


def _snapshot_for_config(
    config: RunnerConfig,
    slot_state: SlotState | None = None,
) -> dict[str, object]:
    return collect_snapshot(
        config.name,
        tags=config.tags,
        api_slots=config.api_slots,
        web_slots=config.web_slots,
        performance_slots=config.performance_slots,
        available_slots=slot_state.available() if slot_state is not None else None,
    )


def _client_for_config(config: RunnerConfig, transport: object) -> RunnerClient:
    return RunnerClient(
        config.backend_url,
        transport,
        connect_timeout_seconds=config.connect_timeout_seconds,
        read_timeout_seconds=config.read_timeout_seconds,
        max_attempts=config.max_attempts,
        backoff_base_seconds=config.backoff_base_seconds,
        backoff_max_seconds=config.backoff_max_seconds,
    )


def _register(
    args: argparse.Namespace,
    *,
    protector: object | None = None,
    transport: object | None = None,
    get_token: Callable[[], str] | None = None,
    output: Callable[[str], None] = print,
) -> int:
    tags = tuple(args.tag) if args.tag is not None else default_tags()
    config = RunnerConfig(
        backend_url=args.backend_url,
        name=args.name,
        tags=tags,
        api_slots=args.api_slots,
        web_slots=args.web_slots,
        performance_slots=args.performance_slots,
        connect_timeout_seconds=args.connect_timeout,
        read_timeout_seconds=args.read_timeout,
        max_attempts=args.max_attempts,
        backoff_base_seconds=args.backoff_base,
        backoff_max_seconds=args.backoff_max,
    )
    store = RunnerStateStore(args.state_dir, protector=protector)
    # 先落非敏感配置并完成 DPAPI/原子写预检，再读取并消费一次性 Token。
    store.save_config(config)
    if store.load_config() != config:
        raise RunnerError("非敏感 Runner 配置保存验证失败，未发送注册请求")
    store.preflight()
    registration_token = (get_token or (lambda: getpass.getpass("Registration token: ")))()
    if not registration_token:
        raise AuthenticationError("Registration Token 不能为空")
    active_transport = transport or HttpxTransport()
    try:
        result = _client_for_config(config, active_transport).register(
            _snapshot_for_config(config), registration_token
        )
        identity = RunnerIdentity(result.runner_id, result.credential)
        try:
            store.save_config(config)
            store.save_identity(identity)
        except Exception as exc:
            raise RegistrationPersistenceError(
                "服务端注册已成功，但本地身份未能安全保存；请勿重试同一 Token，"
                "请先让管理员撤销该 Runner，再重新生成 Registration Token。"
            ) from exc
    finally:
        _close_transport(active_transport)
    output(
        json.dumps(
            {
                "runner_id": result.runner_id,
                "heartbeat_interval_seconds": result.heartbeat_interval_seconds,
                "state_dir": str(args.state_dir),
                "credential_saved": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _heartbeat_once(
    args: argparse.Namespace,
    *,
    protector: object | None = None,
    transport: object | None = None,
    output: Callable[[str], None] = print,
) -> int:
    store = RunnerStateStore(args.state_dir, protector=protector)
    config = store.load_config()
    identity = store.load_identity()
    active_transport = transport or HttpxTransport()
    try:
        result = _client_for_config(config, active_transport).heartbeat(
            identity, _snapshot_for_config(config)
        )
    finally:
        _close_transport(active_transport)
    output(
        json.dumps(
            {
                "runner_id": result.runner_id,
                "status": result.status,
                "heartbeat_interval_seconds": result.heartbeat_interval_seconds,
                "server_time": result.server_time.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _run(
    args: argparse.Namespace,
    *,
    protector: object | None = None,
    transport: object | None = None,
    output: Callable[[str], None] = print,
) -> int:
    store = RunnerStateStore(args.state_dir, protector=protector)
    config = store.load_config()
    identity = store.load_identity()
    active_transport = transport or HttpxTransport()
    stop_event = Event()
    try:
        client = _client_for_config(config, active_transport)
        result = heartbeat_loop(
            client,
            config,
            identity,
            lambda: _snapshot_for_config(config),
            stop_event,
        )
    except KeyboardInterrupt:
        stop_event.set()
        output("收到停止信号，Runner 已安全停止。")
        return 0
    finally:
        _close_transport(active_transport)
    output(f"Runner 心跳已停止，共发送 {result.cycles} 次。")
    return 0


def _consume_once(
    args: argparse.Namespace,
    *,
    protector: object | None = None,
    output: Callable[[str], None] = print,
) -> int:
    store = RunnerStateStore(args.state_dir, protector=protector)
    config = store.load_config()
    identity = store.load_identity()
    rabbit_config = RabbitMqConfig.from_environment(args.rabbitmq_url)
    broker = PikaBroker(rabbit_config)
    connection = broker.connect()
    active_transport = HttpxTransport()
    try:
        consumer = RunnerTaskConsumer(
            connection.channel(),
            _client_for_config(config, active_transport),
            identity,
            {
                "API": config.api_slots,
                "WEB": config.web_slots,
                "PERFORMANCE": config.performance_slots,
            },
        )
        outcome = consumer.consume_once()
        output(
            json.dumps(
                {
                    "queue": consumer.queue,
                    "outcome": outcome.value,
                    "claim_only": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _close_transport(active_transport)
        _close_resource(connection)
    return 2 if outcome is ConsumeOutcome.STOPPED_AUTH else 0


def _execute_once(
    args: argparse.Namespace,
    *,
    protector: object | None = None,
    output: Callable[[str], None] = print,
) -> int:
    store = RunnerStateStore(args.state_dir, protector=protector)
    config = store.load_config()
    identity = store.load_identity()
    rabbit_config = RabbitMqConfig.from_environment(args.rabbitmq_url)
    broker = PikaBroker(rabbit_config)
    connection = broker.connect()
    active_transport = HttpxTransport()
    executor = ApiExecutor()
    try:
        consumer = RunnerTaskConsumer(
            connection.channel(),
            _client_for_config(config, active_transport),
            identity,
            {
                "API": config.api_slots,
                "WEB": config.web_slots,
                "PERFORMANCE": config.performance_slots,
            },
            executor=executor,
            performance_isolation_factory=spawn_performance_task_process,
            performance_result_store=PerformanceResultStore(
                args.state_dir / "performance-results"
            ),
        )
        outcome = consumer.execute_once()
        output(
            json.dumps(
                {
                    "queue": consumer.queue,
                    "outcome": outcome.value,
                    "execution": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        executor.close()
        _close_transport(active_transport)
        _close_resource(connection)
    return 2 if outcome is ConsumeOutcome.STOPPED_AUTH else 0


def _worker(
    args: argparse.Namespace,
    *,
    protector: object | None = None,
    output: Callable[[str], None] = print,
    broker: object | None = None,
    connection: object | None = None,
    heartbeat_transport: object | None = None,
    task_transport: object | None = None,
    executor: object | None = None,
    reconnect_waiter: Callable[[float], bool] | None = None,
) -> int:
    """组装前台 Worker 依赖；实际生命周期由 RunnerWorker 管理。"""

    store = RunnerStateStore(args.state_dir, protector=protector)
    config = store.load_config()
    slot_overrides = {
        field_name: getattr(args, field_name)
        for field_name in ("api_slots", "web_slots", "performance_slots")
        if hasattr(args, field_name)
    }
    if slot_overrides:
        config = replace(config, **slot_overrides)
    identity = store.load_identity()
    rabbit_config = RabbitMqConfig.from_environment(args.rabbitmq_url)
    active_broker = broker if broker is not None else PikaBroker(rabbit_config)
    active_heartbeat_transport = (
        heartbeat_transport if heartbeat_transport is not None else HttpxTransport()
    )
    active_task_transport = task_transport if task_transport is not None else HttpxTransport()
    active_executor = executor if executor is not None else ApiExecutor()
    active_connection = connection
    stop_event = Event()
    reconnect_policy = RabbitMqReconnectPolicy(
        initial_delay_seconds=args.rabbitmq_reconnect_base,
        max_delay_seconds=args.rabbitmq_reconnect_max,
    )
    active_reconnect_waiter = reconnect_waiter or stop_event.wait
    resources = WorkerResources(
        executor=active_executor,
        heartbeat_transport=active_heartbeat_transport,
        task_transport=active_task_transport,
        connection=None,
    )
    worker: RunnerWorker | None = None
    signal_boundary = SignalBoundary(stop_event)
    try:
        signal_boundary.install()
        if active_connection is None:
            active_connection = _connect_broker_with_retry(
                active_broker,
                stop_event,
                reconnect_policy,
                active_reconnect_waiter,
            )
        if active_connection is None:
            return 0
        resources = WorkerResources(
            executor=active_executor,
            heartbeat_transport=active_heartbeat_transport,
            task_transport=active_task_transport,
            connection=active_connection,
        )
        slots = {
            "API": config.api_slots,
            "WEB": config.web_slots,
            "PERFORMANCE": config.performance_slots,
        }
        slot_state = SlotState(slots)
        heartbeat_client = _client_for_config(config, active_heartbeat_transport)
        task_client = _client_for_config(config, active_task_transport)

        def consumer_factory(rabbit_connection: object) -> RunnerTaskConsumer:
            return RunnerTaskConsumer(
                rabbit_connection.channel(),
                task_client,
                identity,
                slots,
                executor=active_executor,  # type: ignore[arg-type]
                slot_state=slot_state,
                isolation_factory=spawn_api_task_process,
                performance_isolation_factory=spawn_performance_task_process,
                performance_result_store=PerformanceResultStore(
                    args.state_dir / "performance-results"
                ),
                force_stop_poll_seconds=getattr(args, "force_stop_poll_seconds", 1.0),
            )

        consumer = consumer_factory(active_connection)
        heartbeat_service = HeartbeatService(
            heartbeat_client,
            config,
            identity,
            lambda: _snapshot_for_config(config, slot_state),
            stop_event,
        )
        slot_state.set_on_change(heartbeat_service.request_immediate_heartbeat)
        worker = RunnerWorker(
            consumer,
            heartbeat_service,
            stop_event,
            resources,
            poll_interval_seconds=args.poll_interval,
            connection_factory=(
                getattr(active_broker, "connect", None)
                if callable(getattr(active_broker, "connect", None))
                else None
            ),
            consumer_factory=consumer_factory,
            reconnect_policy=reconnect_policy,
            reconnect_waiter=active_reconnect_waiter,
        )
        result = worker.run()
        output(json.dumps(result.to_mapping(), ensure_ascii=False, indent=2))
        return result.exit_code
    finally:
        signal_boundary.restore()
        if worker is not None:
            worker.close()
        else:
            _close_worker_resources(resources)


def _connect_broker_with_retry(
    broker: object,
    stop_event: Event,
    reconnect_policy: RabbitMqReconnectPolicy,
    waiter: Callable[[float], bool],
) -> object | None:
    """Keep the foreground worker alive while RabbitMQ is temporarily unavailable."""

    connect = getattr(broker, "connect", None)
    if not callable(connect):
        raise RunnerError("RabbitMQ broker 缺少连接方法")
    failure_count = 0
    while not stop_event.is_set():
        try:
            return connect()
        except Exception as exc:
            if not is_transient_rabbitmq_error(exc):
                raise
            delay = reconnect_policy.delay_for(failure_count)
            failure_count += 1
            if waiter(delay) or stop_event.is_set():
                return None
    return None


def _inspect(args: argparse.Namespace, *, output: Callable[[str], None] = print) -> int:
    name = args.name or "runner"
    tags = tuple(args.tag) if args.tag is not None else default_tags()
    snapshot = collect_snapshot(name, tags=tags)
    output(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


def _doctor(
    args: argparse.Namespace,
    *,
    protector: object | None = None,
    output: Callable[[str], None] = print,
) -> int:
    report: dict[str, object] = {"ok": False, "state_dir": str(args.state_dir)}
    try:
        store = RunnerStateStore(args.state_dir, protector=protector)
        report.update(store.preflight_report())
    except RunnerError as exc:
        # 预期的 DPAPI 初始化失败会由 preflight_report 独立记录；这里仅
        # 兜底处理状态目录等构造阶段错误，并保持 doctor 输出为脱敏 JSON。
        report["error"] = redact_text(exc)
    report["ok"] = report.get("ok") is True
    output(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def _close_transport(transport: object) -> None:
    close = getattr(transport, "close", None)
    if callable(close):
        close()


def _close_resource(resource: object) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def _close_worker_resources(resources: WorkerResources) -> None:
    closed: set[int] = set()
    for resource in (
        resources.executor,
        resources.heartbeat_transport,
        resources.task_transport,
        resources.connection,
    ):
        if resource is None or id(resource) in closed:
            continue
        closed.add(id(resource))
        try:
            _close_resource(resource)
        except Exception:
            continue


def main(
    argv: Sequence[str] | None = None,
    *,
    protector: object | None = None,
    transport: object | None = None,
    get_token: Callable[[], str] | None = None,
    output: Callable[[str], None] = print,
    error: Callable[[str], None] = lambda message: print(message, file=sys.stderr),
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            return _inspect(args, output=output)
        if args.command == "register":
            return _register(
                args,
                protector=protector,
                transport=transport,
                get_token=get_token,
                output=output,
            )
        if args.command == "doctor":
            return _doctor(args, protector=protector, output=output)
        if args.command == "heartbeat-once":
            return _heartbeat_once(args, protector=protector, transport=transport, output=output)
        if args.command == "run":
            return _run(args, protector=protector, transport=transport, output=output)
        if args.command == "consume-once":
            return _consume_once(args, protector=protector, output=output)
        if args.command == "execute-once":
            return _execute_once(args, protector=protector, output=output)
        if args.command == "worker":
            return _worker(args, protector=protector, output=output)
    except RunnerError as exc:
        error(redact_text(exc))
        return 2
    except Exception as exc:
        # CLI 不输出 traceback，避免第三方异常把请求头或响应中的凭证带出。
        error(f"Runner 未处理错误：{redact_text(type(exc).__name__)}")
        return 2
    return 2
