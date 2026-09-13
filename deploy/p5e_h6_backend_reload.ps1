[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$stage = "initialization"

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendDirectory = Join-Path $workspaceRoot "backend"
$validationDirectory = Join-Path $workspaceRoot ".codex-validation\p5e-services"
$readyPath = Join-Path $validationDirectory "ready.json"
$ownedPath = Join-Path $validationDirectory "owned-processes.json"
$preflightPath = Join-Path $validationDirectory "h6-preflight.json"
$reloadRecordPath = Join-Path $validationDirectory "h6-backend-reload.json"
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$package = "P5E-H6/r1"
$expectedHashes = [ordered]@{
    "backend/app/modules/ai_gateway/service.py" = "4479107a11a8ba4b45198e3b845ac57b68f0cb587cc02ff8b46594415c835729"
    "backend/app/modules/web_failure_analysis/service.py" = "729f707e8a0d9efa3b8aab7e0802013a80c55e2d6bb67eed9ced4cd807833453"
    "deploy/p5e_resume_failure_analysis.py" = "9b387663be3b888e85e4cd7fdb1c446043c3545e5dd1407ba7808f5b6706187a"
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
    $json = $Value | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($temporaryPath, $json, [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temporaryPath, $Path, $true)
}

function Get-UtcStartText {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    return (Get-Process -Id $ProcessId -ErrorAction Stop).
        StartTime.ToUniversalTime().ToString("o")
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

function Get-LiveProcessRecord {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

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
        purpose = $Purpose
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

    $actual = Get-LiveProcessRecord -ProcessId ([int]$Entry.pid) -Purpose "backend"
    $recordedStart = [DateTimeOffset]::Parse([string]$Entry.started_at).UtcDateTime
    $actualStart = [DateTimeOffset]::Parse([string]$actual.started_at).UtcDateTime
    if ([Math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -ge 1) {
        throw "A candidate Backend PID has been reused."
    }
    if ([int]$actual.parent_pid -ne [int]$Entry.parent_pid) {
        throw "The recorded Backend parent-child relationship has changed."
    }
    return $actual
}

function Get-Port8000Listener {
    $listeners = @(
        Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
    )
    if ($listeners.Count -eq 0) {
        return $null
    }
    if ($listeners.Count -ne 1) {
        throw "Port 8000 does not have exactly one listener."
    }
    return [int]$listeners[0].OwningProcess
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
            $records.Add((
                Get-LiveProcessRecord -ProcessId $currentId -Purpose "backend"
            ))
        }
        foreach ($child in @(
            $snapshot | Where-Object { [int]$_.ParentProcessId -eq $currentId }
        )) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($records)
}

try {
    $stage = "validate H6 preflight"
    foreach ($requiredPath in @($readyPath, $ownedPath, $preflightPath, $pythonPath)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "A required H6 input is unavailable."
        }
    }
    $preflight = Read-JsonAsHashtable -Path $preflightPath
    $preflightAt = [DateTimeOffset]::Parse([string]$preflight.checked_at_utc).UtcDateTime
    if ($preflight.package -ne $package -or $preflight.phase -ne "preflight" -or
        $preflight.ready -ne $true -or
        ([DateTime]::UtcNow - $preflightAt).TotalMinutes -gt 2) {
        throw "The H6 read-only preflight is missing, stale, or not ready."
    }

    $stage = "confirm H5 hashes"
    $confirmationUtc = [DateTime]::UtcNow.ToString("o")
    $confirmedHashes = [ordered]@{}
    foreach ($entry in $expectedHashes.GetEnumerator()) {
        $path = Join-Path $workspaceRoot $entry.Key
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).
            Hash.ToLowerInvariant()
        if ($actual -ne $entry.Value) {
            throw "An H5 source hash does not match the reviewed delivery."
        }
        $confirmedHashes[$entry.Key] = $actual
    }

    $stage = "validate exact old Backend ownership"
    $ready = Read-JsonAsHashtable -Path $readyPath
    $owned = @(Read-JsonAsHashtable -Path $ownedPath)
    $backendService = @(
        $ready.services | Where-Object { $_.name -eq "backend" }
    ) | Select-Object -First 1
    if ($null -eq $backendService -or $backendService.ownership -ne "p5e-owned") {
        throw "The ready manifest does not prove P5E ownership of Backend."
    }
    $listenerPid = Get-Port8000Listener
    if ($null -eq $listenerPid -or [int]$backendService.pid -ne $listenerPid) {
        throw "The live Backend listener does not match the ready manifest."
    }
    $oldEntries = @($owned | Where-Object { $_.purpose -eq "backend" })
    if ($oldEntries.Count -eq 0 -or $listenerPid -notin @($oldEntries.pid)) {
        throw "The live Backend listener is not in the owned ledger."
    }
    $oldTree = [Collections.Generic.List[object]]::new()
    foreach ($entry in $oldEntries) {
        $oldTree.Add((Assert-RecordedProcessIdentity -Entry $entry))
    }
    $oldIds = @($oldTree | ForEach-Object { $_.pid })
    if (@($oldTree | Where-Object {
        $_.pid -eq $listenerPid -and $_.command_identity -like "python-uvicorn-*"
    }).Count -ne 1) {
        throw "The owned listener is not the approved Backend command."
    }

    $stage = "preserve H2 manifests"
    $historyStamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
    $readyHistoryPath = Join-Path $validationDirectory `
        "ready-history-pre-P5E-H6-r1-$historyStamp.json"
    $ownedHistoryPath = Join-Path $validationDirectory `
        "owned-processes-history-pre-P5E-H6-r1-$historyStamp.json"
    Copy-Item -LiteralPath $readyPath -Destination $readyHistoryPath -ErrorAction Stop
    Copy-Item -LiteralPath $ownedPath -Destination $ownedHistoryPath -ErrorAction Stop

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
    while ($null -ne (Get-Port8000Listener) -and [DateTime]::UtcNow -lt $portDeadline) {
        Start-Sleep -Milliseconds 250
    }
    if ($null -ne (Get-Port8000Listener)) {
        throw "Port 8000 did not become free after the exact owned Backend stopped."
    }

    $stage = "start reviewed H5 Backend"
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
        throw "The replacement listener is not a descendant of the H6 root process."
    }
    $confirmationTime = [DateTimeOffset]::Parse($confirmationUtc).UtcDateTime
    foreach ($record in $newTree) {
        if ([DateTimeOffset]::Parse([string]$record.started_at).UtcDateTime -le $confirmationTime) {
            throw "A replacement Backend process predates the H5 hash confirmation."
        }
    }

    $stage = "update safe ownership manifests"
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
    $ready.generated_at = [DateTime]::UtcNow.ToString("o")
    $ready.notes = @($ready.notes) + @(
        "P5E-H6/r1 reloaded only the precisely owned Backend after an approved H5 hash confirmation."
    )
    Write-JsonAtomic -Path $readyPath -Value $ready

    $reloadRecord = [ordered]@{
        schema_version = 1
        package = $package
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
            ready_sha256 = (Get-FileHash -LiteralPath $readyHistoryPath -Algorithm SHA256).
                Hash.ToLowerInvariant()
            owned_file = Split-Path -Leaf $ownedHistoryPath
            owned_sha256 = (Get-FileHash -LiteralPath $ownedHistoryPath -Algorithm SHA256).
                Hash.ToLowerInvariant()
        }
        safety = [ordered]@{
            stopped_only_validated_backend_pids = @($oldIds)
            restarted_other_services = $false
            business_requests = 0
            ai_requests = 0
            message_operations = 0
            demo_state_changes = 0
        }
    }
    Write-JsonAtomic -Path $reloadRecordPath -Value $reloadRecord
    Write-Output ("RELOADED|old_listener={0}|new_listener={1}|confirmation={2}" -f `
        $listenerPid,$newListenerPid,$confirmationUtc)
    Write-Output ("READY|{0}" -f $readyPath)
    Write-Output ("RECORD|{0}" -f $reloadRecordPath)
}
catch {
    Write-Error (
        "P5E-H6 Backend reload failed during safe stage: {0} ({1})." -f `
            $stage,$_.Exception.GetType().Name
    )
    exit 1
}
