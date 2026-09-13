[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$stage = "initialization"

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendDirectory = Join-Path $workspaceRoot "backend"
$servicesDirectory = Join-Path $workspaceRoot ".codex-validation\p5e-services"
$proofDirectory = Join-Path $workspaceRoot ".codex-validation\p5f-l2"
$readyPath = Join-Path $servicesDirectory "ready.json"
$ownedPath = Join-Path $servicesDirectory "owned-processes.json"
$preflightPath = Join-Path $proofDirectory "preflight.json"
$reloadRecordPath = Join-Path $proofDirectory "backend-reload.json"
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$package = "P5F-L2/r1"
$expectedPreviousPackage = "P5E-H6/r1"
$expectedHashes = [ordered]@{
    "backend/app/core/redaction.py" = "458d05f7299b56cd277ec6b63ee3082527c34986b167f7c5c3e9432f7a74ddbb"
    "backend/app/core/logging.py" = "4736ef0407ba5ba41473ef3d00b17f55f8acaa4dea6b9fad44e031675bebca43"
    "backend/app/modules/prompt_center/service.py" = "2db4cfbbcc949fdb0ace73f2ce13f4c69b71715e6007ffada65397f5856a2c26"
}

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
    $json = $Value | ConvertTo-Json -Depth 16
    [IO.File]::WriteAllText($temporaryPath, $json, [Text.UTF8Encoding]::new($false))
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

function Get-LiveBackendRecord {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $cimProcess = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    if (-not (Test-BackendCommandIdentity -CimProcess $cimProcess)) {
        throw "A candidate Backend process does not match the approved command identity."
    }
    return [ordered]@{
        pid = [int]$process.Id
        parent_pid = [int]$cimProcess.ParentProcessId
        process_name = [string]$process.ProcessName
        purpose = "backend"
        started_at = $process.StartTime.ToUniversalTime().ToString("o")
        command_sha256 = Get-CommandHash -CommandLine ([string]$cimProcess.CommandLine)
        command_identity = if ([string]$cimProcess.Name -eq "conhost.exe") {
            "backend-console-host"
        } else {
            "python-uvicorn-app.main-127.0.0.1-8000"
        }
    }
}

function Assert-RecordedProcessIdentity {
    param([Parameter(Mandatory = $true)]$Entry)

    $actual = Get-LiveBackendRecord -ProcessId ([int]$Entry.pid)
    $recordedStart = [DateTimeOffset]::Parse([string]$Entry.started_at).UtcDateTime
    $actualStart = [DateTimeOffset]::Parse([string]$actual.started_at).UtcDateTime
    if ([Math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -ge 1) {
        throw "A candidate Backend PID has been reused."
    }
    if ([int]$actual.parent_pid -ne [int]$Entry.parent_pid -or
        [string]$actual.command_identity -ne [string]$Entry.command_identity -or
        [string]$actual.command_sha256 -ne [string]$Entry.command_sha256) {
        throw "The recorded Backend process identity has changed."
    }
    return $actual
}

function Get-Port8000Listener {
    $listenerIds = @(
        Get-NetTCPConnection -State Listen -LocalPort 8000 `
            -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($listenerIds.Count -eq 0) {
        return $null
    }
    if ($listenerIds.Count -ne 1) {
        throw "Port 8000 does not have exactly one owning process."
    }
    return [int]$listenerIds[0]
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
            # The replacement Backend may still be booting.
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-DescendantBackendProcesses {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

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
            $records.Add((Get-LiveBackendRecord -ProcessId $currentId))
        }
        foreach ($child in @(
            $snapshot | Where-Object { [int]$_.ParentProcessId -eq $currentId }
        )) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($records)
}

function Assert-ReadyTreeMatchesOwned {
    param(
        [Parameter(Mandatory = $true)]$BackendService,
        [Parameter(Mandatory = $true)][object[]]$OwnedEntries
    )

    $readyTree = @($BackendService.process_tree)
    $readyIds = @($readyTree | ForEach-Object { [int]$_.pid } | Sort-Object)
    $ownedIds = @($OwnedEntries | ForEach-Object { [int]$_.pid } | Sort-Object)
    if (($readyIds -join ",") -ne ($ownedIds -join ",")) {
        throw "The ready Backend tree does not match the owned ledger."
    }
    foreach ($readyEntry in $readyTree) {
        $ownedEntry = $OwnedEntries |
            Where-Object { [int]$_.pid -eq [int]$readyEntry.pid } |
            Select-Object -First 1
        if ($null -eq $ownedEntry -or
            [int]$readyEntry.parent_pid -ne [int]$ownedEntry.parent_pid -or
            [string]$readyEntry.started_at -ne [string]$ownedEntry.started_at -or
            [string]$readyEntry.command_identity -ne [string]$ownedEntry.command_identity -or
            [string]$readyEntry.command_sha256 -ne [string]$ownedEntry.command_sha256) {
            throw "The ready and owned Backend identity records differ."
        }
    }
}

try {
    $stage = "validate L2 preflight"
    foreach ($requiredPath in @($readyPath, $ownedPath, $preflightPath, $pythonPath)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "A required L2 input is unavailable."
        }
    }
    $preflight = Read-JsonAsHashtable -Path $preflightPath
    $preflightAt = [DateTimeOffset]::Parse([string]$preflight.checked_at_utc).UtcDateTime
    if ($preflight.package -ne $package -or $preflight.phase -ne "preflight" -or
        $preflight.ready -ne $true -or
        ([DateTime]::UtcNow - $preflightAt).TotalMinutes -gt 2) {
        throw "The L2 read-only preflight is missing, stale, or not ready."
    }

    $stage = "confirm reviewed L1 source hashes"
    $confirmationUtc = [DateTime]::UtcNow.ToString("o")
    $confirmedHashes = [ordered]@{}
    foreach ($entry in $expectedHashes.GetEnumerator()) {
        $path = Join-Path $workspaceRoot $entry.Key
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).
            Hash.ToLowerInvariant()
        if ($actual -ne $entry.Value) {
            throw "An L1 source hash does not match the reviewed delivery."
        }
        $confirmedHashes[$entry.Key] = $actual
    }

    $stage = "validate exact prior Backend records"
    $ready = Read-JsonAsHashtable -Path $readyPath
    $owned = @(Read-JsonAsHashtable -Path $ownedPath)
    $backendServices = @($ready.services | Where-Object { $_.name -eq "backend" })
    if ($backendServices.Count -ne 1) {
        throw "The ready manifest does not contain exactly one Backend service."
    }
    $backendService = $backendServices[0]
    $stage = "validate prior Backend service identity"
    if ($backendService.ownership -ne "p5e-owned" -or
        $backendService.reload_package -ne $expectedPreviousPackage -or
        [string]$backendService.working_directory -ne $backendDirectory -or
        $backendService.command_identity -ne "python-uvicorn-app.main-127.0.0.1-8000") {
        throw "The ready manifest does not prove the expected prior Backend identity."
    }
    $oldEntries = @($owned | Where-Object { $_.purpose -eq "backend" })
    if ($oldEntries.Count -eq 0) {
        throw "The owned ledger has no Backend records."
    }
    $stage = "compare ready and owned Backend trees"
    Assert-ReadyTreeMatchesOwned -BackendService $backendService `
        -OwnedEntries $oldEntries

    $stage = "inspect port 8000 ownership"
    $listenerPid = Get-Port8000Listener
    $oldTree = @()
    $oldIds = @($oldEntries | ForEach-Object { [int]$_.pid })
    if ($null -eq $listenerPid) {
        $liveRecordedIds = @(
            $oldIds | Where-Object {
                $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue)
            }
        )
        if ($liveRecordedIds.Count -ne 0) {
            throw "Backend has no listener but recorded Backend processes remain live."
        }
        $mode = "recover"
        $oldTree = @($oldEntries)
    } else {
        $stage = "match listener to prior Backend records"
        if ([int]$backendService.pid -ne $listenerPid -or
            $listenerPid -notin $oldIds) {
            throw "The port 8000 listener is unfamiliar; no process was stopped."
        }
        $validatedTree = [Collections.Generic.List[object]]::new()
        foreach ($entry in $oldEntries) {
            $stage = "validate live prior Backend process identity"
            $validatedTree.Add((Assert-RecordedProcessIdentity -Entry $entry))
        }
        $oldTree = @($validatedTree)
        $listenerEntry = $oldTree |
            Where-Object { [int]$_.pid -eq $listenerPid } |
            Select-Object -First 1
        if ($null -eq $listenerEntry -or
            $listenerEntry.command_identity -ne
                "python-uvicorn-app.main-127.0.0.1-8000") {
            throw "The owned listener is not the approved Backend command."
        }
        $rootEntries = @(
            $oldTree | Where-Object { [int]$_.parent_pid -notin $oldIds }
        )
        $stage = "validate live prior Backend tree root"
        if ($rootEntries.Count -ne 1) {
            throw "The owned Backend ledger does not describe exactly one process tree."
        }
        $liveTree = @(Get-DescendantBackendProcesses `
            -RootProcessId ([int]$rootEntries[0].pid))
        $liveTreeIds = @($liveTree | ForEach-Object { [int]$_.pid } | Sort-Object)
        $ownedTreeIds = @($oldIds | Sort-Object)
        $stage = "compare live prior Backend descendants"
        if (($liveTreeIds -join ",") -ne ($ownedTreeIds -join ",")) {
            throw "The live Backend descendants do not exactly match the owned ledger."
        }
        $mode = "reload"
    }

    $stage = "preserve current service manifests"
    $historyStamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
    $readyHistoryPath = Join-Path $servicesDirectory `
        "ready-history-pre-P5F-L2-r1-$historyStamp.json"
    $ownedHistoryPath = Join-Path $servicesDirectory `
        "owned-processes-history-pre-P5F-L2-r1-$historyStamp.json"
    Copy-Item -LiteralPath $readyPath -Destination $readyHistoryPath -ErrorAction Stop
    Copy-Item -LiteralPath $ownedPath -Destination $ownedHistoryPath -ErrorAction Stop

    if ($mode -eq "reload") {
        $stage = "stop exact owned Backend"
        $listenerEntry = $oldEntries |
            Where-Object { [int]$_.pid -eq $listenerPid } |
            Select-Object -First 1
        Assert-RecordedProcessIdentity -Entry $listenerEntry | Out-Null
        Stop-Process -Id $listenerPid -Force -ErrorAction Stop
        $stopDeadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 250
            $remainingIds = @(
                $oldIds | Where-Object {
                    $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue)
                }
            )
        } while ($remainingIds.Count -gt 0 -and [DateTime]::UtcNow -lt $stopDeadline)
        foreach ($remainingId in $remainingIds) {
            $entry = $oldEntries |
                Where-Object { [int]$_.pid -eq [int]$remainingId } |
                Select-Object -First 1
            Assert-RecordedProcessIdentity -Entry $entry | Out-Null
            Stop-Process -Id $remainingId -Force -ErrorAction Stop
        }
        $portDeadline = [DateTime]::UtcNow.AddSeconds(10)
        while ($null -ne (Get-Port8000Listener) -and
            [DateTime]::UtcNow -lt $portDeadline) {
            Start-Sleep -Milliseconds 250
        }
        if ($null -ne (Get-Port8000Listener)) {
            throw "Port 8000 did not become free after the exact owned Backend stopped."
        }
    }

    $stage = "start L1 reviewed Backend"
    $newRoot = Start-Process -FilePath $pythonPath `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
        ) `
        -WorkingDirectory $backendDirectory `
        -WindowStyle Hidden `
        -PassThru
    if (-not (Wait-BackendHealth -TimeoutSeconds 60)) {
        throw "The replacement Backend did not become healthy."
    }
    $newListenerPid = Get-Port8000Listener
    if ($null -eq $newListenerPid) {
        throw "The replacement Backend has no listener."
    }
    $newTree = @(Get-DescendantBackendProcesses -RootProcessId $newRoot.Id)
    if ($newListenerPid -notin @($newTree.pid)) {
        throw "The replacement listener is not a descendant of the L2 root process."
    }
    $confirmationTime = [DateTimeOffset]::Parse($confirmationUtc).UtcDateTime
    foreach ($record in $newTree) {
        if ([DateTimeOffset]::Parse([string]$record.started_at).UtcDateTime -le
            $confirmationTime) {
            throw "A replacement Backend process predates the L1 hash confirmation."
        }
    }

    $stage = "update Backend ownership records only"
    $retainedOwned = @($owned | Where-Object { $_.purpose -ne "backend" })
    $newOwned = @($retainedOwned) + @($newTree)
    Write-JsonAtomic -Path $ownedPath -Value $newOwned

    $newListener = $newTree |
        Where-Object { [int]$_.pid -eq [int]$newListenerPid } |
        Select-Object -First 1
    $backendService.pid = [int]$newListenerPid
    $backendService.started_at = [string]$newListener.started_at
    $backendService.ownership = "p5e-owned"
    $backendService.process_tree = @($newTree)
    $backendService.working_directory = $backendDirectory
    $backendService.command_identity = "python-uvicorn-app.main-127.0.0.1-8000"
    $backendService.reload_package = $package
    $backendService.loaded_source_hashes = $confirmedHashes
    Write-JsonAtomic -Path $readyPath -Value $ready

    $reloadRecord = [ordered]@{
        schema_version = 1
        package = $package
        mode = $mode
        completed_at_utc = [DateTime]::UtcNow.ToString("o")
        source_confirmation_utc = $confirmationUtc
        source_hashes = $confirmedHashes
        working_directory = $backendDirectory
        command_identity = "python-uvicorn-app.main-127.0.0.1-8000"
        old_backend = [ordered]@{
            listener_pid = $listenerPid
            process_tree = @($oldTree)
        }
        new_backend = [ordered]@{
            listener_pid = $newListenerPid
            process_tree = @($newTree)
        }
        history = [ordered]@{
            ready_file = Split-Path -Leaf $readyHistoryPath
            ready_sha256 = (Get-FileHash -LiteralPath $readyHistoryPath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
            owned_file = Split-Path -Leaf $ownedHistoryPath
            owned_sha256 = (Get-FileHash -LiteralPath $ownedHistoryPath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        safety = [ordered]@{
            stopped_only_validated_backend_pids = if ($mode -eq "reload") {
                @($oldIds)
            } else {
                @()
            }
            restarted_other_services = $false
            business_posts = 0
            ai_requests = 0
            message_operations = 0
            demo_state_changes = 0
        }
    }
    Write-JsonAtomic -Path $reloadRecordPath -Value $reloadRecord
    Write-Output ("READY|mode={0}|old_listener={1}|new_listener={2}|confirmation={3}" -f `
        $mode,$listenerPid,$newListenerPid,$confirmationUtc)
    Write-Output ("MANIFEST|{0}" -f $readyPath)
    Write-Output ("RECORD|{0}" -f $reloadRecordPath)
}
catch {
    Write-Error (
        "P5F-L2 Backend load failed during safe stage: {0} ({1})." -f `
            $stage,$_.Exception.GetType().Name
    )
    exit 1
}
