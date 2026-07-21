# Register the sensei-cycle 6-hourly Windows Task Scheduler job.
#   .\bin\register_sensei_task.ps1           register (default)
#   .\bin\register_sensei_task.ps1 -Remove   remove the task
#   .\bin\register_sensei_task.ps1 -Status   show current task state
param(
    [switch]$Remove,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

$TASK_NAME    = "OrderSamurai-SenseiCycle"
$TASK_FOLDER  = "\"
$MAX_TURNS    = 40
$PERIOD_HOURS = 6

# Offset by 1h from the ronin-daemon (which typically runs at :00 or :30).
# Adjust START_TIME if ronin-daemon uses a different offset.
$START_TIME   = "03:00"

$CLAUDE_EXE   = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $CLAUDE_EXE) {
    # Fallback: try common install locations
    $candidates = @(
        "$env:APPDATA\npm\claude.cmd",
        "$env:LOCALAPPDATA\Programs\claude\claude.exe",
        "$env:USERPROFILE\.local\bin\claude"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $CLAUDE_EXE = $c; break }
    }
}
if (-not $CLAUDE_EXE) {
    Write-Error "claude CLI not found. Install it first or add it to PATH."
}

$ORDER_SAMURAI_ROOT = $PSScriptRoot | Split-Path

# ── Status ─────────────────────────────────────────────────────────────────
if ($Status) {
    $t = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($t) {
        $info = Get-ScheduledTaskInfo -TaskName $TASK_NAME
        Write-Host "Task:       $TASK_NAME" -ForegroundColor Cyan
        Write-Host "State:      $($t.State)"
        Write-Host "LastRun:    $($info.LastRunTime)"
        Write-Host "LastResult: $($info.LastTaskResult)"
        Write-Host "NextRun:    $($info.NextRunTime)"
    } else {
        Write-Host "Task not registered." -ForegroundColor DarkGray
    }
    exit 0
}

# ── Remove ──────────────────────────────────────────────────────────────────
if ($Remove) {
    $t = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
        Write-Host "Removed: $TASK_NAME" -ForegroundColor Yellow
    } else {
        Write-Host "Task not found — nothing to remove." -ForegroundColor DarkGray
    }
    exit 0
}

# ── Register ────────────────────────────────────────────────────────────────
$action = New-ScheduledTaskAction `
    -Execute $CLAUDE_EXE `
    -Argument "--print -p `"/sensei-cycle`" --permission-mode acceptEdits --max-turns $MAX_TURNS" `
    -WorkingDirectory $ORDER_SAMURAI_ROOT

# Repeat every 6h starting at START_TIME (indefinite)
$trigger = New-ScheduledTaskTrigger -Daily -At $START_TIME
$trigger.RepetitionInterval    = [TimeSpan]::FromHours($PERIOD_HOURS)
$trigger.RepetitionDuration    = [TimeSpan]::MaxValue
$trigger.StopAtDurationEnd     = $false

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew `
    -WakeToRun:$false

$env_vars = @(
    New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
)

$task = New-ScheduledTask `
    -Action  $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal (New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited)

# Set ORDER_SAMURAI_ROOT in the task's environment
# (Task Scheduler doesn't expose env vars natively; we pass it via the action argument instead)
$task.Actions[0].Arguments = `
    "--print -p `"/sensei-cycle`" --permission-mode acceptEdits --max-turns $MAX_TURNS"

Register-ScheduledTask `
    -TaskName   $TASK_NAME `
    -TaskPath   $TASK_FOLDER `
    -InputObject $task `
    -Force | Out-Null

Write-Host ""
Write-Host "Registered: $TASK_NAME" -ForegroundColor Green
Write-Host "  Runs:     every ${PERIOD_HOURS}h starting $START_TIME"
Write-Host "  Command:  claude --print -p `"/sensei-cycle`" --permission-mode acceptEdits --max-turns $MAX_TURNS"
Write-Host "  WorkDir:  $ORDER_SAMURAI_ROOT"
Write-Host ""
Write-Host "Verify with:  .\bin\register_sensei_task.ps1 -Status"
Write-Host "Remove with:  .\bin\register_sensei_task.ps1 -Remove"
