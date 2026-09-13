# V1 本地构建镜像与 Ubuntu 服务器部署手册

> 适用场景：在 Windows 开发机保留项目仓库，在本地构建 Linux Docker 镜像，只把发布包上传到 Ubuntu 服务器，再使用 Docker Compose 启动。  
> 当前迁移基线：`20260913_0077`。实际发布前始终以 `alembic heads` 的输出为准，不根据本文中的历史编号猜测。  
> 目标服务器参考：4 核 CPU、4GB 内存、40GB SSD。Windows Runner、Playwright、JMeter 和实际压力生成必须放在服务器外运行。

## 最短操作路径（推荐）

日常发布不需要逐条复制本手册中的底层命令。项目已经提供两个入口脚本：

- Windows 本地：`deploy/server/build-release.ps1`，构建 Linux 镜像、检查架构、校验 Compose、导出镜像并生成发布压缩包；
- Ubuntu 服务器：`deploy/server/deploy-release.sh`，校验发布包、加载镜像、执行数据库迁移、启动服务并检查健康状态。

在项目根目录执行本地构建：

```powershell
.\deploy\server\build-release.ps1 -ReleaseTag 20260913-0077
```

默认生成：

```text
deploy/server/.artifacts/ai-test-release-20260913-0077.tar.gz
deploy/server/.artifacts/ai-test-release-20260913-0077.tar.gz.sha256
```

这里需要区分“Docker 镜像”和“可复制的发布文件”：

- 构建出的 `ai-test-backend:<tag>`、`ai-test-frontend:<tag>`、`ai-test-demo:<tag>` 首先保存在 Docker Desktop 自己的镜像存储中，可以用 `docker image ls` 查看，但不能到 Windows 资源管理器中直接复制；
- 脚本随后把三个应用镜像导出为发布包内部的 `app-images.tar`，并最终生成上面的 `.tar.gz`；
- 实际传服务器时只复制 `.tar.gz` 和同名 `.sha256` 两个文件，不需要寻找或复制 Docker Desktop 的内部磁盘文件，也不上传源码目录。

在 Windows 资源管理器中可直接打开：

```text
<项目根目录>\deploy\server\.artifacts
```

服务器安装好 Docker，并已通过控制台文件管理把两个发布文件上传到 `/home/ubuntu/ai-test-upload` 后，执行：

```bash
cd /home/ubuntu/ai-test-upload
sha256sum -c ai-test-release-20260913-0077.tar.gz.sha256
sudo install -d -m 0750 /opt/ai-test/releases/20260913-0077
sudo tar -xzf ai-test-release-20260913-0077.tar.gz -C /opt/ai-test/releases/20260913-0077
cd /opt/ai-test/releases/20260913-0077
sudo bash ./deploy-release.sh first-install
```

首次执行会在 `/opt/ai-test/shared/production.env` 不存在时复制一份安全配置模板并主动停止。只需填写模板空值、创建模板指定的 Fernet 密钥文件，然后原样再执行一次最后一条命令。脚本不会把生产密钥打进发布包。

后续版本仍先在本地用新标签构建并上传、解压。确认云盘快照或应用数据备份可用后执行：

```bash
cd /opt/ai-test/releases/ai-test-release-20260920-0078
sudo bash ./deploy-release.sh upgrade --backup-confirmed
```

每个发布包自带 `release.env`，只保存 Backend、Frontend 和 Demo 镜像标签，不含密码。服务器上的 `production.env` 长期复用，因此升级时不再手工改应用镜像标签。

## 上线后的演示能力

平台中的“AI 主演示”管理页面属于正式应用，部署成功并以管理员登录后可以访问和初始化。独立的智能商城 Demo 也会构建为 `ai-test-demo:<release-tag>` 镜像并随发布 Compose 启动。

正式环境推荐为 Demo 配置独立 HTTPS 域名：

```dotenv
DEMO_BIND_ADDRESS=127.0.0.1
DEMO_PORT=8765
DEMO_PUBLIC_URL=https://demo.example.com
```

TLS 反向代理把 `demo.example.com` 转发到 `127.0.0.1:8765`。平台的“访问在线 Demo”按钮、自动创建的项目环境以及新导入的 OpenAPI 都使用 `DEMO_PUBLIC_URL`。客户可在新窗口访问 Demo，外置 Windows Runner 也必须能够解析并访问同一地址。

尚未配置域名和证书时，可在受控验收阶段暂用 `DEMO_BIND_ADDRESS=0.0.0.0` 与 `DEMO_PUBLIC_URL=http://服务器公网IP:8765`，并在云防火墙放行 8765。该方式没有 HTTPS，只适合短期验收，不作为正式对客方案。

## 1. 发布模式与边界

本流程采用“应用镜像本地构建、公共镜像服务器首次拉取”的半离线模式：

```text
Windows 本地源码
  -> 构建 linux/amd64 Backend、Frontend 与 Demo 镜像
  -> 导出 app-images.tar 并生成 SHA-256
  -> 上传不含密钥的发布压缩包
  -> Ubuntu 校验并执行 docker image load
  -> Compose 拉取 MySQL/Redis/RabbitMQ/MinIO/Prometheus
  -> 迁移、首次管理员初始化、启动和验收
```

服务器发布目录不包含 `.git`、`backend/`、`frontend/`、`runner/`、测试文件或本地备份。需要注意：当前 Backend 镜像会把 Python 应用文件复制进镜像；这表示服务器上没有可直接编辑的源码目录和 Git 仓库，但 Docker 镜像不是源码加密或知识产权保护机制。

服务器启动必须使用 `deploy/server/compose.release.yml`。该文件只引用已经加载的 Backend、Frontend 和 Demo 镜像，不包含 `build:`。不要在无源码服务器上使用面向现场构建的 `deploy/server/compose.yml`。

官方参考：

- [Docker Engine Ubuntu 安装](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose Linux 插件安装](https://docs.docker.com/compose/install/linux/)
- [Docker image save](https://docs.docker.com/reference/cli/docker/image/save/)
- [Docker image load](https://docs.docker.com/reference/cli/docker/image/load/)
- [生产环境使用 Compose](https://docs.docker.com/compose/how-tos/production/)

## 2. 发布包内容

每个发布包只包含以下文件：

```text
ai-test-release-<release-tag>/
|-- app-images.tar
|-- SHA256SUMS
|-- compose.release.yml
|-- prometheus.server.yml
|-- .env.server.example
|-- release.env
`-- deploy-release.sh
```

不得放入发布包：

- `.env`、`production.env` 或任何真实环境配置；
- JWT 签名密钥、Fernet 密钥、数据库密码、AI Provider 密钥；
- 管理员密码、Runner Credential 或 Registration Token；
- 项目源码、`.git`、IDE 配置、测试数据和 `.local-backups/`；
- 本地 MySQL、Redis、RabbitMQ、MinIO 或 Prometheus 数据。

## 3. 本地准备

### 3.1 确认服务器架构

先通过服务器控制台或 SSH 执行：

```bash
uname -m
```

本手册后续命令假定结果为 `x86_64`，对应 Docker 平台 `linux/amd64`。如果结果为 `aarch64`，必须把本地构建参数改为 `linux/arm64`，不能把 amd64 镜像直接部署到 arm64 服务器。

### 3.2 本地工具

Windows 本地需要：

- Docker Desktop，使用 Linux Containers；
- Docker Buildx；
- PowerShell 7 或 Windows PowerShell；
- Windows 自带的 `tar.exe`，用于生成最终压缩包；
- 足够的本地磁盘空间保存构建缓存、镜像和导出 tar。

检查：

```powershell
docker version
docker compose version
docker buildx version
tar.exe --version
```

如果 Docker Desktop 没有运行，先人工启动并确认 Linux Docker Engine 可用。

## 4. 本地手动构建

以下命令在项目根目录执行。版本标签必须唯一且不可变，不使用 `latest`。推荐采用“日期 + 数据库迁移尾号”，例如 `20260913-0077`。

```powershell
Set-Location "<项目根目录>"

$releaseTag = "20260913-0077"
$releaseRoot = "D:\ai-test-releases"
$releaseDirectory = Join-Path $releaseRoot "ai-test-release-$releaseTag"
$releaseArchive = Join-Path $releaseRoot "ai-test-release-$releaseTag.tar.gz"

if (Test-Path $releaseDirectory) {
  throw "发布目录已存在；请使用新的不可变版本标签，或先人工核对该目录"
}
New-Item -ItemType Directory -Path $releaseDirectory | Out-Null
```

### 4.1 可选的源码门禁

没有新代码变更并且已经有同一源码状态的通过证据时，不重复运行全量门禁。正式发布候选发生过代码变化时，至少执行：

```powershell
Push-Location backend
& "..\.venv\Scripts\python.exe" -m ruff check --no-cache app tests migrations
& "..\.venv\Scripts\python.exe" -m pytest
& "..\.venv\Scripts\python.exe" -m alembic heads
Pop-Location

Push-Location runner
& "..\.venv\Scripts\python.exe" -m ruff check --no-cache runner tests
& "..\.venv\Scripts\python.exe" -m pytest
Pop-Location

Push-Location frontend
npm ci --no-audit --no-fund
npm run type-check
npm run build
Pop-Location
```

`alembic heads` 必须只返回一个 head。当前预期为：

```text
20260913_0077 (head)
```

### 4.2 构建 Backend 镜像

```powershell
docker buildx build `
  --platform linux/amd64 `
  --file deploy/server/backend.Dockerfile `
  --tag "ai-test-backend:$releaseTag" `
  --load `
  .
```

### 4.3 构建 Frontend 镜像

```powershell
docker buildx build `
  --platform linux/amd64 `
  --file deploy/server/frontend.Dockerfile `
  --tag "ai-test-frontend:$releaseTag" `
  --load `
  .
```

### 4.4 构建 Demo 镜像

```powershell
docker buildx build `
  --platform linux/amd64 `
  --file deploy/server/demo.Dockerfile `
  --tag "ai-test-demo:$releaseTag" `
  --load `
  .
```

### 4.5 核对镜像平台和标签

```powershell
docker image inspect "ai-test-backend:$releaseTag" `
  --format '{{.Os}}/{{.Architecture}} {{.Id}}'

docker image inspect "ai-test-frontend:$releaseTag" `
  --format '{{.Os}}/{{.Architecture}} {{.Id}}'

docker image inspect "ai-test-demo:$releaseTag" `
  --format '{{.Os}}/{{.Architecture}} {{.Id}}'
```

三项平台都必须是 `linux/amd64`。

### 4.6 本地解析发布 Compose

先设置仅用于解析的非敏感占位值。不要在终端粘贴正式环境密钥：

```powershell
$env:BACKEND_IMAGE = "ai-test-backend:$releaseTag"
$env:FRONTEND_IMAGE = "ai-test-frontend:$releaseTag"
$env:DEMO_IMAGE = "ai-test-demo:$releaseTag"
$env:MYSQL_IMAGE = "mysql:8.4"
$env:REDIS_IMAGE = "redis:7.4-alpine"
$env:RABBITMQ_IMAGE = "rabbitmq:4.1-management-alpine"
$env:MINIO_IMAGE = "填写经过审核的固定MinIO标签或digest"
$env:PROMETHEUS_IMAGE = "prom/prometheus:v2.55.1"
$env:MYSQL_DATABASE = "ai_test_platform"
$env:MYSQL_USER = "validation"
$env:MYSQL_PASSWORD = "validation-only"
$env:MYSQL_ROOT_PASSWORD = "validation-only"
$env:APP_DATABASE_URL = "mysql+pymysql://validation:validation-only@mysql:3306/ai_test_platform?charset=utf8mb4"
$env:REDIS_PASSWORD = "validation-only"
$env:APP_REDIS_URL = "redis://:validation-only@redis:6379/0"
$env:RABBITMQ_DEFAULT_USER = "validation"
$env:RABBITMQ_DEFAULT_PASS = "validation-only"
$env:APP_RABBITMQ_URL = "amqp://validation:validation-only@rabbitmq:5672/"
$env:MINIO_ROOT_USER = "validation"
$env:MINIO_ROOT_PASSWORD = "validation-only-secret"
$env:APP_SECRET_KEY = "validation-only-jwt-signing-secret"
$env:APP_SECRET_FERNET_KEY_FILE_HOST = "C:/validation/app-fernet.key"
$env:DEMO_PUBLIC_URL = "https://demo.example.invalid"
$env:APP_CORS_ORIGINS = "https://example.invalid"
$env:APP_DEV_ADMIN_PASSWORD = "validation-only-admin-password"

docker compose -f deploy/server/compose.release.yml config --quiet
```

成功时命令不输出错误。正式环境不得复用以上占位值。

## 5. 生成发布包

### 5.1 复制服务器运行文件

```powershell
Copy-Item deploy/server/compose.release.yml `
  (Join-Path $releaseDirectory "compose.release.yml")

Copy-Item deploy/prometheus/prometheus.server.yml `
  (Join-Path $releaseDirectory "prometheus.server.yml")

Copy-Item deploy/server/.env.server.example `
  (Join-Path $releaseDirectory ".env.server.example")

Copy-Item deploy/server/deploy-release.sh `
  (Join-Path $releaseDirectory "deploy-release.sh")

@(
  "RELEASE_TAG=$releaseTag",
  "BACKEND_IMAGE=ai-test-backend:$releaseTag",
  "FRONTEND_IMAGE=ai-test-frontend:$releaseTag",
  "DEMO_IMAGE=ai-test-demo:$releaseTag"
) | Set-Content `
  -Encoding ascii `
  (Join-Path $releaseDirectory "release.env")
```

### 5.2 导出应用镜像

```powershell
docker image save `
  --output (Join-Path $releaseDirectory "app-images.tar") `
  "ai-test-backend:$releaseTag" `
  "ai-test-frontend:$releaseTag" `
  "ai-test-demo:$releaseTag"
```

这里只导出 Backend、Frontend 和 Demo。MySQL、Redis、RabbitMQ、MinIO、Prometheus 由服务器首次拉取，避免每次上传完整中间件镜像。

### 5.3 生成校验和

```powershell
$checksumFiles = @(
  "app-images.tar",
  "compose.release.yml",
  "prometheus.server.yml",
  ".env.server.example",
  "release.env",
  "deploy-release.sh"
)

$checksumLines = foreach ($fileName in $checksumFiles) {
  $filePath = Join-Path $releaseDirectory $fileName
  $hash = (Get-FileHash $filePath -Algorithm SHA256).Hash.ToLowerInvariant()
  "$hash  $fileName"
}

$checksumLines | Set-Content `
  -Encoding ascii `
  (Join-Path $releaseDirectory "SHA256SUMS")
```

### 5.4 生成最终压缩包

确保 `$releaseArchive` 位于 `$releaseDirectory` 之外，然后执行：

```powershell
tar.exe -czf $releaseArchive -C $releaseDirectory .
Get-Item $releaseArchive | Select-Object FullName, Length, LastWriteTime
Get-FileHash $releaseArchive -Algorithm SHA256
```

记录最终压缩包 SHA-256。上传后还要在服务器核对一次。

## 6. 把发布文件复制到服务器

### 6.1 推荐的服务器暂存目录

发布文件先放在登录用户有写权限的暂存目录，不要直接通过文件管理器上传到 `/opt`。以服务器用户 `ubuntu` 为例，只需首次在服务器终端创建一次：

```bash
mkdir -p /home/ubuntu/ai-test-upload
chmod 750 /home/ubuntu/ai-test-upload
```

推荐的服务器目录用途如下：

```text
/home/ubuntu/ai-test-upload/             # 控制台上传的原始压缩包和 sha256
/opt/ai-test/releases/<release-tag>/     # 解压后的某个不可变版本
/opt/ai-test/shared/production.env       # 跨版本长期复用的正式配置
/opt/ai-test/shared/secrets/             # 跨版本长期复用的密钥文件
/opt/ai-test/current                     # 部署成功后指向当前版本的软链接
```

### 6.2 不使用上传命令：通过云控制台文件管理复制

本地构建脚本成功结束后：

1. 在 Windows 资源管理器打开项目的 `deploy\server\.artifacts`；
2. 找到同一版本的 `ai-test-release-<release-tag>.tar.gz` 和 `ai-test-release-<release-tag>.tar.gz.sha256`；
3. 打开云服务器控制台的“文件管理”；
4. 进入 `/home/ubuntu/ai-test-upload`；
5. 点击上传，把两个文件都上传到该目录；
6. 等待控制台显示上传完成，并核对文件名中的版本标签完全一致。

例如版本 `20260913-0077` 上传后应为：

```text
/home/ubuntu/ai-test-upload/ai-test-release-20260913-0077.tar.gz
/home/ubuntu/ai-test-upload/ai-test-release-20260913-0077.tar.gz.sha256
```

文件复制可以完全通过图形界面完成；但校验、解压、填写受保护配置和启动容器仍需在服务器终端执行。不要在本地解压后上传大量散文件。

### 6.3 上传后校验完整压缩包

```bash
cd /home/ubuntu/ai-test-upload
sha256sum -c ai-test-release-20260913-0077.tar.gz.sha256
```

必须显示：

```text
ai-test-release-20260913-0077.tar.gz: OK
```

如果显示 `FAILED`，删除服务器上这一份不完整文件并通过控制台重新上传，不要继续解压。

### 6.4 可选：使用 scp 上传

只有希望使用命令上传时才需要 `scp`。把 `<server-user>` 和 `<server-host>` 替换为实际 SSH 用户和地址：

```powershell
scp $releaseArchive "$releaseArchive.sha256" `
  "<server-user>@<server-host>:/home/ubuntu/ai-test-upload/"
```

Fernet 密钥和正式 `production.env` 不得与发布包一起传输。密钥应按[《跨平台Secret配置说明》](./跨平台Secret配置说明.md)独立生成和传输，不在聊天、工单、截图、Shell 参数或项目目录中留下明文。

## 7. Ubuntu 从零安装 Docker

以下步骤来自 Docker 官方 Ubuntu 软件源方式。不要在正式服务器直接运行未经审查的一键安装脚本。

### 7.1 核对系统

```bash
cat /etc/os-release
uname -m
df -h /
free -h
```

确认系统是 Docker 官方支持的 64 位 Ubuntu，架构与本地构建平台一致，并确保系统盘有足够空间。

### 7.2 安装 Docker Engine 与 Compose 插件

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt-get update
sudo apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
```

验证：

```bash
sudo systemctl status docker --no-pager
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
```

Docker 发布端口可能绕过部分 UFW 规则。公网部署必须同时检查云厂商安全组、主机防火墙和 Docker `DOCKER-USER` 链，不能仅凭 UFW 状态判断端口是否安全。

## 8. 创建服务器目录

把 `<release-tag>` 替换为实际标签：

```bash
export RELEASE_TAG="20260913-0077"

sudo install -d -m 0755 /opt/ai-test
sudo install -d -m 0755 /opt/ai-test/releases
sudo install -d -m 0750 /opt/ai-test/shared
sudo install -d -m 0750 /opt/ai-test/shared/secrets
sudo install -d -m 0750 "/opt/ai-test/releases/$RELEASE_TAG"
```

从控制台上传暂存目录解压发布包：

```bash
cd /home/ubuntu/ai-test-upload
sha256sum -c "ai-test-release-$RELEASE_TAG.tar.gz.sha256"

sudo tar -xzf "/home/ubuntu/ai-test-upload/ai-test-release-$RELEASE_TAG.tar.gz" \
  -C "/opt/ai-test/releases/$RELEASE_TAG"

cd "/opt/ai-test/releases/$RELEASE_TAG"
sudo sha256sum -c SHA256SUMS
```

外层校验确认上传的压缩包完整，内层校验确认发布包中的镜像和部署文件完整；两次校验都必须显示 `OK`。校验失败时停止部署，重新上传，不继续加载镜像。

核对解压结果：

```bash
sudo ls -lah "/opt/ai-test/releases/$RELEASE_TAG"
```

目录中必须包含 `app-images.tar`、`SHA256SUMS`、`compose.release.yml`、`prometheus.server.yml`、`.env.server.example`、`release.env` 和 `deploy-release.sh`。

## 9. 放置正式配置和 Fernet 密钥

### 9.1 首次创建配置

```bash
sudo install -m 0600 \
  "/opt/ai-test/releases/$RELEASE_TAG/.env.server.example" \
  /opt/ai-test/shared/production.env

sudoedit /opt/ai-test/shared/production.env
```

至少填写：

```dotenv
# 三个应用镜像标签由当前发布目录中的 release.env 提供，长期配置中保持为空。
BACKEND_IMAGE=
FRONTEND_IMAGE=
DEMO_IMAGE=

MYSQL_IMAGE=mysql:8.4
REDIS_IMAGE=redis:7.4-alpine
RABBITMQ_IMAGE=rabbitmq:4.1-management-alpine
MINIO_IMAGE=经过审核并固定的标签或digest
PROMETHEUS_IMAGE=prom/prometheus:v2.55.1

SERVER_BIND_ADDRESS=127.0.0.1
SERVER_HTTP_PORT=8080
RUNNER_BIND_ADDRESS=127.0.0.1
RUNNER_RABBITMQ_PORT=5672
PROMETHEUS_BIND_ADDRESS=127.0.0.1
PROMETHEUS_PORT=9090
PROMETHEUS_RETENTION=7d

DEMO_BIND_ADDRESS=127.0.0.1
DEMO_PORT=8765
DEMO_PUBLIC_URL=https://你的Demo域名

APP_SECRET_FERNET_KEY_FILE_HOST=/opt/ai-test/shared/secrets/app-fernet.key
APP_CORS_ORIGINS=https://你的正式域名

# 可选：简历/客户公开体验账号。两项同时填写或同时留空。
# 如需体验全部功能，可配置专用公开管理员，但不要复用唯一的私有管理员。
PUBLIC_DEMO_USERNAME=demo-admin
PUBLIC_DEMO_PASSWORD=你为公开Demo用户设置的密码
```

其余数据库、Redis、RabbitMQ、MinIO、JWT 和管理员字段也必须填写独立强随机值。数据库、Redis、RabbitMQ URL 中的用户名和密码必须与对应服务字段一致；特殊字符必须进行 URL 编码。

不要把三个应用镜像标签固化到共享 `production.env`，否则后续上传新版本后仍可能启动旧镜像。`deploy-release.sh` 会在每次发布时把当前目录的 `release.env` 作为后一份环境文件加载，从而选择本版本的 Backend、Frontend 和 Demo 镜像。

没有 TLS 入口时，首次内网验收可暂时把 `APP_CORS_ORIGINS` 设置为实际受控访问 origin。正式开放公网前必须改为精确 HTTPS origin。

### 9.2 放置 Fernet 密钥

按[《跨平台Secret配置说明》](./跨平台Secret配置说明.md)离线生成 Fernet Key Ring，通过独立受控通道上传，然后安装到固定路径：

```bash
sudo install -m 0400 -o 10001 -g 10001 \
  /受控临时路径/app-fernet.key \
  /opt/ai-test/shared/secrets/app-fernet.key
```

核对权限但不要输出文件内容：

```bash
sudo stat -c '%a %u:%g %n' \
  /opt/ai-test/shared/secrets/app-fernet.key
```

预期权限为 `400 10001:10001`。不要使用 `cat`、`head` 或日志命令显示密钥内容。

## 10. 首次部署

完成第 9 节的正式配置和 Fernet 密钥后，不需要手工逐个启动容器。进入当前发布目录，只执行发布脚本：

```bash
cd "/opt/ai-test/releases/$RELEASE_TAG"
sudo bash ./deploy-release.sh first-install
```

脚本会按固定顺序自动完成：

- 校验发布目录中的全部文件；
- 同时加载共享 `production.env` 和本版本 `release.env`；
- 校验 Compose 配置和 Fernet 密钥文件；
- 从 `app-images.tar` 加载 Backend、Frontend、Demo 镜像并核对架构；
- 拉取并启动 MySQL、Redis、RabbitMQ、MinIO；
- 执行 `alembic heads`、`upgrade head` 和 `current`；
- 仅在管理员不存在时初始化管理员；
- 启动 Backend、Frontend、Demo、Prometheus，并等待健康检查；
- 把 `/opt/ai-test/current` 更新为指向本次成功发布目录。

第一次拉取公共镜像时，在 3Mbps 服务器上可能持续较长时间，不要按 `Ctrl+C`，也不要同时启动第二个部署命令。脚本最后出现 `Deployment completed: <release-tag>` 才表示自动部署完成。

如果旧版 Windows 构建脚本生成的包在校验时出现类似 `'app-images.tar'$'\r': No such file or directory`，说明 `SHA256SUMS` 使用了 Windows CRLF 行尾。发布文件本身通常没有损坏；在当前解压目录执行下面两条命令修正清单后，再重新运行部署脚本：

```bash
sudo sed -i 's/\r$//' SHA256SUMS
sudo sha256sum -c SHA256SUMS
```

所有文件显示 `OK` 后才能继续。当前 `build-release.ps1` 已固定使用 LF 生成 `SHA256SUMS`，新构建的发布包不再需要这个兼容处理。

如果数据库迁移报 `ModuleNotFoundError: No module named 'asyncmy'`，说明 `production.env` 错把同步后端连接串写成了 `mysql+asyncmy://`。项目安装并使用的是 PyMySQL；保留用户名、密码、主机、端口和库名不变，只替换驱动前缀：

```bash
sudo sed -i \
  's#^APP_DATABASE_URL=mysql+asyncmy://#APP_DATABASE_URL=mysql+pymysql://#' \
  /opt/ai-test/shared/production.env
```

修正后重新运行 `sudo bash ./deploy-release.sh first-install`。当前发布脚本也会在启动容器前拒绝其他数据库驱动前缀。

如果此前没有按第 9 节手工创建 `production.env`，也可以直接先运行一次脚本。脚本会复制配置模板后主动停止；此时填写 `/opt/ai-test/shared/production.env`、放好 Fernet 密钥，再原样运行一次上述命令。

首次登录后立即通过平台修改初始化管理员密码，使 `APP_DEV_ADMIN_PASSWORD` 中的启动密码失效。首次生产部署使用独立空库，不导入本地开发库和历史验收备份，也不在生产库演练 downgrade。

### 10.1 配置简历公开体验账号（可选）

浏览器中的任何预填密码都能被访客读取。如果希望访客体验包括用户管理、Runner 注册、AI 主演示初始化和模型中心管理在内的全部功能，可以使用专门的公开管理员，但不要复用唯一的私有管理员账号。

配置步骤：

1. 先使用管理员登录平台，进入“用户管理”；
2. 创建用户，用户名建议为 `demo-admin`，显示名称可写“公开 Demo 管理员”；
3. 如需全部功能，平台角色选择“平台管理员（ADMIN）”，状态选择“启用”；只需普通体验时可选择“普通用户（USER）”；
4. 将同一用户名和密码写入 `/opt/ai-test/shared/production.env` 的 `PUBLIC_DEMO_USERNAME`、`PUBLIC_DEMO_PASSWORD`；
5. 部署包含公开 Demo 登录功能的新版本，或仅重建前端容器使配置生效。

这两个配置值会被前端有意发送到浏览器，性质上就是公开信息，不能复用私有管理员密码、数据库密码或其他系统密码。配置生效后，登录页会自动填入该账号，并显示“进入公开 Demo”；其他账号仍可覆盖表单内容登录。

公开管理员意味着任何访客都能修改用户、Runner、模型连接和平台数据，也可能修改公开账号自己的密码，造成简历入口暂时失效。至少保留一个不公开的私有管理员用于恢复，并确保演示环境不存放正式资产、真实客户数据、可用的生产密钥或高额度 AI 凭据。

如果暂时不需要公开体验，把这两项同时留空并重启前端容器，登录页就恢复为空白表单。若要更换公开密码，由于当前用户管理页不提供密码重置，应新建另一个普通用户、更新这两项配置并停用旧用户。

为下面的只读验证准备环境变量：

```bash
export COMPOSE_FILE="/opt/ai-test/releases/$RELEASE_TAG/compose.release.yml"
export COMPOSE_ENV="/opt/ai-test/shared/production.env"
export COMPOSE_RELEASE_ENV="/opt/ai-test/releases/$RELEASE_TAG/release.env"
```

## 11. 首次部署验证

### 11.1 前端入口

```bash
curl -fsS http://127.0.0.1:8080/healthz
```

预期返回：

```text
ok
```

### 11.2 Backend 容器健康

```bash
sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  exec -T backend \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read().decode())"
```

### 11.3 迁移与日志

```bash
sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  exec -T backend alembic current

sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  logs --tail=200 backend frontend mysql redis rabbitmq minio prometheus
```

不得只根据 `/health` 判断完整业务就绪。还要实际完成登录、项目列表、Runner 注册、一次 API 用例运行、Evidence 读取、报告查看和所需 AI Provider 调用。

### 11.4 尚未配置公网入口时访问

保持 `SERVER_BIND_ADDRESS=127.0.0.1`，在本地建立 SSH Tunnel：

```powershell
ssh -L 18080:127.0.0.1:8080 "<server-user>@<server-host>"
```

然后本地访问：

```text
http://127.0.0.1:18080
```

不要为了临时访问直接把 MySQL、Redis、MinIO、Prometheus 或 RabbitMQ 管理端口开放公网。

## 12. 公网入口与 Runner 网络

正式公网访问必须在 Compose 前增加 TLS 终止层，例如受维护的 Caddy、Nginx 或云负载均衡：

```text
Internet -> HTTPS 443 -> TLS 反向代理 -> 127.0.0.1:8080
```

只公开：

- `443/tcp`：正式 HTTPS；
- `80/tcp`：只用于证书签发或跳转 HTTPS；
- `22/tcp`：仅允许固定管理 IP。

RabbitMQ `5672` 不得直接开放公网。远程 Windows Runner 应通过受管内网、WireGuard/Tailscale 等 VPN 访问；如果无法提供私网链路，必须另行设计 AMQPS，不能把当前明文 5672 直接暴露在公网。

## 13. 后续版本更新

假设当前版本为 `20260913-0077`，新版本为 `20260920-0078`。

### 13.1 本地生成新发布包

重复第 4、5 节，使用新且不可变的标签：

```powershell
$releaseTag = "20260920-0078"
```

不要覆盖旧标签，不要使用 `latest`，不要删除上一版本镜像和发布包。

### 13.2 上传并创建新的服务器发布目录

可以继续使用第 6 节的控制台文件管理方式：把新版本的 `.tar.gz` 和 `.sha256` 上传到同一个 `/home/ubuntu/ai-test-upload`，不覆盖也不删除旧版本文件。也可以使用下面的可选命令上传：

```powershell
scp "D:\ai-test-releases\ai-test-release-20260920-0078.tar.gz" `
  "D:\ai-test-releases\ai-test-release-20260920-0078.tar.gz.sha256" `
  "<server-user>@<server-host>:/home/ubuntu/ai-test-upload/"
```

服务器执行：

```bash
export OLD_RELEASE_TAG="20260913-0077"
export RELEASE_TAG="20260920-0078"

cd /home/ubuntu/ai-test-upload
sha256sum -c "ai-test-release-$RELEASE_TAG.tar.gz.sha256"

sudo install -d -m 0750 "/opt/ai-test/releases/$RELEASE_TAG"
sudo tar -xzf "/home/ubuntu/ai-test-upload/ai-test-release-$RELEASE_TAG.tar.gz" \
  -C "/opt/ai-test/releases/$RELEASE_TAG"

cd "/opt/ai-test/releases/$RELEASE_TAG"
sudo sha256sum -c SHA256SUMS
sudo docker image load -i app-images.tar
```

### 13.3 核对版本镜像标签

新发布目录中的 `release.env` 已包含本版本 Backend、Frontend 和 Demo 镜像标签。不要修改共享的 `production.env`，也不要在普通版本更新中重新生成或替换 JWT/Fernet 密钥、数据库、Redis、RabbitMQ 或 MinIO 密码。

### 13.4 更新前备份

在停止写入后，至少保留：

- 云厂商系统盘快照；
- MySQL 一致性备份；
- MinIO Evidence 备份；
- `/opt/ai-test/shared/production.env` 的受控备份；
- Fernet Key Ring 的独立离线备份；
- 旧应用镜像标签、旧发布包和旧数据库 revision。

这些备份必须至少有一份位于本服务器之外。40GB 系统盘不能同时作为唯一生产数据和唯一备份位置。

### 13.5 维护窗口更新

```bash
export COMPOSE_FILE="/opt/ai-test/releases/$RELEASE_TAG/compose.release.yml"
export COMPOSE_ENV="/opt/ai-test/shared/production.env"
export COMPOSE_RELEASE_ENV="/opt/ai-test/releases/$RELEASE_TAG/release.env"

sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  config --quiet

sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  stop frontend backend

sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  run --rm backend alembic heads

sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  run --rm backend alembic upgrade head

sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  up -d backend frontend prometheus
```

随后重复第 11 节验证，并完成至少一条真实业务闭环。确认稳定后再结束维护窗口。

## 14. 更新失败与回退

### 14.1 仅应用镜像问题，数据库仍兼容

使用旧发布目录自带的 `release.env` 重新启动：

```bash
export RELEASE_TAG="20260913-0077"
export COMPOSE_FILE="/opt/ai-test/releases/$RELEASE_TAG/compose.release.yml"
export COMPOSE_ENV="/opt/ai-test/shared/production.env"
export COMPOSE_RELEASE_ENV="/opt/ai-test/releases/$RELEASE_TAG/release.env"

cd "/opt/ai-test/releases/$RELEASE_TAG"

sudo docker compose \
  --env-file "$COMPOSE_ENV" \
  --env-file "$COMPOSE_RELEASE_ENV" \
  -f "$COMPOSE_FILE" \
  up -d backend frontend prometheus
```

### 14.2 已执行不兼容数据库迁移

不要在生产库盲目执行 `alembic downgrade`。应停止写入，恢复与旧应用镜像对应并且已验证可恢复的数据库备份，然后再启动旧镜像。数据库恢复必须作为单独受控操作执行。

## 15. 4 核 4G、40GB 服务器约束

这台服务器只适合小团队、低并发和外置 Runner 的初期运行：

- 不在服务器执行 Docker build、npm build、pytest、Playwright 或 JMeter；
- Backend 保持单 Uvicorn worker；
- 建议配置 2–4GB swap/zram 作为 OOM 保护，但不能把 swap 当作可用内存；
- Prometheus 初始保留 7 天，并继续补充容量上限；
- 必须配置 Docker 日志轮转、磁盘使用率告警和容器资源限制；
- 监控 `/var/lib/docker`、MySQL、MinIO 和 Prometheus 的实际增长；
- 系统盘剩余空间低于安全线时停止发布，不执行无审查的全局镜像或数据卷清理；
- 正式长期运行建议升级到至少 4 核 8GB，或把 MySQL、对象存储、Prometheus 中的一部分迁移到外部服务。

当前 `compose.release.yml` 解决的是“无源码服务器加载镜像并启动”的交付方式，不代表 4G 资源限制、TLS、登录限流、完整就绪检查、告警和自动备份已经全部完成。对公网开放前仍须完成这些发布加固与真实 Linux 验收。

## 16. 每次发布检查清单

本地：

- [ ] 使用唯一、不可变版本标签；
- [ ] 代码门禁与迁移 head 符合发布要求；
- [ ] 两个镜像均为目标 Linux 架构；
- [ ] 发布 Compose 解析通过；
- [ ] 发布包不含源码、真实 `.env`、密钥和测试数据；
- [ ] SHA-256 已生成并单独记录。

服务器：

- [ ] 上传后 SHA-256 全部通过；
- [ ] 正式配置权限为 `0600`，Fernet 文件权限和 UID 正确；
- [ ] 镜像标签与 `production.env` 一致；
- [ ] 数据库、Evidence、配置和密钥已做服务器外备份；
- [ ] `alembic heads` 与 `alembic current` 一致；
- [ ] 全部容器启动且必要服务 healthy；
- [ ] 登录、项目、Runner、Run、Evidence、报告和 AI 路径按实际启用范围验证；
- [ ] 公网只暴露 HTTPS，RabbitMQ 不直接暴露公网；
- [ ] 上一版本发布包、镜像标签和数据库备份仍可用于回退。
