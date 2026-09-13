[CmdletBinding()]
param(
    [string]$Image = "mysql:8.4",
    [int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-DockerCapture {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed ($($Arguments[0])). $($output -join [Environment]::NewLine)"
    }
    return @($output)
}

function Invoke-Alembic {
    param(
        [Parameter(Mandatory = $true)][string]$Direction,
        [Parameter(Mandatory = $true)][string]$Target
    )

    Write-Host "[alembic] $Direction $Target"
    Push-Location -LiteralPath $backendRoot
    try {
        & $pythonPath -m alembic $Direction $Target
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic $Direction $Target failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-Verifier {
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [string]$ExpectedRevision = ""
    )

    $arguments = @($verifierPath, "--action", $Action)
    if ($ExpectedRevision) {
        $arguments += @("--expected-revision", $ExpectedRevision)
    }
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Migration verifier action '$Action' failed with exit code $LASTEXITCODE."
    }
}

function Get-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $listener.Start()
    try {
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function New-RandomHex {
    param([int]$ByteCount)

    $bytes = [byte[]]::new($ByteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return ([Convert]::ToHexString($bytes)).ToLowerInvariant()
}

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $workspaceRoot "backend"
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$verifierPath = Join-Path $PSScriptRoot "p5f_mysql_migration_verifier.py"
$validationRoot = Join-Path $workspaceRoot ".codex-validation"
$runId = New-RandomHex -ByteCount 6
$runDirectory = Join-Path $validationRoot "p5f-mysql-$runId"
$credentialFile = Join-Path $runDirectory "mysql.env"
$containerName = "p5f-mysql-$runId"
$volumeName = "p5f-mysql-$runId-data"
$databaseName = "p5f_validation"
$databaseUser = "p5f_validator"
$containerCreated = $false
$volumeCreated = $false
$validationSucceeded = $false
$cleanupErrors = [System.Collections.Generic.List[string]]::new()
$previousDatabaseUrl = [Environment]::GetEnvironmentVariable("APP_DATABASE_URL", "Process")
$previousNoBytecode = [Environment]::GetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "Process")

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project Python interpreter was not found at .venv/Scripts/python.exe."
}
if (-not (Test-Path -LiteralPath $verifierPath -PathType Leaf)) {
    throw "Migration verifier was not found under deploy/."
}
if ($Image -ne "mysql:8.4") {
    throw "Only the approved mysql:8.4 validation image is allowed."
}

New-Item -ItemType Directory -Path $validationRoot -Force | Out-Null
New-Item -ItemType Directory -Path $runDirectory | Out-Null

$resolvedValidationRoot = [System.IO.Path]::GetFullPath($validationRoot)
$resolvedRunDirectory = [System.IO.Path]::GetFullPath($runDirectory)
$requiredPrefix = $resolvedValidationRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
if (-not $resolvedRunDirectory.StartsWith(
        $requiredPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Refusing to use a validation directory outside .codex-validation."
}
if ((Split-Path -Leaf $resolvedRunDirectory) -notmatch '^p5f-mysql-[0-9a-f]{12}$') {
    throw "Refusing to use an unexpected validation directory name."
}

try {
    $null = Invoke-DockerCapture -Arguments @("version", "--format", "{{.Server.Version}}")
    $null = Invoke-DockerCapture -Arguments @(
        "image", "inspect", $Image, "--format", "{{.Id}}"
    )

    $databasePassword = New-RandomHex -ByteCount 24
    $rootPassword = New-RandomHex -ByteCount 32
    $envText = @(
        "MYSQL_DATABASE=$databaseName",
        "MYSQL_USER=$databaseUser",
        "MYSQL_PASSWORD=$databasePassword",
        "MYSQL_ROOT_PASSWORD=$rootPassword"
    ) -join "`n"
    [System.IO.File]::WriteAllText(
        $credentialFile,
        $envText + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )

    do {
        $hostPort = Get-FreeLoopbackPort
    } while ($hostPort -eq 3306)
    $null = Invoke-DockerCapture -Arguments @("volume", "create", $volumeName)
    $volumeCreated = $true
    $null = Invoke-DockerCapture -Arguments @(
        "create",
        "--name", $containerName,
        "--env-file", $credentialFile,
        "--mount", "source=$volumeName,target=/var/lib/mysql",
        "--publish", "127.0.0.1:$hostPort`:3306",
        "--health-cmd", 'mysqladmin ping -h 127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" --silent',
        "--health-interval", "2s",
        "--health-timeout", "5s",
        "--health-retries", "60",
        $Image,
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_0900_ai_ci"
    )
    $containerCreated = $true
    $null = Invoke-DockerCapture -Arguments @("start", $containerName)
    Write-Host "[mysql] isolated container started on 127.0.0.1:$hostPort"

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $health = "starting"
    while ([DateTime]::UtcNow -lt $deadline) {
        $healthOutput = Invoke-DockerCapture -Arguments @(
            "inspect", "--format", "{{.State.Health.Status}}", $containerName
        )
        $health = ($healthOutput | Select-Object -First 1).Trim()
        if ($health -eq "healthy") {
            break
        }
        if ($health -eq "unhealthy") {
            throw "The isolated MySQL container became unhealthy."
        }
        Start-Sleep -Seconds 2
    }
    if ($health -ne "healthy") {
        throw "The isolated MySQL container did not become healthy within the timeout."
    }
    Write-Host "[mysql] health=healthy"

    $encodedPassword = [System.Uri]::EscapeDataString($databasePassword)
    $env:APP_DATABASE_URL = (
        "mysql+pymysql://$databaseUser`:$encodedPassword@127.0.0.1:$hostPort/" +
        "$databaseName`?charset=utf8mb4"
    )
    $env:PYTHONDONTWRITEBYTECODE = "1"

    # First prove that the entire graph upgrades from a genuinely empty schema.
    Invoke-Alembic -Direction "upgrade" -Target "head"
    Invoke-Verifier -Action "assert-stage" -ExpectedRevision "20260909_0038"
    Invoke-Verifier -Action "assert-timestampadd-boundary"
    Invoke-Alembic -Direction "downgrade" -Target "20260902_0030"
    Invoke-Verifier -Action "assert-stage" -ExpectedRevision "20260902_0030"

    # Upgrade each P5-F revision separately so every boundary is inspected.
    $upgradesBeforeSeed = @(
        "20260904_0031",
        "20260907_0032",
        "20260908_0033",
        "20260908_0034"
    )
    foreach ($revision in $upgradesBeforeSeed) {
        Invoke-Alembic -Direction "upgrade" -Target $revision
        Invoke-Verifier -Action "assert-stage" -ExpectedRevision $revision
    }
    Invoke-Verifier -Action "seed-legacy-model"

    Invoke-Alembic -Direction "upgrade" -Target "20260908_0035"
    Invoke-Verifier -Action "assert-stage" -ExpectedRevision "20260908_0035"
    Invoke-Verifier -Action "assert-0035-backfill"

    Invoke-Alembic -Direction "upgrade" -Target "20260908_0036"
    Invoke-Verifier -Action "assert-stage" -ExpectedRevision "20260908_0036"
    Invoke-Verifier -Action "assert-0036-backfill"
    Invoke-Verifier -Action "mutate-0036-connection"

    Invoke-Alembic -Direction "upgrade" -Target "20260909_0037"
    Invoke-Verifier -Action "assert-stage" -ExpectedRevision "20260909_0037"
    Invoke-Verifier -Action "prepare-0037-null-context"

    Invoke-Alembic -Direction "upgrade" -Target "20260909_0038"
    Invoke-Verifier -Action "assert-stage" -ExpectedRevision "20260909_0038"
    Invoke-Verifier -Action "assert-0038-check"

    # Walk every revision back with non-empty legacy model data in place.
    $downgrades = @(
        "20260909_0037",
        "20260908_0036",
        "20260908_0035",
        "20260908_0034",
        "20260908_0033",
        "20260907_0032",
        "20260904_0031",
        "20260902_0030"
    )
    foreach ($revision in $downgrades) {
        Invoke-Alembic -Direction "downgrade" -Target $revision
        Invoke-Verifier -Action "assert-stage" -ExpectedRevision $revision
        if ($revision -eq "20260908_0036") {
            Invoke-Verifier -Action "assert-0037-downgrade-protection"
        }
        if ($revision -eq "20260908_0035") {
            Invoke-Verifier -Action "assert-0036-downgrade-protection"
        }
        if ($revision -eq "20260908_0034") {
            Invoke-Verifier -Action "assert-base-data"
        }
    }
    Invoke-Verifier -Action "assert-base-data"

    # Re-upgrade each revision and re-check structures and data backfills.
    $reupgrades = @(
        "20260904_0031",
        "20260907_0032",
        "20260908_0033",
        "20260908_0034",
        "20260908_0035",
        "20260908_0036",
        "20260909_0037",
        "20260909_0038"
    )
    foreach ($revision in $reupgrades) {
        Invoke-Alembic -Direction "upgrade" -Target $revision
        Invoke-Verifier -Action "assert-stage" -ExpectedRevision $revision
        if ($revision -eq "20260908_0035") {
            Invoke-Verifier -Action "assert-0035-backfill"
        }
        if ($revision -eq "20260908_0036") {
            Invoke-Verifier -Action "assert-0036-backfill"
        }
    }
    Invoke-Verifier -Action "assert-0038-check"
    Invoke-Verifier -Action "assert-final-data"
    Invoke-Verifier -Action "assert-timestampadd-boundary"
    $validationSucceeded = $true
    Write-Host "[result] P5-F MySQL migration validation passed through 20260909_0038."
}
finally {
    try {
        if ($null -eq $previousDatabaseUrl) {
            Remove-Item Env:APP_DATABASE_URL -ErrorAction SilentlyContinue
        }
        else {
            $env:APP_DATABASE_URL = $previousDatabaseUrl
        }
    }
    catch {
        $cleanupErrors.Add("Could not restore APP_DATABASE_URL process state.")
    }
    try {
        if ($null -eq $previousNoBytecode) {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
        }
    }
    catch {
        $cleanupErrors.Add("Could not restore PYTHONDONTWRITEBYTECODE process state.")
    }

    try {
        if (Test-Path -LiteralPath $credentialFile -PathType Leaf) {
            Remove-Item -LiteralPath $credentialFile -Force
        }
    }
    catch {
        $cleanupErrors.Add("Temporary credential file could not be removed.")
    }
    if ($containerCreated -and $containerName -match '^p5f-mysql-[0-9a-f]{12}$') {
        try {
            $null = Invoke-DockerCapture -Arguments @("rm", "--force", $containerName)
        }
        catch {
            $cleanupErrors.Add($_.Exception.Message)
        }
    }
    if ($volumeCreated -and $volumeName -match '^p5f-mysql-[0-9a-f]{12}-data$') {
        try {
            $null = Invoke-DockerCapture -Arguments @("volume", "rm", $volumeName)
        }
        catch {
            $cleanupErrors.Add($_.Exception.Message)
        }
    }
    try {
        if (Test-Path -LiteralPath $resolvedRunDirectory -PathType Container) {
            Remove-Item -LiteralPath $resolvedRunDirectory -Recurse -Force
        }
    }
    catch {
        $cleanupErrors.Add("Temporary validation directory could not be removed.")
    }

    try {
        $remainingContainers = Invoke-DockerCapture -Arguments @(
            "ps", "--all", "--quiet", "--filter", "name=^/$containerName$"
        )
        if (@($remainingContainers | Where-Object { $_.Trim() }).Count -ne 0) {
            $cleanupErrors.Add("Temporary container still exists after cleanup.")
        }
        $remainingVolumes = Invoke-DockerCapture -Arguments @(
            "volume", "ls", "--quiet", "--filter", "name=^$volumeName$"
        )
        if (@($remainingVolumes | Where-Object { $_.Trim() }).Count -ne 0) {
            $cleanupErrors.Add("Temporary volume still exists after cleanup.")
        }
    }
    catch {
        $cleanupErrors.Add($_.Exception.Message)
    }

    if ($cleanupErrors.Count -eq 0) {
        Write-Host "[cleanup] temporary container, volume, credential file and run directory removed."
    }
    else {
        throw "Validation cleanup failed: $($cleanupErrors -join ' | ')"
    }
}

if (-not $validationSucceeded) {
    throw "P5-F MySQL migration validation did not complete successfully."
}
