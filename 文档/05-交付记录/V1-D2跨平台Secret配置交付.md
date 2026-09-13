# V1-D2/r1 跨平台 Secret 配置交付

## 结论

V1-D2/r1 源码已实现，当前环境可执行的编译、Lint、静态合同、Windows DPAPI、Secret 直接回归与 A1 认证回归均通过。项目虚拟环境未安装本包新增并已声明的 `cryptography` 依赖，因此 Fernet 动态合成用例有 2 项按设计跳过；没有擅自安装或升级环境，也未把这些项目标记为已验收。

## 已实现

- 保留 `encrypt_secret(plaintext, application_key)` / `decrypt_secret(ciphertext, application_key)` 接口和 `dpapi:v1:` 历史密文。
- 新增 `fernet:v1:` Provider：使用独立密钥文件、MultiFernet 第一把写入/后续旧钥读取、随机化认证加密，以及内部版本、用途和 `application_key` 上下文绑定。
- DPAPI 密文仅在 Windows 读取；Windows Provider 切换后仍按前缀读取历史 DPAPI。非 Windows 在调用 DPAPI 前安全拒绝，不回退明文、不自动转换或重加密。
- Windows API 库只在实际 DPAPI 调用时载入，移除了 `ctypes.WinDLL` 即时注解障碍；Fernet 依赖也按需导入，当前 Windows DPAPI 开发不因依赖尚未安装而失效。
- 密钥文件限定为 ASCII、最多 4,096 字节、1–8 个唯一 Fernet 密钥；拒绝空白、空行、注释、BOM、错误 Base64、重复、超量和不可读文件。应用不生成、不写入、不记录密钥。
- 明文上限为 1,000,000 UTF-8 字节，覆盖现有 Secret、模型渠道 Key、512 KiB DOM 与 1,000,000 字节 Session 上限；JSON 载荷和密文也有考虑转义膨胀的独立上限。
- `app.main` 在启动后台协调器及记录启动成功前检查 Provider。Fernet 缺路径、文件错误或依赖不可用时不会进入健康运行态。
- `backend/pyproject.toml` 新增 `cryptography>=46.0,<51.0`，本轮未改变虚拟环境。

## 配置名

- `APP_SECRET_PROVIDER=dpapi|fernet`：Windows 开发默认 `dpapi`；生产必须显式选择。
- `APP_SECRET_FERNET_KEY_FILE=<独立只读挂载路径>`：仅提供路径，不把密钥内容放进环境变量。
- `APP_SECRET_KEY` 保持 A1 原语义，同时只作为新密文的应用上下文绑定输入；它不派生或替代 Fernet 密钥。

运维原则见 `文档/01-使用指南/跨平台Secret配置说明.md`，其中只描述安全生成、只读挂载、备份和轮换流程，不包含任何真实或通用密钥。

## 基本检查

- Python 内存编译：通过，4 个生产文件及 1 个专项测试文件。
- Ruff `--no-cache`：生产文件、专项测试和静态合同检查器全部通过。
- 静态合同检查：26/26 通过。
- 定向 Pytest：`test_secret_provider.py`、`test_secrets.py`、`test_auth.py` 合计 17 passed、2 skipped；唯一 warning 为既有 Starlette/httpx 弃用提示。
- Windows DPAPI 合成往返、错误上下文、切换 Provider 后的旧前缀读取、模拟非 Windows 拒绝、密钥文件错误、启动顺序、现有 Secret 与 A1 认证测试均已通过。
- 本机 `cryptography` 导入探测结果为 `ModuleNotFoundError`。2 个跳过用例覆盖实际 Fernet Unicode/边界/随机化以及错误密钥/上下文/篡改/轮换，不能视为已通过。

证据：`.codex-validation/v1-d2/static-contract-check.py` 与 `.codex-validation/v1-d2/basic-check-result.json`。

## 待集中验收

1. 在按 `backend/pyproject.toml` 安装依赖的干净 Python 3.12 环境执行被跳过的 Fernet 合成测试，并确认当前依赖范围可解析安装。
2. 在真实 Linux 容器验证导入、缺配置启动拒绝、有效只读挂载启动、非 root 权限与重启；模拟平台检查不能替代。
3. 隔离验证 Secret、模型渠道 Key、Session 和录制 DOM 的真实存取边界，以及异常、日志、repr 和配置展示不泄漏合成密钥/明文。
4. 验证 MultiFernet 新钥写入、旧钥读取、移除旧钥后的拒绝及原密文读取不变，并完成备份/恢复演练。
5. 正式密文转换、数据库更新、真实密钥生成、服务配置和部署均需后续明确任务；本包未读取或改写正式密钥、密文、数据库或服务。

## 范围说明

本包只改动明确分配的 core Provider/config/main、依赖声明、独立专项测试、独占说明和证据。未修改 auth、runs、web schema/web cases、共享测试夹具、Frontend、Runner、迁移、现有 `.env`、服务配置、正式数据或主控文档。
