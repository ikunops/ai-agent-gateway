# gateway.ps1 — AI Gateway 启停/状态管理
# 用法:
# .\scripts\gateway.ps1 start 启动网关(后台, 日志到 logs\gateway.log)
# .\scripts\gateway.ps1 stop 停止网关 (默认 5 秒确认倒计时; -Force 跳过)
# .\scripts\gateway.ps1 status 查看状态(健康检查 + 最近路由记录)
# .\scripts\gateway.ps1 restart 重启 (默认 5 秒确认倒计时; -Force 跳过)
# .\scripts\gateway.ps1 -Install 注册开机自启(登录时自动启动, 计划任务 AIGateway)
# .\scripts\gateway.ps1 -Uninstall 移除开机自启
#
# 保护约束: 停止/重启网关会中断正在经网关(8901)的会话。操作前必须确认
# opencode 模型已切换为直连 (opencode/*), 否则会断连。脚本默认等待 5 秒
# 供确认; 确认已切换直连后可加 -Force 立即执行。
param(
[Parameter(Position = 0)]
[ValidateSet("start", "stop", "status", "restart")]
[string]$Action = "status",
[switch]$Install,
[switch]$Uninstall,
[switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = "C:\Users\30849\AppData\Local\Programs\Python\Python311\python.exe"
$Port = 8901
$HealthUrl = "http://127.0.0.1:$Port/v1/health"
$PidFile = Join-Path $Root "logs\gateway.pid"
$LogFile = Join-Path $Root "logs\gateway.log"
$TaskName = "AIGateway"

function Get-RunningPid {
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) { return $conn[0].OwningProcess }
return $null
}

function Start-Gateway {
if (Get-RunningPid) {
Write-Host "[gateway] 已在运行 (PID $(Get-RunningPid)), 无需重复启动"
return
}
New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null
$p = Start-Process -FilePath $Py `
-ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port" `
-WorkingDirectory $Root -WindowStyle Hidden `
-RedirectStandardOutput $LogFile -RedirectStandardError "$LogFile.err" -PassThru
Start-Sleep -Seconds 3
if (Get-RunningPid) {
Write-Host "[gateway] 已启动 (PID $(Get-RunningPid)), 健康检查:"
try { $h = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5; Write-Host " status: $($h.status)" }
catch { Write-Host " health ERR: $($_.Exception.Message)" }
}
else {
Write-Host "[gateway] 启动失败, 见 $LogFile.err"
}
}

function Confirm-StopGuard {
    if ($Force) { return }
    Write-Host ""
    Write-Host "[保护] 停止网关会中断正在经 8901 的 opencode 会话!" -ForegroundColor Yellow
    Write-Host "  请先在 /models 将模型切换到直连 (opencode/deepseek-v4-flash 等), 再继续。" -ForegroundColor Yellow
    Write-Host "  5 秒后继续 (Ctrl+C 取消; 或加 -Force 跳过等待):" -ForegroundColor Yellow
    for ($i = 5; $i -ge 1; $i--) {
        Write-Host "  $i..." -NoNewline -ForegroundColor DarkYellow
        Start-Sleep -Seconds 1
    }
    Write-Host ""
}

function Stop-Gateway {
    $pid_ = Get-RunningPid
    if (-not $pid_) { Write-Host "[gateway] 未在运行"; return }
    if ($Action -in @("stop", "restart")) { Confirm-StopGuard }
    Stop-Process -Id $pid_ -Force
    Start-Sleep -Seconds 1
    Write-Host "[gateway] 已停止 (PID $pid_)"
}

function Show-Status {
$pid_ = Get-RunningPid
if (-not $pid_) {
Write-Host "[gateway] 未在运行"
return
}
Write-Host "[gateway] 运行中 (PID $pid_, 端口 $Port)"
try {
$h = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
Write-Host " health: $($h.status)"
$s = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/stats/routing" -TimeoutSec 5
Write-Host " 路由记录: $($s.records) 条; 来源: $(($s.by_source | ConvertTo-Json -Compress))"
}
catch {
Write-Host " health ERR: $($_.Exception.Message)"
}
}

# 开机自启注册/移除（照抄 go-cache-proxy 的计划任务模式）
function Register-AutoStart {
try {
$action = New-ScheduledTaskAction -Execute $Py `
-Argument "-m uvicorn app.main:app --host 127.0.0.1 --port $Port" `
-WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "[OK] 开机自启已注册 (任务: $TaskName, 登录时启动网关)"
}
catch {
Write-Host "[FAIL] 注册失败: $($_.Exception.Message)" -ForegroundColor Red
exit 1
}
}

function Unregister-AutoStart {
try {
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Write-Host "[OK] 开机自启已移除 (任务: $TaskName)"
}
catch {
Write-Host "[WARN] 移除失败: $($_.Exception.Message)"
}
}

if ($Install) { Register-AutoStart; exit 0 }
if ($Uninstall) { Unregister-AutoStart; exit 0 }

switch ($Action) {
"start" { Start-Gateway }
"stop" { Stop-Gateway }
"status" { Show-Status }
"restart" { Stop-Gateway; Start-Gateway }
}
