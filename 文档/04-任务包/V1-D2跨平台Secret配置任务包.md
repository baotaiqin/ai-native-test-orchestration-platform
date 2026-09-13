# V1-D2/r1 跨平台 Secret Provider 与运行配置

主控明确分配AI-Test-DevOps_sol执行本包，Sol/xhigh。属于服务器部署环境依赖开发：实现文档/03-实施契约/V1跨平台Secret与部署实施契约.md的Provider部分。Backend W11只改web schema/runs局部，不改本包文件；A1已交付。不得因为D1尚未实机验收阻塞本包。

保留encrypt_secret(plaintext, application_key)/decrypt_secret(ciphertext, application_key)接口和Windows DPAPI历史兼容。新增显式FERNET Provider，独立只读挂载密钥文件，MultiFernet第一把加密、旧钥读取。独立版本前缀和被认证内部版本/用途/application_key上下文绑定；密钥不能由JWT签名字符串派生或默认值替代。旧DPAPI只Windows读取，Linux安全拒绝，不自动转换/重加密或回退明文。

密钥文件严格大小/数量/格式/重复校验，不自动生成，错误安全且不含密钥/明文。Provider设置和文件路径加入Settings但不要改变A1任何认证配置语义；Windows开发默认沿用DPAPI，生产显式选Provider，FERNET未配置或文件不可用时startup前拒绝。对Linux移除WinDLL即时注解和Windows导入初始化障碍，Windows库仅调用DPAPI时载入。核对Secret/ModelKey/Session/录制快照现有允许尺寸，输入有界但不缩减历史合法上限。不要执行正式密钥文件读取或转换。

明确范围：backend/app/core/secret_cipher.py及必要新内部Provider模块、core/config.py仅新增Provider配置校验、app/main.py仅启动前Provider配置检查、backend/pyproject.toml仅声明cryptography依赖（本机可用先核查，不能未经授权升级环境）；backend/tests新增Provider专项（不改共享conftest/auth_helpers/tests_runs/webcases）；独占文档/01-使用指南/跨平台Secret配置说明.md、.codex-validation/v1-d2/、文档/05-交付记录/V1-D2跨平台Secret配置交付.md。不改auth源码/接口/迁移、runs业务、Frontend/Runner、现有正式.env/服务/密文/数据库和主控文档。

基本检查仅编译/lint及合成临时密钥往返、错误密钥/上下文/篡改、轮换/缺文件、Windows合成DPAPI兼容；不运行全量或Docker/Linux实机，不把模拟平台称为Linux实测。密钥测试文件只放自有临时目录且finally清理，不写日志或报告。配置说明提供生成/只读挂载/备份/轮换原则，不包含真实或通用密钥，不让读文档触发命令执行。

完成后简短报告实现、配置名、检查、待验与阻塞并结束。不得跨任务消息/等待、Git、Claude、内部Agent或实际部署。