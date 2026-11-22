# 作品集 —— 桌面客户端启动器
#
# 双击桌面「作品集」图标时执行，做三件事：
#   1. 检查 http://127.0.0.1:8000/health
#   2. 没起来就拉起 Docker Desktop + docker compose up -d，并等它就绪
#   3. 用 Edge 的 --app 模式打开，没有地址栏、没有标签页，看起来就是个独立客户端
#
# 为什么不用 Electron：只是把同一个页面装进独立窗口，Electron 要多 150 MB 运行时；
# Edge/Chrome 的 --app 模式已经是同一个浏览器内核的无边框窗口，零额外体积。
#
# 参数：-Hidden 由快捷方式传入，等待期间不显示控制台（保留错误可查）

param(
    [switch]$Hidden
)

$ErrorActionPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$repo = "F:\Projects\portfolio-hub"
$healthUrl = "http://127.0.0.1:8000/health"
$appUrl = "http://127.0.0.1:8000/"
$dockerDesktop = "C:\Users\Jeno_Ra\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe"
$edgePaths = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

function Test-Backend {
    try {
        $r = Invoke-WebRequest $healthUrl -TimeoutSec 2 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function New-Splash([string]$text) {
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "作品集"
    $form.Size = New-Object System.Drawing.Size(360, 150)
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.TopMost = $true

    $label = New-Object System.Windows.Forms.Label
    $label.Text = $text
    $label.AutoSize = $false
    $label.TextAlign = "MiddleCenter"
    $label.Dock = "Fill"
    $label.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 11)
    $form.Controls.Add($label)

    $form.Show()
    $form.Refresh()
    return @{ Form = $form; Label = $label }
}

function Set-SplashText($splash, [string]$text) {
    $splash.Label.Text = $text
    $splash.Form.Refresh()
    [System.Windows.Forms.Application]::DoEvents()
}

# ---------- 1. 后端已经在跑，直接开窗口 ----------
if (Test-Backend) {
    Start-Process $edge "--app=$appUrl --window-size=1320,880"
    exit 0
}

# ---------- 2. 没跑：先启动，期间显示等待窗口 ----------
$splash = New-Splash "正在启动作品集服务…"
Set-SplashText $splash "正在启动 Docker…"

if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
    if (Test-Path $dockerDesktop) {
        Start-Process $dockerDesktop
    } else {
        Set-SplashText $splash "找不到 Docker Desktop，请手动启动后重试"
        Start-Sleep -Seconds 6
        $splash.Form.Close()
        exit 1
    }
}

# 等 Docker 引擎就绪（最多 90 秒）
$engineReady = $false
for ($i = 0; $i -lt 45; $i++) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $engineReady = $true; break }
    Set-SplashText $splash "正在启动 Docker…（$([int]($i * 2)) 秒）"
    Start-Sleep -Seconds 2
}

if (-not $engineReady) {
    Set-SplashText $splash "Docker 启动超时，请手动打开 Docker Desktop"
    Start-Sleep -Seconds 8
    $splash.Form.Close()
    exit 1
}

Set-SplashText $splash "正在启动服务容器…"
Set-Location $repo
docker compose up -d *> $null

# 等后端健康检查通过（最多 120 秒）
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Backend) { $ok = $true; break }
    Set-SplashText $splash "正在等待服务就绪…（$([int]($i * 2)) 秒）"
    Start-Sleep -Seconds 2
}

$splash.Form.Close()

if (-not $ok) {
    [System.Windows.Forms.MessageBox]::Show(
        "服务没有起来。`n请在 ${repo} 下执行 docker compose logs --tail 50 backend 查看原因。",
        "作品集",
        "OK",
        "Warning"
    ) | Out-Null
    exit 1
}

# ---------- 3. 开独立窗口 ----------
Start-Process $edge "--app=$appUrl --window-size=1320,880"
