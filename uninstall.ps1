# uninstall.ps1 - Remove VibeTrace scheduled task, optionally delete recorded data
$ErrorActionPreference = 'Continue'

# Data root = this script's directory (D:\电脑使用情况监控)
$dataRoot = $PSScriptRoot

try {
    Unregister-ScheduledTask -TaskName 'VibeTrace' -Confirm:$false
    Write-Host "Scheduled task 'VibeTrace' removed."
}
catch {
    Write-Host "No task found or removal failed: $_"
}

try {
    Unregister-ScheduledTask -TaskName 'VibeTraceReport' -Confirm:$false
    Write-Host "Scheduled task 'VibeTraceReport' removed."
}
catch {
    Write-Host "No task found or removal failed: $_"
}

$del = Read-Host "Delete all recorded data (date folders) under the data root? [y/N]"
if ($del -eq 'y' -or $del -eq 'Y') {
    try {
        Get-ChildItem -LiteralPath $dataRoot -Directory |
            Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
            Remove-Item -Recurse -Force
        Write-Host "Data folders deleted."
    }
    catch {
        Write-Host "Data cleanup failed: $_"
    }
}

Write-Host "Done. Scripts and config files are kept."
