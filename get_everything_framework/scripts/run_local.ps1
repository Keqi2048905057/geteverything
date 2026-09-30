# 本机启动脚本（M1 起，M3 起同时拉起 worker）
#
# 用途：在 Windows 开发机上一键启动本机联调版：Web 进程 + 异步任务 worker。
# 特性：
#   - 默认只绑定 127.0.0.1，不对外暴露；
#   - 关闭 Flask debug（由 app.py 使用 waitress 提供生产级 WSGI）；
#   - 未配置 LOCAL_ADMIN_TOKEN 时，启动后控制台会打印本次进程的临时 Token；
#   - Web 与 worker 是两个独立进程，互不阻塞；worker 崩溃不影响 Web；
#   - 不修改任何 .env 内容，不触碰 scripts/OneForAll.exe。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 -SkipInstall
#   powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 -Port 5001
#   powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 -NoWorker
#
# 退出：在本窗口按 Ctrl+C。脚本会依次通知 worker 与 Web 进程退出；
# worker 收到 SIGTERM/中断后会把正在跑的任务标成 interrupted（不会静默消失）。

[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$NoWorker,
    [string]$Host = "127.0.0.1",
    [int]$Port = 5000,
    [double]$StepDelay = 0.0
)

$ErrorActionPreference = "Stop"

# 项目根 = 本脚本所在目录的上一级
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "== Get Everything Framework · 本机联调版 ==" -ForegroundColor Cyan
Write-Host "项目根: $ProjectRoot"

if (-not $SkipInstall) {
    Write-Host "`n[1/3] 安装依赖 ..." -ForegroundColor Yellow
    python -m pip install --disable-pip-version-check -r requirement.txt -r requirement-dev.txt
}

# 同一台机器上的两个进程必须用同一份环境（同一个 .env → 同一个数据库路径）。
$env:WEB_HOST = $Host
$env:WEB_PORT = "$Port"
$env:WEB_DEBUG = "false"

$worker = $null

try {
    if (-not $NoWorker) {
        Write-Host "`n[2/3] 启动 worker (python -m jobs.worker) ..." -ForegroundColor Yellow
        $workerArgs = @("-m", "jobs.worker")
        if ($StepDelay -gt 0) {
            $workerArgs += @("--step-delay", "$StepDelay")
        }
        $worker = Start-Process -FilePath "python" -ArgumentList $workerArgs `
            -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
        Write-Host "      worker PID = $($worker.Id)"
    } else {
        Write-Host "`n[2/3] 跳过 worker（-NoWorker）：任务会一直停在 queued。" -ForegroundColor Yellow
    }

    Write-Host "`n[3/3] 启动 Web 服务 (http://${Host}:${Port}/) ..." -ForegroundColor Yellow
    Write-Host "提示: 未配置 .env 的 LOCAL_ADMIN_TOKEN 时，下方会打印本次进程的临时 Token。"
    Write-Host "提示: 另开一个终端执行 python -m jobs.worker 也可以单独跑 worker。`n"
    python app.py
}
finally {
    if ($worker -and -not $worker.HasExited) {
        Write-Host "`n正在停止 worker (PID $($worker.Id)) ..." -ForegroundColor Yellow
        Stop-Process -Id $worker.Id -Force -ErrorAction SilentlyContinue
        $worker.WaitForExit(5000) | Out-Null
    }
    Write-Host "已退出。" -ForegroundColor Cyan
}
