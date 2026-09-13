[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$')]
    [string]$ReleaseTag,

    [ValidateSet('linux/amd64', 'linux/arm64')]
    [string]$Platform = 'linux/amd64',

    [string]$OutputDirectory = (Join-Path $PSScriptRoot '.artifacts')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
    }
}

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command is unavailable: $Name"
    }
}

function Write-Utf8LfLines {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string[]]$Lines
    )

    $content = if ($Lines.Count -eq 0) { '' } else { ($Lines -join "`n") + "`n" }
    [System.IO.File]::WriteAllText(
        $Path,
        $content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$packageName = "ai-test-release-$ReleaseTag"
$packageDirectory = Join-Path $outputRoot $packageName
$archivePath = Join-Path $outputRoot "$packageName.tar.gz"
$archiveChecksumPath = "$archivePath.sha256"
$backendImage = "ai-test-backend:$ReleaseTag"
$frontendImage = "ai-test-frontend:$ReleaseTag"
$demoImage = "ai-test-demo:$ReleaseTag"

Assert-Command docker
Assert-Command tar.exe

Invoke-External docker @('version')
Invoke-External docker @('compose', 'version')
Invoke-External docker @('buildx', 'version')

if (Test-Path -LiteralPath $packageDirectory) {
    throw "Release directory already exists. Use a new immutable tag: $packageDirectory"
}
if (Test-Path -LiteralPath $archivePath) {
    throw "Release archive already exists. Use a new immutable tag: $archivePath"
}
if (Test-Path -LiteralPath $archiveChecksumPath) {
    throw "Release checksum already exists. Use a new immutable tag: $archiveChecksumPath"
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

Write-Host "[1/6] Building Backend image $backendImage for $Platform"
Invoke-External docker @(
    'buildx', 'build',
    '--platform', $Platform,
    '--file', (Join-Path $projectRoot 'deploy/server/backend.Dockerfile'),
    '--tag', $backendImage,
    '--load',
    $projectRoot
)

Write-Host "[2/6] Building Frontend image $frontendImage for $Platform"
Invoke-External docker @(
    'buildx', 'build',
    '--platform', $Platform,
    '--file', (Join-Path $projectRoot 'deploy/server/frontend.Dockerfile'),
    '--tag', $frontendImage,
    '--load',
    $projectRoot
)

Write-Host "[3/6] Building Demo image $demoImage for $Platform"
Invoke-External docker @(
    'buildx', 'build',
    '--platform', $Platform,
    '--file', (Join-Path $projectRoot 'deploy/server/demo.Dockerfile'),
    '--tag', $demoImage,
    '--load',
    $projectRoot
)

$expectedArchitecture = $Platform.Split('/')[1]
foreach ($imageName in @($backendImage, $frontendImage, $demoImage)) {
    $actualArchitecture = (& docker image inspect --format '{{.Architecture}}' $imageName).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect image: $imageName"
    }
    if ($actualArchitecture -ne $expectedArchitecture) {
        throw "Image architecture mismatch for ${imageName}: expected $expectedArchitecture, got $actualArchitecture"
    }
}

Write-Host '[4/6] Copying source-free deployment files'
New-Item -ItemType Directory -Path $packageDirectory | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'compose.release.yml') -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.server.example') -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'deploy-release.sh') -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy/prometheus/prometheus.server.yml') -Destination (Join-Path $packageDirectory 'prometheus.server.yml')

$releaseEnvironment = @(
    "RELEASE_TAG=$ReleaseTag",
    "BACKEND_IMAGE=$backendImage",
    "FRONTEND_IMAGE=$frontendImage",
    "DEMO_IMAGE=$demoImage"
)
Write-Utf8LfLines -Path (Join-Path $packageDirectory 'release.env') -Lines $releaseEnvironment

Write-Host '[5/6] Validating release Compose and exporting application images'
$validationEnvironmentPath = [System.IO.Path]::GetTempFileName()
try {
    $validationEnvironment = @(
        'MYSQL_IMAGE=mysql:8.4',
        'REDIS_IMAGE=redis:7.4-alpine',
        'RABBITMQ_IMAGE=rabbitmq:4.1-management-alpine',
        'MINIO_IMAGE=minio/minio:validation',
        'PROMETHEUS_IMAGE=prom/prometheus:v2.55.1',
        'MYSQL_DATABASE=ai_test_platform',
        'MYSQL_USER=validation',
        'MYSQL_PASSWORD=validation-only',
        'MYSQL_ROOT_PASSWORD=validation-only',
        'APP_DATABASE_URL=mysql+pymysql://validation:validation-only@mysql:3306/ai_test_platform',
        'REDIS_PASSWORD=validation-only',
        'APP_REDIS_URL=redis://:validation-only@redis:6379/0',
        'RABBITMQ_DEFAULT_USER=validation',
        'RABBITMQ_DEFAULT_PASS=validation-only',
        'APP_RABBITMQ_URL=amqp://validation:validation-only@rabbitmq:5672/',
        'MINIO_ROOT_USER=validation',
        'MINIO_ROOT_PASSWORD=validation-only-password',
        'APP_SECRET_KEY=validation-only-secret-key',
        'APP_SECRET_FERNET_KEY_FILE_HOST=/opt/ai-test/shared/secrets/app-fernet.key',
        'DEMO_PUBLIC_URL=https://demo.example.invalid',
        'APP_CORS_ORIGINS=https://example.invalid',
        'APP_DEV_ADMIN_PASSWORD=validation-only-password'
    )
    Write-Utf8LfLines -Path $validationEnvironmentPath -Lines $validationEnvironment

    Invoke-External docker @(
        'compose',
        '--project-directory', $packageDirectory,
        '--env-file', $validationEnvironmentPath,
        '--env-file', (Join-Path $packageDirectory 'release.env'),
        '--file', (Join-Path $packageDirectory 'compose.release.yml'),
        'config', '--quiet'
    )
}
finally {
    if (Test-Path -LiteralPath $validationEnvironmentPath) {
        Remove-Item -LiteralPath $validationEnvironmentPath -Force
    }
}

Invoke-External docker @(
    'image', 'save',
    '--output', (Join-Path $packageDirectory 'app-images.tar'),
    $backendImage,
    $frontendImage,
    $demoImage
)

Write-Host '[6/6] Generating checksums and compressed archive'
$checksumFiles = @(
    'app-images.tar',
    'compose.release.yml',
    'prometheus.server.yml',
    '.env.server.example',
    'release.env',
    'deploy-release.sh'
)
$checksumLines = foreach ($fileName in $checksumFiles) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $packageDirectory $fileName)).Hash.ToLowerInvariant()
    "$hash  $fileName"
}
Write-Utf8LfLines -Path (Join-Path $packageDirectory 'SHA256SUMS') -Lines $checksumLines

Invoke-External tar.exe @('-czf', $archivePath, '-C', $packageDirectory, '.')
$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    $archiveChecksumPath,
    "$archiveHash  $([System.IO.Path]::GetFileName($archivePath))`n",
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host 'Release package is ready'
Write-Host "Archive:  $archivePath"
Write-Host "Checksum: $archiveChecksumPath"
Write-Host "SHA-256:  $archiveHash"
Write-Host ''
Write-Host 'Upload only the .tar.gz file and its .sha256 file. No source code or production secrets are included.'
