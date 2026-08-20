# installer.ps1 — VibeTrace 安装向导（GUI / 静默，零依赖，Windows PowerShell 5.1+）
<#
用法：
  powershell -ExecutionPolicy Bypass -File installer.ps1               # GUI 向导
  powershell -ExecutionPolicy Bypass -File installer.ps1 -Silent       # 静默安装（默认选项）
  powershell -ExecutionPolicy Bypass -File installer.ps1 -Silent `
      -InstallDir "D:\VibeTrace" -NoTasks -NoShortcuts -NoLaunch    # 自定义静默

安装内容：
  1) 复制 dist\VibeTrace.exe、卸载器到安装目录；config.json 缺失时从默认模板生成
     （数据目录默认跟随程序目录，可后续在 config.json 中改 data_root）
  2) 注册计划任务：VibeTrace（登录自启）/ VibeTraceReport（每日 19:30 日报）
  3) 创建开始菜单与桌面快捷方式
  4) 写入 HKCU「添加或删除程序」条目（无需管理员权限）
#>
[CmdletBinding()]
param(
  [switch]$Silent,
  [string]$InstallDir = "",
  [switch]$NoTasks,
  [switch]$NoShortcuts,
  [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$SrcExe = Join-Path $RepoRoot "dist\VibeTrace.exe"
$SrcUninstaller = Join-Path $RepoRoot "uninstaller.ps1"
$SrcConfigTemplate = Join-Path $RepoRoot "config.default.json"

function Get-ProgramVersion {
  try {
    $m = Select-String -Path (Join-Path $RepoRoot "version.py") -Pattern 'VERSION\s*=\s*"([^"]+)"' -ErrorAction Stop
    return $m.Matches[0].Groups[1].Value
  } catch { return "" }
}
$AppVersion = Get-ProgramVersion
$AppTitle = "VibeTrace 电脑使用情况监控" + $(if ($AppVersion) { " v$AppVersion" } else { "" })

function Assert-Sources {
  if (-not (Test-Path -LiteralPath $SrcExe)) {
    throw "未找到 $SrcExe`n请先在项目目录构建：python -m PyInstaller VibeTrace.spec --noconfirm"
  }
  if (-not (Test-Path -LiteralPath $SrcUninstaller)) {
    throw "未找到卸载器 $SrcUninstaller"
  }
}

function Resolve-InstallDir {
  $dir = $InstallDir
  if (-not $dir) { $dir = Join-Path $env:LOCALAPPDATA "VibeTrace" }
  $dir = [System.IO.Path]::GetFullPath($dir.TrimEnd('\'))
  $srcFull = [System.IO.Path]::GetFullPath($RepoRoot)
  if ($dir.TrimEnd('\') -eq $srcFull.TrimEnd('\')) {
    throw "安装目录不能是项目源码目录（$srcFull）。请选择其他目录（例如 $env:LOCALAPPDATA\VibeTrace）。"
  }
  return $dir
}

function Stop-RunningInstances {
  Get-Process -Name "VibeTrace" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

function Copy-ProgramFiles([string]$dir, [scriptblock]$log) {
  New-Item -ItemType Directory -Path $dir -Force | Out-Null
  Copy-Item -LiteralPath $SrcExe -Destination (Join-Path $dir "VibeTrace.exe") -Force
  if ($log) { & $log "已复制 VibeTrace.exe" }
  Copy-Item -LiteralPath $SrcUninstaller -Destination (Join-Path $dir "uninstaller.ps1") -Force
  $cfg = Join-Path $dir "config.json"
  if (-not (Test-Path -LiteralPath $cfg) -and (Test-Path -LiteralPath $SrcConfigTemplate)) {
    Copy-Item -LiteralPath $SrcConfigTemplate -Destination $cfg
    if ($log) { & $log "已生成 config.json（默认配置）" }
  }
}

function Register-Tasks([string]$dir, [scriptblock]$log) {
  $exe = Join-Path $dir "VibeTrace.exe"
  $action = New-ScheduledTaskAction -Execute $exe -Argument " " -WorkingDirectory $dir
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $settings = New-ScheduledTaskSettingsSet -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  Register-ScheduledTask -TaskName "VibeTrace" -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null
  if ($log) { & $log "已注册计划任务 VibeTrace（登录自启）" }

  $rAction = New-ScheduledTaskAction -Execute $exe -Argument "--today --write" -WorkingDirectory $dir
  $rTrigger = New-ScheduledTaskTrigger -Daily -At 19:30
  $rSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable
  Register-ScheduledTask -TaskName "VibeTraceReport" -Action $rAction -Trigger $rTrigger `
    -Settings $rSettings -Force | Out-Null
  if ($log) { & $log "已注册计划任务 VibeTraceReport（每日 19:30 日报）" }
}

function New-Shortcuts([string]$dir, [bool]$desktop, [scriptblock]$log) {
  $ws = New-Object -ComObject WScript.Shell
  $exe = Join-Path $dir "VibeTrace.exe"
  $menuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\VibeTrace"
  New-Item -ItemType Directory -Path $menuDir -Force | Out-Null
  $lnk1 = $ws.CreateShortcut((Join-Path $menuDir "VibeTrace.lnk"))
  $lnk1.TargetPath = $exe; $lnk1.WorkingDirectory = $dir; $lnk1.IconLocation = "$exe,0"
  $lnk1.Description = "启动 VibeTrace（托盘常驻）"
  $lnk1.Save()
  $lnk2 = $ws.CreateShortcut((Join-Path $menuDir "打开仪表盘.lnk"))
  $lnk2.TargetPath = $exe; $lnk2.Arguments = "--dashboard"
  $lnk2.WorkingDirectory = $dir; $lnk2.IconLocation = "$exe,0"
  $lnk2.Description = "打开本地网页仪表盘（127.0.0.1:8765）"
  $lnk2.Save()
  if ($desktop) {
    $desktopDir = [Environment]::GetFolderPath("Desktop")
    $lnk3 = $ws.CreateShortcut((Join-Path $desktopDir "VibeTrace.lnk"))
    $lnk3.TargetPath = $exe; $lnk3.WorkingDirectory = $dir; $lnk3.IconLocation = "$exe,0"
    $lnk3.Save()
  }
  $msg = if ($desktop) { "已创建快捷方式（开始菜单 + 桌面）" } else { "已创建快捷方式（开始菜单）" }
  if ($log) { & $log $msg }
}

function Write-UninstallKey([string]$dir) {
  $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VibeTrace"
  $uninst = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $dir 'uninstaller.ps1')`""
  New-Item -Path $key -Force | Out-Null
  New-ItemProperty -Path $key -Name "DisplayName" -Value $AppTitle -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name "DisplayVersion" -Value $AppVersion -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name "DisplayIcon" -Value "$(Join-Path $dir 'VibeTrace.exe'),0" -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name "InstallLocation" -Value $dir -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name "Publisher" -Value "VibeTrace" -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name "UninstallString" -Value $uninst -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name "QuietUninstallString" -Value "$uninst -Silent" -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $key -Name "NoModify" -Value 1 -PropertyType DWord -Force | Out-Null
  New-ItemProperty -Path $key -Name "NoRepair" -Value 1 -PropertyType DWord -Force | Out-Null
}

function Install-All([string]$dir, [bool]$desktop, [scriptblock]$log) {
  Assert-Sources
  if ($log) { & $log "安装目录：$dir" }
  Stop-RunningInstances
  Copy-ProgramFiles $dir $log
  if (-not $NoTasks)  { Register-Tasks $dir $log }  else { if ($log) { & $log "跳过计划任务注册" } }
  if (-not $NoShortcuts) { New-Shortcuts $dir $desktop $log } else { if ($log) { & $log "跳过快捷方式创建" } }
  Write-UninstallKey $dir
  if ($log) { & $log "已写入「添加或删除程序」条目" }
  return $true
}

# ============================================================
# GUI 向导
# ============================================================
function Show-GuiWizard {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  [System.Windows.Forms.Application]::EnableVisualStyles()

  $form = New-Object System.Windows.Forms.Form
  $form.Text = "安装 - $AppTitle"
  $form.ClientSize = New-Object System.Drawing.Size(560, 400)
  $form.FormBorderStyle = "FixedDialog"
  $form.MaximizeBox = $false
  $form.MinimizeBox = $false
  $form.StartPosition = "CenterScreen"

  # ---------- 步骤 1：欢迎 + 安装位置 ----------
  $panel1 = New-Object System.Windows.Forms.Panel
  $panel1.Dock = "Fill"
  $lbl1 = New-Object System.Windows.Forms.Label
  $lbl1.Text = "欢迎安装 $AppTitle`n`n本工具将在后台记录你的软件使用情况，数据仅保存在本地。`n安装将完成以下步骤：`n  · 复制程序文件`n  · 注册开机自启与每日日报计划任务`n  · 创建开始菜单快捷方式`n  · 登记到「添加或删除程序」"
  $lbl1.SetBounds(24, 20, 500, 150)
  $lblDir = New-Object System.Windows.Forms.Label
  $lblDir.Text = "安装位置："
  $lblDir.SetBounds(24, 200, 80, 24)
  $txtDir = New-Object System.Windows.Forms.TextBox
  $txtDir.Text = Resolve-InstallDir
  $txtDir.SetBounds(100, 198, 340, 24)
  $btnBrowse = New-Object System.Windows.Forms.Button
  $btnBrowse.Text = "浏览…"
  $btnBrowse.SetBounds(448, 197, 80, 26)
  $lblNote = New-Object System.Windows.Forms.Label
  $lblNote.Text = "数据（每日记录）默认保存在安装目录下，可随时在 config.json 中更改 data_root。"
  $lblNote.SetBounds(100, 230, 430, 30)
  $lblNote.ForeColor = [System.Drawing.Color]::Gray
  $lblNote.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 8.5)
  $panel1.Controls.AddRange(@($lbl1, $lblDir, $txtDir, $btnBrowse, $lblNote))

  # ---------- 步骤 2：选项 ----------
  $panel2 = New-Object System.Windows.Forms.Panel
  $panel2.Dock = "Fill"
  $lbl2 = New-Object System.Windows.Forms.Label
  $lbl2.Text = "安装选项"
  $lbl2.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Bold)
  $lbl2.SetBounds(24, 20, 200, 28)
  $chkTasks = New-Object System.Windows.Forms.CheckBox
  $chkTasks.Text = "开机自动启动（计划任务）+ 每日 19:30 自动生成日报"
  $chkTasks.Checked = $true
  $chkTasks.SetBounds(40, 70, 460, 26)
  $chkStartMenu = New-Object System.Windows.Forms.CheckBox
  $chkStartMenu.Text = "创建开始菜单快捷方式（含「打开仪表盘」）"
  $chkStartMenu.Checked = $true
  $chkStartMenu.SetBounds(40, 106, 460, 26)
  $chkDesktop = New-Object System.Windows.Forms.CheckBox
  $chkDesktop.Text = "同时在桌面创建快捷方式"
  $chkDesktop.SetBounds(40, 142, 460, 26)
  $chkLaunch = New-Object System.Windows.Forms.CheckBox
  $chkLaunch.Text = "安装完成后立即启动（托盘常驻）"
  $chkLaunch.Checked = $true
  $chkLaunch.SetBounds(40, 178, 460, 26)
  $lbl2Note = New-Object System.Windows.Forms.Label
  $lbl2Note.Text = "提示：如果本机已运行旧版本，安装器会自动停止旧进程再覆盖安装，数据保留。"
  $lbl2Note.SetBounds(40, 230, 470, 30)
  $lbl2Note.ForeColor = [System.Drawing.Color]::Gray
  $panel2.Controls.AddRange(@($lbl2, $chkTasks, $chkStartMenu, $chkDesktop, $chkLaunch, $lbl2Note))

  # ---------- 步骤 3：进度 / 结果 ----------
  $panel3 = New-Object System.Windows.Forms.Panel
  $panel3.Dock = "Fill"
  $lbl3 = New-Object System.Windows.Forms.Label
  $lbl3.Text = "正在安装…"
  $lbl3.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Bold)
  $lbl3.SetBounds(24, 20, 300, 28)
  $txtLog = New-Object System.Windows.Forms.TextBox
  $txtLog.Multiline = $true
  $txtLog.ReadOnly = $true
  $txtLog.ScrollBars = "Vertical"
  $txtLog.SetBounds(24, 60, 510, 220)
  $lblDone = New-Object System.Windows.Forms.Label
  $lblDone.Text = ""
  $lblDone.ForeColor = [System.Drawing.Color]::ForestGreen
  $lblDone.SetBounds(24, 292, 510, 26)
  $panel3.Controls.AddRange(@($lbl3, $txtLog, $lblDone))

  # ---------- 导航 ----------
  $btnBack = New-Object System.Windows.Forms.Button
  $btnBack.Text = "上一步"
  $btnBack.SetBounds(350, 356, 90, 28)
  $btnNext = New-Object System.Windows.Forms.Button
  $btnNext.Text = "下一步 >"
  $btnNext.SetBounds(446, 356, 90, 28)
  $form.Controls.AddRange(@($panel1, $panel2, $panel3, $btnBack, $btnNext))

  $step = 1
  function Show-Step([int]$n) {
    $panel1.Visible = ($n -eq 1)
    $panel2.Visible = ($n -eq 2)
    $panel3.Visible = ($n -eq 3)
    $btnBack.Enabled = ($n -eq 2)
    $script:step = $n
  }
  Show-Step 1

  $script:logLines = @()
  function Append-Log([string]$msg) {
    $script:logLines += $msg
    $txtLog.AppendText($msg + [Environment]::NewLine)
    $txtLog.SelectionStart = $txtLog.TextLength
    $txtLog.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
  }

  $btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "选择安装目录"
    if ($dlg.ShowDialog() -eq "OK") { $txtDir.Text = $dlg.SelectedPath }
  })

  $btnNext.Add_Click({
    if ($step -eq 1) {
      Show-Step 2
      return
    }
    if ($step -eq 2) {
      $btnNext.Enabled = $false; $btnBack.Enabled = $false
      $form.Text = "正在安装…"
      $script:installError = $null
      $script:installOk = $false
      try {
        $script:installDir = Resolve-InstallDir
        $script:installOk = Install-All $script:installDir ([bool]$chkDesktop.Checked) { param($m) Append-Log $m }
      } catch {
        $script:installError = $_.Exception.Message
      }
      if ($script:installOk) {
        $lblDone.Text = "✔ 安装完成！"
        $lblDone.ForeColor = [System.Drawing.Color]::ForestGreen
        $btnNext.Text = "完成"
        $btnNext.Enabled = $true
      } else {
        $lblDone.Text = "✘ 安装失败：" + $script:installError
        $lblDone.ForeColor = [System.Drawing.Color]::Firebrick
        $btnNext.Text = "关闭"
        $btnNext.Enabled = $true
      }
      Show-Step 3
      return
    }
    # 完成 / 关闭
    if ($script:installOk -and $chkLaunch.Checked -and -not $script:installError) {
      Start-Process (Join-Path $script:installDir "VibeTrace.exe")
    }
    $form.Close()
  })

  $form.AddShown({ $form.Activate() })
  [void]$form.ShowDialog()
  return @{ ok = $true }
}

# ============================================================
# 入口
# ============================================================
try {
  Assert-Sources
  if ($Silent) {
    $dir = Resolve-InstallDir
    $log = { param($m) Write-Host "  $m" }
    Write-Host "VibeTrace 静默安装"
    Install-All $dir $true $log
    Write-Host "安装完成：$dir"
    if (-not $NoLaunch) { Start-Process (Join-Path $dir "VibeTrace.exe") }
    exit 0
  }
  Show-GuiWizard | Out-Null
  exit 0
} catch {
  Write-Host "安装失败：$($_.Exception.Message)" -ForegroundColor Red
  if (-not $Silent) {
    [System.Windows.Forms.MessageBox]::Show("安装失败：$($_.Exception.Message)", "VibeTrace 安装", "OK", "Error") | Out-Null
  }
  exit 1
}
