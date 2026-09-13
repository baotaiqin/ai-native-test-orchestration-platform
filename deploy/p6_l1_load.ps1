[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$PSNativeCommandUseErrorActionPreference = $false
$stage = "initialization"

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendDirectory = Join-Path $workspaceRoot "backend"
$runnerDirectory = Join-Path $workspaceRoot "runner"
$servicesDirectory = Join-Path $workspaceRoot ".codex-validation\p5e-services"
$validationDirectory = Join-Path $workspaceRoot ".codex-validation\p6-l1"
$readyPath = Join-Path $servicesDirectory "ready.json"
$ownedPath = Join-Path $servicesDirectory "owned-processes.json"
$preflightPath = Join-Path $validationDirectory "preflight.json"
$verificationPath = Join-Path $validationDirectory "verification.json"
$loadPath = Join-Path $validationDirectory "load.json"
$freezePath = Join-Path $workspaceRoot ".codex-validation\p6-l1-controller-freeze.json"
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$runnerConfigurationPath = Join-Path $workspaceRoot ".run\Runner Worker.run.xml"
$runnerReadinessPath = Join-Path $workspaceRoot "deploy\p5e_runner_readiness.py"
$p6ReadinessPath = Join-Path $workspaceRoot "deploy\p6_l1_readiness.py"
$package = "P6-L1/r1"
$runnerId = "1282a04dfd894f85877384cb31af5c16"
$expectedOldWorkerCommandHashes = @(
    "751b18249a6a20c4b19612ea9b4adbd7a1c3d37e993f7b2fdd1c074f5d155cc2",
    "efb0366706448dfc8832f9fe0b18e4a65307d7ebc6808348069dff6f2e834594"
)
$requiredOpenApiPaths = @(
    "/api/v1/reports",
    "/api/v1/reports/{run_id}",
    "/api/v1/reports/{run_id}/cases",
    "/api/v1/reports/{run_id}/steps",
    "/api/v1/reports/{run_id}/evidence",
    "/api/v1/reports/{run_id}/export",
    "/api/v1/dashboard"
)

function Read-JsonAsHashtable {
    param([Parameter(Mandatory = $true)][string]$Path)

    return Get-Content -LiteralPath $Path -Raw |
        ConvertFrom-Json -AsHashtable -DateKind String
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $temporaryPath = "$Path.tmp-$PID"
    $json = $Value | ConvertTo-Json -Depth 30
    [IO.File]::WriteAllText($temporaryPath, $json + "`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temporaryPath, $Path, $true)
}

function Get-CommandHash {
    param([string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $null
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($CommandLine)
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).
        ToLowerInvariant()
}

function Test-BackendCommandIdentity {
    param([Parameter(Mandatory = $true)]$CimProcess)

    if ([string]$CimProcess.Name -eq "conhost.exe") {
        return $true
    }
    return [string]$CimProcess.Name -like "python*" -and
        [string]$CimProcess.CommandLine -match "(?i)-m\s+uvicorn\s+app\.main:app" -and
        [string]$CimProcess.CommandLine -match "(?i)--host\s+127\.0\.0\.1" -and
        [string]$CimProcess.CommandLine -match "(?i)--port\s+8000"
}

function Test-WorkerCommandIdentity {
    param([Parameter(Mandatory = $true)]$CimProcess)

    return [string]$CimProcess.Name -like "python*" -and
        [string]$CimProcess.CommandLine -match "(?i)-m\s+runner\.main\s+worker(?:\s|$)" -and
        [string]$CimProcess.CommandLine -match "(?i)--web-slots\s+1(?:\s|$)"
}

function Get-LiveTargetRecord {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][ValidateSet("backend", "runner-worker")]
        [string]$Purpose
    )

    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $cimProcess = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    if ($Purpose -eq "backend") {
        if (-not (Test-BackendCommandIdentity -CimProcess $cimProcess)) {
            throw "A candidate Backend process does not match the approved command identity."
        }
        $identity = if ([string]$cimProcess.Name -eq "conhost.exe") {
            "backend-console-host"
        }
        else {
            "python-uvicorn-app.main-127.0.0.1-8000"
        }
    }
    else {
        if (-not (Test-WorkerCommandIdentity -CimProcess $cimProcess)) {
            throw "A candidate Worker process does not match the approved command identity."
        }
        $identity = "python-runner.main-worker-web-slots-1"
    }
    return [ordered]@{
        pid = [int]$process.Id
        parent_pid = [int]$cimProcess.ParentProcessId
        process_name = [string]$process.ProcessName
        purpose = $Purpose
        started_at = $process.StartTime.ToUniversalTime().ToString("o")
        command_sha256 = Get-CommandHash -CommandLine ([string]$cimProcess.CommandLine)
        command_identity = $identity
    }
}

function Assert-RecordedTargetIdentity {
    param(
        [Parameter(Mandatory = $true)]$Entry,
        [switch]$AllowLegacyWorkerRecord
    )

    $purpose = [string]$Entry.purpose
    $actual = Get-LiveTargetRecord -ProcessId ([int]$Entry.pid) -Purpose $purpose
    $recordedStart = [DateTimeOffset]::Parse([string]$Entry.started_at).UtcDateTime
    $actualStart = [DateTimeOffset]::Parse([string]$actual.started_at).UtcDateTime
    if ([Math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -ge 1) {
        throw "A candidate target PID has been reused."
    }
    if ([int]$actual.parent_pid -ne [int]$Entry.parent_pid) {
        throw "A recorded target process parent has changed."
    }
    if ($purpose -eq "runner-worker" -and $AllowLegacyWorkerRecord) {
        if ([string]$actual.command_sha256 -notin $expectedOldWorkerCommandHashes) {
            throw "The legacy owned Worker command hash is not approved."
        }
    }
    elseif ([string]$actual.command_identity -ne [string]$Entry.command_identity -or
        [string]$actual.command_sha256 -ne [string]$Entry.command_sha256) {
        throw "A recorded target command identity has changed."
    }
    return $actual
}

function Get-TargetDescendants {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][ValidateSet("backend", "runner-worker")]
        [string]$Purpose
    )

    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $pending = [Collections.Generic.Queue[int]]::new()
    $seen = [Collections.Generic.HashSet[int]]::new()
    $records = [Collections.Generic.List[object]]::new()
    $pending.Enqueue($RootProcessId)
    while ($pending.Count -gt 0) {
        $currentId = $pending.Dequeue()
        if (-not $seen.Add($currentId)) {
            continue
        }
        $current = $snapshot |
            Where-Object { [int]$_.ProcessId -eq $currentId } |
            Select-Object -First 1
        if ($null -ne $current -and $null -ne (
            Get-Process -Id $currentId -ErrorAction SilentlyContinue
        )) {
            $records.Add((Get-LiveTargetRecord -ProcessId $currentId -Purpose $Purpose))
        }
        foreach ($child in @(
            $snapshot | Where-Object { [int]$_.ParentProcessId -eq $currentId }
        )) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($records)
}

function Get-PortListenerIds {
    param([Parameter(Mandatory = $true)][int]$Port)

    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port `
            -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            Sort-Object
    )
}

function Get-SinglePortListener {
    param([Parameter(Mandatory = $true)][int]$Port)

    $ids = @(Get-PortListenerIds -Port $Port)
    if ($ids.Count -eq 0) {
        return $null
    }
    if ($ids.Count -ne 1) {
        throw "Port $Port does not have exactly one owning process."
    }
    return [int]$ids[0]
}

function Get-WorkerInventory {
    $workers = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { Test-WorkerCommandIdentity -CimProcess $_ }
    )
    $records = @(
        foreach ($worker in $workers) {
            Get-LiveTargetRecord -ProcessId ([int]$worker.ProcessId) `
                -Purpose "runner-worker"
        }
    )
    $ids = @($records | ForEach-Object { [int]$_.pid })
    $roots = @($records | Where-Object { [int]$_.parent_pid -notin $ids })
    return [pscustomobject]@{
        logical_count = $roots.Count
        roots = $roots
        processes = $records
    }
}

function Assert-ReadyTargetTrees {
    param(
        [Parameter(Mandatory = $true)]$Ready,
        [Parameter(Mandatory = $true)][object[]]$Owned
    )

    $backend = @($Ready.services | Where-Object { $_.name -eq "backend" })
    if ($backend.Count -ne 1 -or $backend[0].ownership -ne "p5e-owned" -or
        [string]$backend[0].working_directory -ne $backendDirectory -or
        $backend[0].command_identity -ne "python-uvicorn-app.main-127.0.0.1-8000") {
        throw "The ready manifest does not prove the expected Backend ownership."
    }
    $backendOwned = @($Owned | Where-Object { $_.purpose -eq "backend" })
    $backendReadyIds = @($backend[0].process_tree | ForEach-Object { [int]$_.pid } | Sort-Object)
    $backendOwnedIds = @($backendOwned | ForEach-Object { [int]$_.pid } | Sort-Object)
    if ($backendOwned.Count -eq 0 -or
        ($backendReadyIds -join ",") -ne ($backendOwnedIds -join ",")) {
        throw "The ready and owned Backend trees differ."
    }

    $workerOwned = @($Owned | Where-Object { $_.purpose -eq "runner-worker" })
    $readyWorkerIds = @($Ready.runner.pids | ForEach-Object { [int]$_ } | Sort-Object)
    $ownedWorkerIds = @($workerOwned | ForEach-Object { [int]$_.pid } | Sort-Object)
    if ($Ready.runner.runner_id -ne $runnerId -or
        $Ready.runner.ownership -ne "p5e-owned" -or
        $Ready.runner.logical_worker_count -ne 1 -or
        $workerOwned.Count -ne 2 -or
        ($readyWorkerIds -join ",") -ne ($ownedWorkerIds -join ",")) {
        throw "The ready and owned Worker identity records differ."
    }
    return [pscustomobject]@{
        backend_service = $backend[0]
        backend_owned = $backendOwned
        worker_owned = $workerOwned
    }
}

function Get-NonTargetProcessSnapshot {
    param([Parameter(Mandatory = $true)][object[]]$Owned)

    $records = @(
        foreach ($entry in @($Owned | Where-Object {
            $_.purpose -notin @("backend", "runner-worker")
        })) {
            $process = Get-Process -Id ([int]$entry.pid) -ErrorAction Stop
            $cim = Get-CimInstance Win32_Process `
                -Filter "ProcessId = $([int]$entry.pid)" -ErrorAction Stop
            $recordedStart = [DateTimeOffset]::Parse([string]$entry.started_at).UtcDateTime
            $actualStart = $process.StartTime.ToUniversalTime()
            if ([Math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -ge 1 -or
                [int]$cim.ParentProcessId -ne [int]$entry.parent_pid) {
                throw "A protected non-target process identity changed."
            }
            [ordered]@{
                pid = [int]$process.Id
                parent_pid = [int]$cim.ParentProcessId
                process_name = [string]$process.ProcessName
                purpose = [string]$entry.purpose
                started_at = $actualStart.ToString("o")
                command_sha256 = Get-CommandHash -CommandLine ([string]$cim.CommandLine)
            }
        }
    )
    return @($records | Sort-Object purpose,pid)
}

function Get-ProtectedListenerSnapshot {
    return [ordered]@{
        frontend_5173 = @(Get-PortListenerIds -Port 5173)
        demo_8765 = @(Get-PortListenerIds -Port 8765)
    }
}

function Test-JsonEqual {
    param($Left, $Right)

    return ($Left | ConvertTo-Json -Depth 30 -Compress) -eq
        ($Right | ConvertTo-Json -Depth 30 -Compress)
}

function Wait-BackendHealth {
    param([int]$TimeoutSeconds = 60)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $payload = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" `
                -Method Get -TimeoutSec 4
            if ($payload.status -eq "ok" -and $payload.service -eq "backend") {
                return $true
            }
        }
        catch {
            # The new Backend may still be booting.
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Test-RequiredOpenApiPaths {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/openapi.json" `
            -Method Get -TimeoutSec 8 -UseBasicParsing
        $document = $response.Content | ConvertFrom-Json -AsHashtable
        $paths = @($document.paths.Keys)
        return @($requiredOpenApiPaths | Where-Object { $_ -notin $paths }).Count -eq 0
    }
    catch {
        return $false
    }
}

function Stop-ExactTargetTree {
    param(
        [Parameter(Mandatory = $true)][object[]]$Entries,
        [Parameter(Mandatory = $true)][ValidateSet("backend", "runner-worker")]
        [string]$Purpose,
        [int]$PreferredFirstPid = 0,
        [switch]$LegacyWorker
    )

    $ids = @($Entries | ForEach-Object { [int]$_.pid })
    $order = @()
    if ($PreferredFirstPid -ne 0) {
        $order += @($Entries | Where-Object { [int]$_.pid -eq $PreferredFirstPid })
    }
    $order += @(
        $Entries |
            Where-Object { [int]$_.pid -ne $PreferredFirstPid -and [int]$_.parent_pid -in $ids }
    )
    $order += @(
        $Entries |
            Where-Object { [int]$_.pid -ne $PreferredFirstPid -and [int]$_.parent_pid -notin $ids }
    )
    foreach ($entry in $order) {
        if ($null -ne (Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue)) {
            Assert-RecordedTargetIdentity -Entry $entry `
                -AllowLegacyWorkerRecord:$LegacyWorker | Out-Null
            Stop-Process -Id ([int]$entry.pid) -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 250
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $remaining = @(
            $Entries | Where-Object {
                $process = Get-Process -Id ([int]$_.pid) -ErrorAction SilentlyContinue
                if ($null -eq $process) {
                    $false
                }
                else {
                    $recorded = [DateTimeOffset]::Parse([string]$_.started_at).UtcDateTime
                    [Math]::Abs(
                        ($process.StartTime.ToUniversalTime() - $recorded).TotalMilliseconds
                    ) -lt 1
                }
            }
        )
        if ($remaining.Count -gt 0) {
            Start-Sleep -Milliseconds 250
        }
    } while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)
    if ($remaining.Count -gt 0) {
        throw "An exact owned $Purpose process did not stop."
    }
}

try {
    $stage = "validate package inputs"
    foreach ($requiredPath in @(
        $readyPath,
        $ownedPath,
        $preflightPath,
        $freezePath,
        $pythonPath,
        $runnerConfigurationPath,
        $runnerReadinessPath,
        $p6ReadinessPath
    )) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "A required P6-L1 input is unavailable."
        }
    }
    New-Item -ItemType Directory -Path $validationDirectory -Force | Out-Null

    $stage = "validate fresh read-only preflight"
    $preflight = Read-JsonAsHashtable -Path $preflightPath
    $preflightAt = [DateTimeOffset]::Parse([string]$preflight.checked_at_utc).UtcDateTime
    if ($preflight.package -ne $package -or $preflight.phase -ne "preflight" -or
        $preflight.ready -ne $true -or
        ([DateTime]::UtcNow - $preflightAt).TotalMinutes -gt 2) {
        throw "The P6-L1 preflight is missing, stale, or not ready."
    }

    $stage = "confirm all controller-frozen source hashes"
    $freeze = Read-JsonAsHashtable -Path $freezePath
    if ($freeze.package -ne $package -or $freeze.source_hashes.Count -ne 19) {
        throw "The controller freeze is invalid."
    }
    $confirmationUtc = [DateTime]::UtcNow.ToString("o")
    $confirmedHashes = [ordered]@{}
    foreach ($entry in $freeze.source_hashes.GetEnumerator()) {
        $actual = (Get-FileHash -LiteralPath (Join-Path $workspaceRoot $entry.Key) `
            -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne [string]$entry.Value) {
            throw "A controller-frozen source hash changed."
        }
        $confirmedHashes[$entry.Key] = $actual
    }

    $stage = "load and validate owned target manifests"
    $ready = Read-JsonAsHashtable -Path $readyPath
    $owned = @(Read-JsonAsHashtable -Path $ownedPath)
    $targets = Assert-ReadyTargetTrees -Ready $ready -Owned $owned
    $oldBackendEntries = @($targets.backend_owned)
    $oldWorkerEntries = @($targets.worker_owned)

    $stage = "validate exact live Backend tree"
    $oldBackendTree = @(
        foreach ($entry in $oldBackendEntries) {
            Assert-RecordedTargetIdentity -Entry $entry
        }
    )
    $backendIds = @($oldBackendEntries | ForEach-Object { [int]$_.pid })
    $oldBackendRoots = @(
        $oldBackendTree | Where-Object { [int]$_.parent_pid -notin $backendIds }
    )
    if ($oldBackendRoots.Count -ne 1) {
        throw "The owned Backend does not describe one exact tree."
    }
    $oldBackendLiveTree = @(
        Get-TargetDescendants -RootProcessId ([int]$oldBackendRoots[0].pid) `
            -Purpose "backend"
    )
    if ((@($oldBackendLiveTree.pid | Sort-Object) -join ",") -ne
        (@($backendIds | Sort-Object) -join ",")) {
        throw "The live Backend descendants differ from the owned ledger."
    }
    $oldBackendListener = Get-SinglePortListener -Port 8000
    if ($null -eq $oldBackendListener -or
        [int]$targets.backend_service.pid -ne $oldBackendListener -or
        $oldBackendListener -notin $backendIds) {
        throw "Port 8000 is not owned by the recorded Backend tree."
    }

    $stage = "validate exact live Worker tree"
    $oldWorkerTree = @(
        foreach ($entry in $oldWorkerEntries) {
            Assert-RecordedTargetIdentity -Entry $entry -AllowLegacyWorkerRecord
        }
    )
    $workerInventory = Get-WorkerInventory
    $oldWorkerIds = @($oldWorkerEntries | ForEach-Object { [int]$_.pid } | Sort-Object)
    $inventoryWorkerIds = @($workerInventory.processes | ForEach-Object { [int]$_.pid } | Sort-Object)
    if ($workerInventory.logical_count -ne 1 -or
        ($oldWorkerIds -join ",") -ne ($inventoryWorkerIds -join ",")) {
        throw "The live Worker inventory differs from the exact owned Worker tree."
    }

    $stage = "load and validate local RabbitMQ environment"
    [xml]$runnerConfiguration = Get-Content -LiteralPath $runnerConfigurationPath
    $rabbitMqNode = @($runnerConfiguration.component.configuration.envs.env) |
        Where-Object { $_.name -eq "AI_TEST_RABBITMQ_URL" } |
        Select-Object -First 1
    $rabbitMqUrl = [string]$rabbitMqNode.value
    if ([string]::IsNullOrWhiteSpace($rabbitMqUrl)) {
        throw "The existing Runner configuration has no RabbitMQ URL."
    }
    $rabbitUri = [Uri]$rabbitMqUrl
    if ($rabbitUri.Scheme -notin @("amqp", "amqps") -or $rabbitUri.Port -ne 5672 -or
        $rabbitUri.Host -notin @("127.0.0.1", "localhost")) {
        throw "The existing Runner configuration is not scoped to local RabbitMQ."
    }

    $stage = "capture protected non-target runtime"
    $nonTargetProcessesBefore = @(Get-NonTargetProcessSnapshot -Owned $owned)
    $listenersBefore = Get-ProtectedListenerSnapshot
    $demoReady = @($ready.services | Where-Object { $_.name -eq "demo" })
    $frontendReady = @($ready.services | Where-Object { $_.name -eq "frontend" })
    if ($demoReady.Count -ne 1 -or $frontendReady.Count -ne 1 -or
        [int]$demoReady[0].pid -notin @($listenersBefore.demo_8765) -or
        [int]$frontendReady[0].pid -notin @($listenersBefore.frontend_5173)) {
        throw "The protected Demo or Frontend listener identity is not ready."
    }
    $nonTargetReadyBefore = [ordered]@{
        services = @($ready.services | Where-Object { $_.name -ne "backend" })
        middleware = $ready.middleware
        database = $ready.database
    }
    $nonTargetOwnedBefore = @(
        $owned | Where-Object { $_.purpose -notin @("backend", "runner-worker") }
    )

    $stage = "preserve readiness and ownership histories"
    $historyStamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
    $readyHistoryPath = Join-Path $servicesDirectory `
        "ready-history-pre-P6-L1-r1-$historyStamp.json"
    $ownedHistoryPath = Join-Path $servicesDirectory `
        "owned-processes-history-pre-P6-L1-r1-$historyStamp.json"
    Copy-Item -LiteralPath $readyPath -Destination $readyHistoryPath -ErrorAction Stop
    Copy-Item -LiteralPath $ownedPath -Destination $ownedHistoryPath -ErrorAction Stop

    $stage = "stop exact idle owned Worker"
    $oldWorkerChild = @(
        $oldWorkerEntries | Where-Object { [int]$_.parent_pid -in $oldWorkerIds }
    ) | Select-Object -First 1
    $preferredWorkerPid = if ($null -ne $oldWorkerChild) {
        [int]$oldWorkerChild.pid
    }
    else {
        0
    }
    Stop-ExactTargetTree -Entries $oldWorkerEntries -Purpose "runner-worker" `
        -PreferredFirstPid $preferredWorkerPid -LegacyWorker
    if ((Get-WorkerInventory).logical_count -ne 0) {
        throw "A Worker process remains after the exact owned Worker stop."
    }

    $stage = "stop exact owned Backend"
    Stop-ExactTargetTree -Entries $oldBackendEntries -Purpose "backend" `
        -PreferredFirstPid $oldBackendListener
    $portDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ($null -ne (Get-SinglePortListener -Port 8000) -and
        [DateTime]::UtcNow -lt $portDeadline) {
        Start-Sleep -Milliseconds 250
    }
    if ($null -ne (Get-SinglePortListener -Port 8000)) {
        throw "Port 8000 did not become free after the exact Backend stop."
    }

    $stage = "start controller-frozen Backend"
    $newBackendRoot = Start-Process -FilePath $pythonPath `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
        ) `
        -WorkingDirectory $backendDirectory `
        -WindowStyle Hidden `
        -PassThru
    if (-not (Wait-BackendHealth -TimeoutSeconds 60)) {
        throw "The replacement Backend did not become healthy."
    }
    if (-not (Test-RequiredOpenApiPaths)) {
        throw "The replacement Backend did not expose every reviewed P6 path."
    }
    Start-Sleep -Milliseconds 750
    $newBackendListener = Get-SinglePortListener -Port 8000
    $newBackendTree = @(
        Get-TargetDescendants -RootProcessId ([int]$newBackendRoot.Id) -Purpose "backend"
    )
    if ($null -eq $newBackendListener -or $newBackendListener -notin @($newBackendTree.pid)) {
        throw "The replacement Backend listener is not in the new owned tree."
    }

    $stage = "start same registered Worker identity"
    $newWorkerRoot = Start-Process -FilePath $pythonPath `
        -ArgumentList @("-m", "runner.main", "worker", "--web-slots", "1") `
        -WorkingDirectory $runnerDirectory `
        -WindowStyle Hidden `
        -Environment @{ AI_TEST_RABBITMQ_URL = $rabbitMqUrl } `
        -PassThru
    Remove-Variable rabbitMqUrl -ErrorAction SilentlyContinue
    $workerDeadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $newWorkerInventory = Get-WorkerInventory
    } while (($newWorkerInventory.logical_count -ne 1 -or
        [int]$newWorkerInventory.roots[0].pid -ne [int]$newWorkerRoot.Id) -and
        [DateTime]::UtcNow -lt $workerDeadline)
    if ($newWorkerInventory.logical_count -ne 1 -or
        [int]$newWorkerInventory.roots[0].pid -ne [int]$newWorkerRoot.Id) {
        throw "The replacement Worker did not form one exact logical tree."
    }
    $newWorkerTree = @($newWorkerInventory.processes)
    if ($newWorkerTree.Count -ne 2) {
        throw "The replacement Worker process count is not the expected two-process launcher tree."
    }

    $stage = "wait for Worker heartbeat and idle WEB capacity"
    $workerMinimumHeartbeat = $newWorkerRoot.StartTime.ToUniversalTime().ToString("o")
    $runnerReadiness = $null
    $readinessDeadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        $runnerReadinessText = (& $pythonPath $runnerReadinessPath `
            --minimum-heartbeat-utc $workerMinimumHeartbeat 2>$null | Out-String)
        if (-not [string]::IsNullOrWhiteSpace($runnerReadinessText)) {
            $runnerReadiness = $runnerReadinessText | ConvertFrom-Json -AsHashtable
        }
        if ($null -ne $runnerReadiness -and $runnerReadiness.ready -eq $true -and
            $runnerReadiness.web_slots.total -eq 1 -and
            $runnerReadiness.web_slots.available -eq 1) {
            break
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $readinessDeadline)
    if ($null -eq $runnerReadiness -or $runnerReadiness.ready -ne $true -or
        $runnerReadiness.runner_id -ne $runnerId -or
        $runnerReadiness.status -ne "ACTIVE" -or
        $runnerReadiness.online_status -ne "ONLINE" -or
        $runnerReadiness.web_capability -ne "READY" -or
        $runnerReadiness.web_slots.total -ne 1 -or
        $runnerReadiness.web_slots.available -ne 1 -or
        $runnerReadiness.active_run_count -ne 0) {
        throw "The replacement Worker did not reach the required ACTIVE/ONLINE WEB 1/1 state."
    }
    Start-Sleep -Seconds 1
    $stableWorkerInventory = Get-WorkerInventory
    if ($stableWorkerInventory.logical_count -ne 1 -or
        (@($stableWorkerInventory.processes.pid | Sort-Object) -join ",") -ne
        (@($newWorkerTree.pid | Sort-Object) -join ",")) {
        throw "The replacement Worker tree did not remain stable."
    }

    $stage = "verify target starts and post-start source hashes"
    $confirmationTime = [DateTimeOffset]::Parse($confirmationUtc).UtcDateTime
    foreach ($record in @($newBackendTree) + @($newWorkerTree)) {
        if ([DateTimeOffset]::Parse([string]$record.started_at).UtcDateTime -le
            $confirmationTime) {
            throw "A replacement target process predates final source confirmation."
        }
    }
    $postHashes = [ordered]@{}
    foreach ($entry in $confirmedHashes.GetEnumerator()) {
        $actual = (Get-FileHash -LiteralPath (Join-Path $workspaceRoot $entry.Key) `
            -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne [string]$entry.Value) {
            throw "A frozen source changed during the bounded load."
        }
        $postHashes[$entry.Key] = $actual
    }

    $stage = "verify protected runtime remained untouched"
    $listenersAfter = Get-ProtectedListenerSnapshot
    $nonTargetProcessesAfter = @(Get-NonTargetProcessSnapshot -Owned $owned)
    $listenersUnchanged = Test-JsonEqual $listenersBefore $listenersAfter
    $nonTargetProcessesUnchanged = Test-JsonEqual `
        $nonTargetProcessesBefore $nonTargetProcessesAfter
    if (-not $listenersUnchanged -or -not $nonTargetProcessesUnchanged) {
        throw "A protected Frontend or Demo runtime identity changed."
    }

    $stage = "update only Backend and Worker ownership/readiness records"
    $retainedOwned = @(
        $owned | Where-Object { $_.purpose -notin @("backend", "runner-worker") }
    )
    $newOwned = @($retainedOwned) + @($newBackendTree) + @($newWorkerTree)
    $backendSourceHashes = [ordered]@{}
    $runnerSourceHashes = [ordered]@{}
    foreach ($entry in $confirmedHashes.GetEnumerator()) {
        if ($entry.Key.StartsWith("backend/")) {
            $backendSourceHashes[$entry.Key] = $entry.Value
        }
        elseif ($entry.Key.StartsWith("runner/")) {
            $runnerSourceHashes[$entry.Key] = $entry.Value
        }
    }
    $backendService = @($ready.services | Where-Object { $_.name -eq "backend" })[0]
    $newBackendListenerRecord = @(
        $newBackendTree | Where-Object { [int]$_.pid -eq [int]$newBackendListener }
    )[0]
    $backendService.pid = [int]$newBackendListener
    $backendService.started_at = [string]$newBackendListenerRecord.started_at
    $backendService.healthy = $true
    $backendService.ownership = "p5e-owned"
    $backendService.process_tree = @($newBackendTree)
    $backendService.working_directory = $backendDirectory
    $backendService.command_identity = "python-uvicorn-app.main-127.0.0.1-8000"
    $backendService.reload_package = $package
    $backendService.loaded_source_hashes = $backendSourceHashes

    $ready.runner.status = [string]$runnerReadiness.status
    $ready.runner.online_status = [string]$runnerReadiness.online_status
    $ready.runner.healthy = $true
    $ready.runner.logical_worker_count = 1
    $ready.runner.process_count = $newWorkerTree.Count
    $ready.runner.pids = @($newWorkerTree | ForEach-Object { [int]$_.pid })
    $ready.runner.process_tree = @($newWorkerTree)
    $ready.runner.web_capability = [string]$runnerReadiness.web_capability
    $ready.runner.web_slots = $runnerReadiness.web_slots
    $ready.runner.active_run_count = [int]$runnerReadiness.active_run_count
    $ready.runner.ownership = "p5e-owned"
    $ready.runner.started_at = [string]$newWorkerTree[0].started_at
    $ready.runner.last_heartbeat_at = [string]$runnerReadiness.last_heartbeat_at
    $ready.runner.rabbitmq_queue = "ai_test.runner.$runnerId.v1"
    $ready.runner.working_directory = $runnerDirectory
    $ready.runner.command_identity = "python-runner.main-worker-web-slots-1"
    $ready.runner.reload_package = $package
    $ready.runner.loaded_source_hashes = $runnerSourceHashes
    $ready.generated_at = [DateTime]::UtcNow.ToString("o")
    $ready.owned_processes = @($newOwned)
    $packageNote = "P6-L1/r1 reloaded only the precisely owned Backend and Worker."
    if ($packageNote -notin @($ready.notes)) {
        $ready.notes = @($ready.notes) + @($packageNote)
    }
    Write-JsonAtomic -Path $ownedPath -Value $newOwned
    Write-JsonAtomic -Path $readyPath -Value $ready

    $stage = "verify manifest scope after update"
    $currentReady = Read-JsonAsHashtable -Path $readyPath
    $currentOwned = @(Read-JsonAsHashtable -Path $ownedPath)
    $nonTargetReadyAfter = [ordered]@{
        services = @($currentReady.services | Where-Object { $_.name -ne "backend" })
        middleware = $currentReady.middleware
        database = $currentReady.database
    }
    $nonTargetOwnedAfter = @(
        $currentOwned | Where-Object { $_.purpose -notin @("backend", "runner-worker") }
    )
    $nonTargetReadyUnchanged = Test-JsonEqual `
        $nonTargetReadyBefore $nonTargetReadyAfter
    $nonTargetOwnedUnchanged = Test-JsonEqual `
        $nonTargetOwnedBefore $nonTargetOwnedAfter
    if (-not $nonTargetReadyUnchanged -or -not $nonTargetOwnedUnchanged) {
        throw "A non-target readiness or ownership record changed."
    }

    $stage = "write bounded load record"
    $loadRecord = [ordered]@{
        schema_version = 1
        package = $package
        status = "SERVICES_LOADED_PENDING_FINAL_READ_ONLY_VERIFICATION"
        completed_at_utc = [DateTime]::UtcNow.ToString("o")
        source_confirmation_utc = $confirmationUtc
        source_hashes = $confirmedHashes
        post_start_source_hashes = $postHashes
        old_backend = [ordered]@{
            listener_pid = $oldBackendListener
            process_tree = @($oldBackendTree)
        }
        new_backend = [ordered]@{
            listener_pid = $newBackendListener
            process_tree = @($newBackendTree)
        }
        old_worker = [ordered]@{
            runner_id = $runnerId
            process_tree = @($oldWorkerTree)
        }
        new_worker = [ordered]@{
            runner_id = $runnerId
            process_tree = @($newWorkerTree)
            readiness = $runnerReadiness
        }
        history = [ordered]@{
            ready_file = Split-Path -Leaf $readyHistoryPath
            ready_sha256 = (Get-FileHash -LiteralPath $readyHistoryPath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
            owned_file = Split-Path -Leaf $ownedHistoryPath
            owned_sha256 = (Get-FileHash -LiteralPath $ownedHistoryPath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        protected_runtime = [ordered]@{
            listeners_before = $listenersBefore
            listeners_after = $listenersAfter
            non_target_processes_before = @($nonTargetProcessesBefore)
            non_target_processes_after = @($nonTargetProcessesAfter)
        }
        protection_checks = [ordered]@{
            all_19_hashes_unchanged_during_load = Test-JsonEqual $confirmedHashes $postHashes
            backend_openapi_paths_loaded = Test-RequiredOpenApiPaths
            worker_same_runner_active_online_web_1_of_1 = (
                $runnerReadiness.ready -eq $true -and
                $runnerReadiness.runner_id -eq $runnerId -and
                $runnerReadiness.web_slots.total -eq 1 -and
                $runnerReadiness.web_slots.available -eq 1
            )
            frontend_demo_listener_sets_unchanged = $listenersUnchanged
            frontend_demo_process_identities_unchanged = $nonTargetProcessesUnchanged
            non_target_ready_manifest_unchanged = $nonTargetReadyUnchanged
            non_target_owned_ledger_unchanged = $nonTargetOwnedUnchanged
        }
        safety = [ordered]@{
            stopped_validated_worker_pids = @($oldWorkerIds)
            stopped_validated_backend_pids = @($backendIds)
            restarted_only_backend_and_worker = $true
            business_posts = 0
            allowed_normal_login_posts = 1
            ai_requests = 0
            message_get_publish_ack_redrive_operations = 0
            demo_state_changes = 0
            container_restarts = 0
            runner_registrations = 0
        }
    }
    Write-JsonAtomic -Path $loadPath -Value $loadRecord

    $stage = "run final read-only P6-L1 verification"
    $verificationSummary = (& $pythonPath $p6ReadinessPath `
        --phase post `
        --confirmation-utc $confirmationUtc `
        --output $verificationPath | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "The final P6-L1 read-only verifier did not pass."
    }
    $verification = Read-JsonAsHashtable -Path $verificationPath
    if ($verification.ready -ne $true) {
        throw "The final P6-L1 verification result is not ready."
    }
    $loadRecord.status = "PASSED"
    $loadRecord.final_verification = [ordered]@{
        file = Split-Path -Leaf $verificationPath
        sha256 = (Get-FileHash -LiteralPath $verificationPath `
            -Algorithm SHA256).Hash.ToLowerInvariant()
        ready = $true
        check_count = $verification.checks.Count
    }
    Write-JsonAtomic -Path $loadPath -Value $loadRecord

    $readyMessage = (
        "READY|package={0}|old_backend={1}|new_backend={2}|" +
        "old_worker={3}|new_worker={4}|confirmation={5}"
    )
    Write-Output ($readyMessage -f
        $package,
        $oldBackendListener,
        $newBackendListener,
        ($oldWorkerIds -join ","),
        (@($newWorkerTree.pid) -join ","),
        $confirmationUtc)
    Write-Output ("RECORD|{0}" -f $loadPath)
    Write-Output ("VERIFICATION|{0}" -f $verificationPath)
}
catch {
    $failure = [ordered]@{
        schema_version = 1
        package = $package
        status = "STOPPED"
        stopped_at_utc = [DateTime]::UtcNow.ToString("o")
        stage = $stage
        error_type = $_.Exception.GetType().Name
        secret_values_written = $false
    }
    try {
        New-Item -ItemType Directory -Path $validationDirectory -Force | Out-Null
        Write-JsonAtomic -Path (Join-Path $validationDirectory "failure.json") `
            -Value $failure
    }
    catch {
        # Preserve the original bounded failure.
    }
    Remove-Variable rabbitMqUrl -ErrorAction SilentlyContinue
    Write-Error (
        "P6-L1 load stopped during safe stage: {0} ({1})." -f
        $stage,$failure.error_type
    )
    exit 1
}
