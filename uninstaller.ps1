# uninstaller.ps1 — UsageMonitor 卸载（GUI 确认 / 静默，零依赖）
<#
用法：
  powershell -ExecutionPolicy Bypass -File uninstaller.ps1            # GUI 确认
  powershell -ExecutionPolicy Bypass -File uninstaller.ps1 -Silent    # 静默卸载（保留数据与配置）
  powershell -ExecutionPolicy Bypass -File uninstaller.ps1 -Silent -DeleteData  # 连数据一起删除
  powershell ... -InstallDir "D:\UsageMonitor"                        # 指定安装目录（默认脚本所在目录）

卸载内容：停止运行中的实例、删除计划任务、删除快捷方式、删除「添加或删除程序」条目、
删除程序文件；是否删除记录数据与 config.json 由 -DeleteData / GUI 勾选决定。
#>
[CmdletBinding()]
param(
  [switch]$Silent,
  [switch]$DeleteData,
  [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"
$installDir = if ($InstallDir) { [System.IO.Path]::GetFullPath($InstallDir.TrimEnd('\')) } else { $PSScriptRoot }

function Stop-RunningInstances {
  Get-Process -Name "UsageMonitor" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

function Remove-Tasks {
  foreach ($name in @("UsageMonitor", "UsageMonitorReport")) {
    try {
      Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
      Write-Host "  已删除计划任务 $name"
    } catch {
      Write-Host "  计划任务 $name 不存在或删除失败：$($_.Exception.Message)"
    }
  }
}

function Remove-Shortcuts {
  $menuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\UsageMonitor"
  if (Test-Path -LiteralPath $menuDir) {
    Remove-Item -LiteralPath $menuDir -Recurse -Force
    Write-Host "  已删除开始菜单快捷方式"
  }
  $desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "UsageMonitor.lnk"
  if (Test-Path -LiteralPath $desktopLnk) {
    Remove-Item -LiteralPath $desktopLnk -Force
    Write-Host "  已删除桌面快捷方式"
  }
}

function Remove-RegKey {
  $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\UsageMonitor"
  if (Test-Path $key) {
    Remove-Item -Path $key -Recurse -Force
    Write-Host "  已删除「添加或删除程序」条目"
  }
}

function Invoke-DelayedDelete([string]$path) {
  # 延迟删除（ping 自延后 ~2 秒），让脚本自身先退出，避免删除正在执行的脚本文件
  Start-Process cmd.exe -ArgumentList "/c ping -n 3 127.0.0.1 >nul & rmdir /s /q `"$path`"" -WindowStyle Hidden
}

function Uninstall-All([bool]$wipeData) {
  if (-not (Test-Path -LiteralPath $installDir)) {
    throw "安装目录不存在：$installDir"
  }
  Write-Host "卸载目录：$installDir"
  Stop-RunningInstances
  Remove-Tasks
  Remove-Shortcuts
  Remove-RegKey

  if ($wipeData) {
    Write-Host "  删除程序文件与全部记录数据…"
    Invoke-DelayedDelete $installDir
    Write-Host "卸载完成（数据已一并删除）。"
  } else {
    foreach ($f in @("UsageMonitor.exe")) {
      $p = Join-Path $installDir $f
      if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Force
        Write-Host "  已删除 $f"
      }
    }
    $self = Join-Path $installDir "uninstaller.ps1"
    if (Test-Path -LiteralPath $self) {
      Start-Process cmd.exe -ArgumentList "/c ping -n 3 127.0.0.1 >nul & del /f /q `"$self`"" -WindowStyle Hidden
    }
    Write-Host "卸载完成。"
    Write-Host "已保留：config.json 与每日记录数据（如需删除，请重新运行：uninstaller.ps1 -DeleteData）。"
  }
}

function Show-GuiConfirm {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  [System.Windows.Forms.Application]::EnableVisualStyles()

  $form = New-Object System.Windows.Forms.Form
  $form.Text = "卸载 UsageMonitor"
  $form.ClientSize = New-Object System.Drawing.Size(460, 190)
  $form.FormBorderStyle = "FixedDialog"
  $form.MaximizeBox = $false
  $form.MinimizeBox = $false
  $form.StartPosition = "CenterScreen"

  $lbl = New-Object System.Windows.Forms.Label
  $lbl.Text = "确定要卸载 UsageMonitor 吗？`n`n安装目录：$installDir`n将停止运行中的程序，并删除计划任务、快捷方式与程序文件。"
  $lbl.SetBounds(24, 20, 410, 70)
  $chk = New-Object System.Windows.Forms.CheckBox
  $chk.Text = "同时删除全部记录数据与 config.json（不可恢复）"
  $chk.SetBounds(24, 96, 410, 26)
  $btnCancel = New-Object System.Windows.Forms.Button
  $btnCancel.Text = "取消"
  $btnCancel.SetBounds(250, 140, 85, 28)
  $btnOk = New-Object System.Windows.Forms.Button
  $btnOk.Text = "卸载"
  $btnOk.SetBounds(345, 140, 85, 28)
  $form.Controls.AddRange(@($lbl, $chk, $btnCancel, $btnOk))
  $form.AcceptButton = $btnOk
  $form.CancelButton = $btnCancel

  $script:confirmDelete = $false
  $btnOk.Add_Click({ $script:confirmDelete = $chk.Checked; $form.DialogResult = "OK"; $form.Close() })
  $btnCancel.Add_Click({ $form.DialogResult = "Cancel"; $form.Close() })

  $result = $form.ShowDialog()
  if ($result -ne "OK") { return $false }
  Uninstall-All -wipeData $script:confirmDelete
  [System.Windows.Forms.MessageBox]::Show(
    "卸载完成。" + $(if ($script:confirmDelete) { "" } else { "`n记录数据与 config.json 已保留。" }),
    "UsageMonitor 卸载", "OK", "Information") | Out-Null
  return $true
}

try {
  if ($Silent) {
    Uninstall-All -wipeData ([bool]$DeleteData)
    exit 0
  }
  $done = Show-GuiConfirm
  exit $(if ($done) { 0 } else { 1 })
} catch {
  Write-Host "卸载失败：$($_.Exception.Message)" -ForegroundColor Red
  if (-not $Silent) {
    [System.Windows.Forms.MessageBox]::Show("卸载失败：$($_.Exception.Message)", "UsageMonitor 卸载", "OK", "Error") | Out-Null
  }
  exit 1
}
