# install.ps1 - Register VibeTrace scheduled task (auto-start at logon)
# Preferred runner: dist\VibeTrace.exe (single-file exe, no Python needed).
# Fallback: pythonw.exe + monitor.py --tray.
# Run as the current user (admin not strictly required; elevation may be needed
# on some systems to register a scheduled task for another user).

$ErrorActionPreference = 'Stop'

try {
    $scriptDir = $PSScriptRoot

    # 1. Prefer the packaged exe (dist\VibeTrace.exe); fall back to pythonw + script
    $exe = Join-Path $scriptDir 'dist\VibeTrace.exe'
    $runner = $null
    $runnerArgs = @()
    if (Test-Path -LiteralPath $exe) {
        $runner = $exe
        $runnerArgs = @()
        $runnerDesc = "exe: $exe"
    } else {
        # 1a. Locate pythonw.exe (priority: py launcher -> PATH -> common dirs)
        $pyw = $null
        $pyCmd = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pyCmd) {
            try {
                $pyPath = & $pyCmd.Source -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2>$null
                if ($pyPath -and (Test-Path -LiteralPath $pyPath)) { $pyw = $pyPath.Trim() }
            } catch { }
        }
        if (-not $pyw) {
            $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
            if ($cmd) { $pyw = $cmd.Source }
        }
        if (-not $pyw) {
            $candidates = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter pythonw.exe -Recurse -ErrorAction SilentlyContinue
            if ($candidates) { $pyw = $candidates | Select-Object -First 1 -ExpandProperty FullName }
        }
        if (-not $pyw) {
            $candidates = Get-ChildItem "C:\Python*" -Filter pythonw.exe -Recurse -ErrorAction SilentlyContinue
            if ($candidates) { $pyw = $candidates | Select-Object -First 1 -ExpandProperty FullName }
        }
        if (-not $pyw) {
            Write-Host "ERROR: pythonw.exe not found. Install Python 3.10+ or build the exe first." -ForegroundColor Red
            exit 1
        }
        $script = Join-Path $scriptDir 'monitor.py'
        if (-not (Test-Path -LiteralPath $script)) {
            Write-Host "ERROR: monitor.py not found in $scriptDir" -ForegroundColor Red
            exit 1
        }
        $runner = $pyw
        $runnerArgs = @('-W', 'ignore', $script)
        $runnerDesc = "pythonw: $pyw`n  script:  $script"
    }

    # 2. Logon task: run the monitor with tray (exe defaults to tray; script path passes --tray)
    $monitorArgs = $runnerArgs
    if ($runnerArgs.Count -eq 0) {
        # exe：无参数默认托盘（--tray 显式传入亦可）；
        # New-ScheduledTaskAction 不接受空字符串 Argument，用一个空格占位
        $monitorArgs = @(' ')
    } else {
        $monitorArgs += '--tray'
    }
    $action = New-ScheduledTaskAction -Execute $runner -Argument ($monitorArgs -join ' ') -WorkingDirectory $scriptDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    Register-ScheduledTask -TaskName 'VibeTrace' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

    # 3. Daily 19:30 report task (regenerate today's report.md/csv incl. browser history)
    if ($runnerArgs.Count -eq 0) {
        # exe：内置 report 分派（--today --write）
        $reportArgs = @('--today', '--write')
        $reportRunner = $runner
        $reportDesc = "exe report 分派: $exe --today --write"
    } else {
        $reportArgs = @('-W', 'ignore', (Join-Path $scriptDir 'report.py'), '--today', '--write')
        $reportRunner = $runner
        $reportDesc = "pythonw report.py --today --write"
    }
    $reportAction = New-ScheduledTaskAction -Execute $reportRunner `
        -Argument ($reportArgs -join ' ') `
        -WorkingDirectory $scriptDir
    $reportTrigger = New-ScheduledTaskTrigger -Daily -At 19:30
    $reportSettings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -StartWhenAvailable

    Register-ScheduledTask -TaskName 'VibeTraceReport' -Action $reportAction -Trigger $reportTrigger -Settings $reportSettings -Force | Out-Null

    Write-Host "Installed: scheduled task 'VibeTrace' will start the monitor at logon."
    Write-Host "Installed: scheduled task 'VibeTraceReport' will regenerate today's report at 19:30 daily."
    Write-Host "  runner:  $runnerDesc"
    Write-Host "  report:  $reportDesc"
    Write-Host "To uninstall, run:  powershell -ExecutionPolicy Bypass -File uninstall.ps1"
}
catch {
    Write-Host "Install failed: $_" -ForegroundColor Red
    Write-Host "Tip: try running this script from an elevated (Administrator) PowerShell." -ForegroundColor Yellow
    exit 1
}
