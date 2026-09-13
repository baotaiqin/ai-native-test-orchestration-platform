[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$PSNativeCommandUseErrorActionPreference = $false
$stage = "initialization"

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$validationDirectory = Join-Path $workspaceRoot ".codex-validation\p5e-services"
$readyPath = Join-Path $validationDirectory "ready.json"
$ownedLedgerPath = Join-Path $validationDirectory "owned-processes.json"
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$vitePath = Join-Path $workspaceRoot "frontend\node_modules\vite\bin\vite.js"
$runnerRunConfiguration = Join-Path $workspaceRoot ".run\Runner Worker.run.xml"
$runnerReadinessVerifier = Join-Path $workspaceRoot "deploy\p5e_runner_readiness.py"

function Get-ProcessStartUtc {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    return $process.StartTime.ToUniversalTime().ToString("o")
}

function Get-SafeProcessRecord {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    return [ordered]@{
        pid = [int]$process.Id
        parent_pid = [int]$cimProcess.ParentProcessId
        process_name = [string]$process.ProcessName
        purpose = $Purpose
        started_at = $process.StartTime.ToUniversalTime().ToString("o")
    }
}

function Get-DescendantProcessRecords {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $pending.Enqueue($RootProcessId)
    $records = [System.Collections.Generic.List[object]]::new()

    while ($pending.Count -gt 0) {
        $currentId = $pending.Dequeue()
        if (-not $seen.Add($currentId)) {
            continue
        }
        $current = $snapshot | Where-Object { [int]$_.ProcessId -eq $currentId } | Select-Object -First 1
        if ($null -ne $current) {
            $process = Get-Process -Id $currentId -ErrorAction SilentlyContinue
            if ($null -ne $process) {
                $records.Add([ordered]@{
                    pid = [int]$process.Id
                    parent_pid = [int]$current.ParentProcessId
                    process_name = [string]$process.ProcessName
                    purpose = $Purpose
                    started_at = $process.StartTime.ToUniversalTime().ToString("o")
                })
            }
        }
        foreach ($child in @($snapshot | Where-Object { [int]$_.ParentProcessId -eq $currentId })) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($records)
}

function Add-OwnedProcessRecord {
    param(
        [Parameter(Mandatory = $true)]$Record,
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[object]]$Processes
    )

    $present = @($Processes | Where-Object {
        $_.pid -eq $Record.pid -and $_.purpose -eq $Record.purpose
    }).Count -gt 0
    if (-not $present) {
        $Processes.Add($Record)
    }
}

function Get-PortListener {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) {
        return $null
    }
    if ($listeners.Count -gt 1) {
        throw "Port $Port has more than one listening socket."
    }

    $process = Get-Process -Id $listeners[0].OwningProcess -ErrorAction Stop
    return [ordered]@{
        pid = [int]$process.Id
        process_name = $process.ProcessName
        started_at = $process.StartTime.ToUniversalTime().ToString("o")
    }
}

function Wait-HttpHealthy {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 45
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -Method Get -TimeoutSec 4 -UseBasicParsing
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return $true
            }
        }
        catch {
            # A service may still be booting. Retry without surfacing response data.
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Test-ProjectServiceFingerprint {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][ValidateSet("backend", "demo", "frontend")]
        [string]$Service
    )

    try {
        $response = Invoke-WebRequest -Uri $Uri -Method Get -TimeoutSec 5 -UseBasicParsing
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
            return $false
        }
        if ($Service -eq "frontend") {
            return $response.Content -match "<title>AI 原生智能测试编排平台</title>"
        }
        $payload = $response.Content | ConvertFrom-Json
        if ($Service -eq "backend") {
            return $payload.status -eq "ok" -and $payload.service -eq "backend"
        }
        return $payload.status -eq "ok" -and $payload.service -eq "v1-demo"
    }
    catch {
        return $false
    }
}

function Get-WorkerInventory {
    $workers = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.CommandLine -match "(?i)(?:^|\s)(?:-m\s+runner\.main\s+worker|runner\.main.*\sworker(?:\s|$))"
            }
    )

    $safeWorkers = @(
        foreach ($worker in $workers) {
            $creationDate = $worker.CreationDate
            if ($creationDate -is [DateTime]) {
                $workerStartedAt = $creationDate.ToUniversalTime().ToString("o")
            }
            else {
                $workerStartedAt = (
                    [Management.ManagementDateTimeConverter]::ToDateTime([string]$creationDate).
                        ToUniversalTime().ToString("o")
                )
            }
            [ordered]@{
                pid = [int]$worker.ProcessId
                parent_pid = [int]$worker.ParentProcessId
                process_name = [string]$worker.Name
                started_at = $workerStartedAt
            }
        }
    )
    $workerIds = @($safeWorkers | ForEach-Object { $_.pid })
    $roots = @($safeWorkers | Where-Object { $_.parent_pid -notin $workerIds })
    return [pscustomobject]@{
        logical_count = $roots.Count
        roots = $roots
        processes = $safeWorkers
    }
}

function Get-ContainerHealth {
    param([Parameter(Mandatory = $true)][string]$Name)

    $format = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    $state = (& docker inspect --format $format $Name 2>$null | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($state)) {
        throw "Required container $Name is unavailable."
    }
    $parts = $state.Trim().Split("|", 2)
    $healthy = $parts[0] -eq "running" -and $parts[1] -in @("healthy", "none")
    return [ordered]@{
        container = $Name
        status = $parts[0]
        health = $parts[1]
        healthy = $healthy
    }
}

function Start-OwnedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Purpose,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [hashtable]$Environment = @{}
    )

    $parameters = @{
        FilePath = $FilePath
        ArgumentList = $ArgumentList
        WorkingDirectory = $WorkingDirectory
        WindowStyle = "Hidden"
        PassThru = $true
    }
    if ($Environment.Count -gt 0) {
        $parameters.Environment = $Environment
    }
    $process = Start-Process @parameters
    return Get-SafeProcessRecord -ProcessId $process.Id -Purpose $Purpose
}

function Save-OwnedProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Processes
    )

    $safeProcesses = @($Processes)
    $json = if ($safeProcesses.Count -eq 0) {
        "[]"
    }
    else {
        $safeProcesses | ConvertTo-Json -Depth 4 -AsArray
    }
    Set-Content -LiteralPath $ownedLedgerPath -Value $json -Encoding utf8
}

function Test-P5eOwnedProcess {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Purpose,
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[object]]$Processes
    )

    return $null -ne (
        @($Processes) |
            Where-Object { $_.pid -eq $ProcessId -and $_.purpose -eq $Purpose } |
            Select-Object -First 1
    )
}

try {
    $stage = "validate local files"
    foreach ($requiredPath in @(
        $pythonPath,
        $vitePath,
        $runnerRunConfiguration,
        $runnerReadinessVerifier
    )) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "A required local runtime file is unavailable."
        }
    }
    New-Item -ItemType Directory -Path $validationDirectory -Force | Out-Null

    $stage = "verify middleware"
    $rabbitMq = Get-ContainerHealth -Name "ai-test-rabbitmq"
    $redis = Get-ContainerHealth -Name "ai-test-redis"
    $minio = Get-ContainerHealth -Name "ai-test-minio"
    if (-not ($rabbitMq.healthy -and $redis.healthy -and $minio.healthy)) {
        throw "One or more required middleware containers are unhealthy."
    }

    $stage = "verify mysql migration"
    Push-Location (Join-Path $workspaceRoot "backend")
    try {
        $migrationOutput = (& $pythonPath -m alembic current 2>&1 | Out-String)
    }
    finally {
        Pop-Location
    }
    if ($migrationOutput -notmatch "20260909_0038\s+\(head\)") {
        throw "The formal MySQL database is not at migration head 20260909_0038."
    }

    $stage = "load owned process ledger"
    $ownedProcesses = [System.Collections.Generic.List[object]]::new()
    if (Test-Path -LiteralPath $ownedLedgerPath -PathType Leaf) {
        $ledgerEntries = @(Get-Content -LiteralPath $ownedLedgerPath -Raw | ConvertFrom-Json)
        foreach ($entry in $ledgerEntries) {
            $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
            if ($null -eq $process) {
                continue
            }
            $recordedStartedAt = if ($entry.started_at -is [DateTime]) {
                $entry.started_at.ToUniversalTime().ToString("o")
            }
            else {
                [string]$entry.started_at
            }
            $recordedStart = [DateTimeOffset]::Parse($recordedStartedAt).UtcDateTime
            $actualStart = $process.StartTime.ToUniversalTime()
            if ([Math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -lt 1) {
                $ownedProcesses.Add((
                    Get-SafeProcessRecord `
                        -ProcessId ([int]$entry.pid) `
                        -Purpose ([string]$entry.purpose)
                ))
            }
        }
        Save-OwnedProcesses -Processes $ownedProcesses
    }
    $services = [System.Collections.Generic.List[object]]::new()

    $stage = "prepare backend"
    $backendListener = Get-PortListener -Port 8000
    $backendOwnership = if ($null -ne $backendListener -and (
        Test-P5eOwnedProcess -ProcessId $backendListener.pid -Purpose "backend" -Processes $ownedProcesses
    )) { "p5e-owned" } else { "existing" }
    $backendRootPid = $null
    if ($null -eq $backendListener) {
        $owned = Start-OwnedProcess -Purpose "backend" -FilePath $pythonPath `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
            -WorkingDirectory (Join-Path $workspaceRoot "backend")
        $ownedProcesses.Add($owned)
        $backendRootPid = $owned.pid
        Save-OwnedProcesses -Processes $ownedProcesses
        $backendOwnership = "p5e-owned"
    }
    if (-not (Wait-HttpHealthy -Uri "http://127.0.0.1:8000/health" -TimeoutSeconds 60)) {
        throw "Backend health endpoint did not become ready."
    }
    if (-not (Test-ProjectServiceFingerprint -Uri "http://127.0.0.1:8000/health" -Service "backend")) {
        throw "Port 8000 is not serving this project's backend."
    }
    $backendListener = Get-PortListener -Port 8000
    if ($null -ne $backendRootPid) {
        foreach ($record in @(Get-DescendantProcessRecords -RootProcessId $backendRootPid -Purpose "backend")) {
            Add-OwnedProcessRecord -Record $record -Processes $ownedProcesses
        }
        Save-OwnedProcesses -Processes $ownedProcesses
    }
    $backendProcessTree = @($ownedProcesses | Where-Object { $_.purpose -eq "backend" })
    $services.Add([ordered]@{
        name = "backend"
        address = "http://127.0.0.1:8000"
        health_address = "http://127.0.0.1:8000/health"
        healthy = $true
        ownership = $backendOwnership
        pid = $backendListener.pid
        started_at = $backendListener.started_at
        process_tree = $backendProcessTree
    })

    $stage = "prepare demo"
    $demoListener = Get-PortListener -Port 8765
    $demoOwnership = if ($null -ne $demoListener -and (
        Test-P5eOwnedProcess -ProcessId $demoListener.pid -Purpose "demo" -Processes $ownedProcesses
    )) { "p5e-owned" } else { "existing" }
    $demoRootPid = $null
    if ($null -eq $demoListener) {
        $owned = Start-OwnedProcess -Purpose "demo" -FilePath $pythonPath `
            -ArgumentList @("-m", "demo.server", "--port", "8765") `
            -WorkingDirectory $workspaceRoot
        $ownedProcesses.Add($owned)
        $demoRootPid = $owned.pid
        Save-OwnedProcesses -Processes $ownedProcesses
        $demoOwnership = "p5e-owned"
    }
    if (-not (Wait-HttpHealthy -Uri "http://127.0.0.1:8765/health" -TimeoutSeconds 30)) {
        throw "Demo health endpoint did not become ready."
    }
    if (-not (Test-ProjectServiceFingerprint -Uri "http://127.0.0.1:8765/health" -Service "demo")) {
        throw "Port 8765 is not serving this project's V1 demo."
    }
    $demoListener = Get-PortListener -Port 8765
    if ($null -ne $demoRootPid) {
        foreach ($record in @(Get-DescendantProcessRecords -RootProcessId $demoRootPid -Purpose "demo")) {
            Add-OwnedProcessRecord -Record $record -Processes $ownedProcesses
        }
        Save-OwnedProcesses -Processes $ownedProcesses
    }
    $demoProcessTree = @($ownedProcesses | Where-Object { $_.purpose -eq "demo" })
    $services.Add([ordered]@{
        name = "demo"
        address = "http://127.0.0.1:8765"
        health_address = "http://127.0.0.1:8765/health"
        healthy = $true
        ownership = $demoOwnership
        pid = $demoListener.pid
        started_at = $demoListener.started_at
        process_tree = $demoProcessTree
    })

    $stage = "prepare frontend"
    $frontendListener = Get-PortListener -Port 5173
    $frontendOwnership = if ($null -ne $frontendListener -and (
        Test-P5eOwnedProcess -ProcessId $frontendListener.pid -Purpose "frontend" -Processes $ownedProcesses
    )) { "p5e-owned" } else { "existing" }
    $frontendRootPid = $null
    if ($null -eq $frontendListener) {
        $owned = Start-OwnedProcess -Purpose "frontend" -FilePath $nodePath `
            -ArgumentList @("`"$vitePath`"", "--host", "127.0.0.1", "--port", "5173", "--strictPort") `
            -WorkingDirectory (Join-Path $workspaceRoot "frontend") `
            -Environment @{ BROWSER = "none" }
        $ownedProcesses.Add($owned)
        $frontendRootPid = $owned.pid
        Save-OwnedProcesses -Processes $ownedProcesses
        $frontendOwnership = "p5e-owned"
    }
    if (-not (Wait-HttpHealthy -Uri "http://127.0.0.1:5173" -TimeoutSeconds 45)) {
        throw "Frontend did not become ready."
    }
    if (-not (Test-ProjectServiceFingerprint -Uri "http://127.0.0.1:5173" -Service "frontend")) {
        throw "Port 5173 is not serving this project's frontend."
    }
    $frontendListener = Get-PortListener -Port 5173
    if ($null -ne $frontendRootPid) {
        foreach ($record in @(Get-DescendantProcessRecords -RootProcessId $frontendRootPid -Purpose "frontend")) {
            Add-OwnedProcessRecord -Record $record -Processes $ownedProcesses
        }
        Save-OwnedProcesses -Processes $ownedProcesses
    }
    $frontendProcessTree = @($ownedProcesses | Where-Object { $_.purpose -eq "frontend" })
    $services.Add([ordered]@{
        name = "frontend"
        address = "http://127.0.0.1:5173"
        health_address = "http://127.0.0.1:5173"
        healthy = $true
        ownership = $frontendOwnership
        pid = $frontendListener.pid
        started_at = $frontendListener.started_at
        process_tree = $frontendProcessTree
    })

    $stage = "prepare single runner worker"
    $workerInventory = Get-WorkerInventory
    if ($workerInventory.logical_count -gt 1) {
        throw "More than one logical Runner worker is already active."
    }
    $workerOwnership = if ($workerInventory.logical_count -eq 1 -and (
        @($workerInventory.processes | Where-Object {
            Test-P5eOwnedProcess -ProcessId $_.pid -Purpose "runner-worker" -Processes $ownedProcesses
        }).Count -gt 0
    )) { "p5e-owned" } else { "existing" }
    $startedWorkerThisRun = $false
    if ($workerInventory.logical_count -eq 0) {
        [xml]$runConfiguration = Get-Content -LiteralPath $runnerRunConfiguration
        $rabbitMqNode = @($runConfiguration.component.configuration.envs.env) |
            Where-Object { $_.name -eq "AI_TEST_RABBITMQ_URL" } |
            Select-Object -First 1
        $rabbitMqUrl = [string]$rabbitMqNode.value
        if ([string]::IsNullOrWhiteSpace($rabbitMqUrl)) {
            throw "The existing Runner run configuration has no RabbitMQ URL."
        }
        $rabbitUri = [Uri]$rabbitMqUrl
        if ($rabbitUri.Scheme -notin @("amqp", "amqps") -or $rabbitUri.Port -ne 5672 -or `
            $rabbitUri.Host -notin @("127.0.0.1", "localhost")) {
            throw "The existing Runner run configuration does not target the approved local RabbitMQ."
        }

        $owned = Start-OwnedProcess -Purpose "runner-worker" -FilePath $pythonPath `
            -ArgumentList @("-m", "runner.main", "worker", "--web-slots", "1") `
            -WorkingDirectory (Join-Path $workspaceRoot "runner") `
            -Environment @{ AI_TEST_RABBITMQ_URL = $rabbitMqUrl }
        $ownedProcesses.Add($owned)
        Save-OwnedProcesses -Processes $ownedProcesses
        $workerOwnership = "p5e-owned"
        $startedWorkerThisRun = $true
        Remove-Variable rabbitMqUrl -ErrorAction SilentlyContinue
        $workerDeadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 500
            $workerInventory = Get-WorkerInventory
        } while ($workerInventory.logical_count -eq 0 -and [DateTime]::UtcNow -lt $workerDeadline)
    }
    if ($workerInventory.logical_count -ne 1) {
        throw "The strict single-Worker requirement is not satisfied."
    }

    if ($startedWorkerThisRun) {
        foreach ($workerProcess in @($workerInventory.processes)) {
            if (-not (Test-P5eOwnedProcess -ProcessId $workerProcess.pid -Purpose "runner-worker" -Processes $ownedProcesses)) {
                $ownedProcesses.Add((
                    Get-SafeProcessRecord `
                        -ProcessId $workerProcess.pid `
                        -Purpose "runner-worker"
                ))
            }
        }
        Save-OwnedProcesses -Processes $ownedProcesses
    }

    $workerStartedAt = @($workerInventory.processes | Sort-Object started_at)[0].started_at
    $stage = "verify long-running runner readiness"
    $runnerReadiness = $null
    $readinessDeadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        Push-Location (Join-Path $workspaceRoot "backend")
        try {
            $runnerReadinessText = (
                & $pythonPath $runnerReadinessVerifier `
                    --minimum-heartbeat-utc $workerStartedAt 2>$null |
                    Out-String
            )
        }
        finally {
            Pop-Location
        }
        if (-not [string]::IsNullOrWhiteSpace($runnerReadinessText)) {
            $runnerReadiness = $runnerReadinessText | ConvertFrom-Json
        }
        if ($null -ne $runnerReadiness -and $runnerReadiness.ready -eq $true) {
            break
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $readinessDeadline)
    if ($runnerReadiness.ready -ne $true) {
        throw "The long-running Runner is not online with the required WEB capacity."
    }
    Start-Sleep -Seconds 2
    $workerInventory = Get-WorkerInventory
    if ($workerInventory.logical_count -ne 1) {
        throw "The long-running Runner process did not remain active."
    }
    $expectedQueue = "ai_test.runner.$($runnerReadiness.runner_id).v1"
    $queueDeadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $queueNames = @(
            & docker exec ai-test-rabbitmq rabbitmqctl --quiet list_queues name 2>$null |
                ForEach-Object { ([string]$_).Trim() }
        )
        if ($expectedQueue -in $queueNames) {
            break
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $queueDeadline)
    if ($expectedQueue -notin $queueNames) {
        throw "The Runner RabbitMQ topology is not ready."
    }

    $liveOwnedProcesses = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in @($ownedProcesses)) {
        $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
        $recordedStartedAt = if ($entry.started_at -is [DateTime]) {
            $entry.started_at.ToUniversalTime().ToString("o")
        }
        else {
            [string]$entry.started_at
        }
        $recordedStart = [DateTimeOffset]::Parse($recordedStartedAt).UtcDateTime
        $actualStart = if ($null -ne $process) { $process.StartTime.ToUniversalTime() } else { $null }
        if ($null -ne $actualStart -and [Math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -lt 1) {
            $liveOwnedProcesses.Add($entry)
        }
    }
    $ownedProcesses = $liveOwnedProcesses
    Save-OwnedProcesses -Processes $ownedProcesses

    $runnerLastHeartbeatAt = if ($runnerReadiness.last_heartbeat_at -is [DateTime]) {
        $runnerReadiness.last_heartbeat_at.ToUniversalTime().ToString("o")
    }
    else {
        [DateTimeOffset]::Parse([string]$runnerReadiness.last_heartbeat_at).
            UtcDateTime.ToString("o")
    }

    $stage = "write readiness manifest"
    $ready = [ordered]@{
        schema_version = 1
        generated_at = [DateTime]::UtcNow.ToString("o")
        database = [ordered]@{
            engine = "mysql"
            migration_revision = "20260909_0038"
            at_head = $true
        }
        middleware = [ordered]@{
            rabbitmq = [ordered]@{
                container = $rabbitMq.container
                status = $rabbitMq.status
                health = $rabbitMq.health
                healthy = $rabbitMq.healthy
                address = "127.0.0.1:5672"
            }
            redis = [ordered]@{
                container = $redis.container
                status = $redis.status
                health = $redis.health
                healthy = $redis.healthy
                address = "127.0.0.1:6379"
            }
            minio = [ordered]@{
                container = $minio.container
                status = $minio.status
                health = $minio.health
                healthy = $minio.healthy
                address = "http://127.0.0.1:9000"
            }
        }
        services = @($services)
        runner = [ordered]@{
            runner_id = [string]$runnerReadiness.runner_id
            status = [string]$runnerReadiness.status
            online_status = [string]$runnerReadiness.online_status
            healthy = $true
            logical_worker_count = 1
            process_count = @($workerInventory.processes).Count
            pids = @($workerInventory.processes | ForEach-Object { $_.pid })
            process_tree = @($workerInventory.processes)
            web_capability = [string]$runnerReadiness.web_capability
            web_slots = $runnerReadiness.web_slots
            active_run_count = [int]$runnerReadiness.active_run_count
            ownership = $workerOwnership
            started_at = $workerStartedAt
            last_heartbeat_at = $runnerLastHeartbeatAt
            rabbitmq_queue = $expectedQueue
        }
        owned_processes = @($ownedProcesses)
        notes = @(
            "This manifest stores operational readiness metadata only.",
            "P5-E-owned processes must remain running until the main task requests coordinated shutdown."
        )
    }
    $readyJson = $ready | ConvertTo-Json -Depth 8
    $readyTemporaryPath = "$readyPath.tmp-$PID"
    [IO.File]::WriteAllText(
        $readyTemporaryPath,
        $readyJson,
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::Move($readyTemporaryPath, $readyPath, $true)
    Write-Output ("READY|{0}" -f $readyPath)
    Write-Output ("RUNNER|{0}|{1}|pids={2}" -f $ready.runner.runner_id,$ready.runner.online_status,($ready.runner.pids -join ","))
    foreach ($service in $ready.services) {
        Write-Output ("SERVICE|{0}|{1}|{2}|pid={3}" -f $service.name,$service.address,$service.ownership,$service.pid)
    }
}
catch {
    Write-Error (
        "P5-E service preparation failed during safe stage: {0} ({1})." -f `
            $stage,$_.Exception.GetType().Name
    )
    exit 1
}
