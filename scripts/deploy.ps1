<#
.SYNOPSIS
    作品集自托管一键部署：检查环境 -> 启动容器 -> 等到真的能访问 -> 打印地址。

.DESCRIPTION
    把"照着 README 一步步敲"压成一条命令，并且每一步都先检查再动手：

      1. 检查 Docker CLI 与引擎（没起就尝试启动 Docker Desktop，最多等 90 秒）
      2. 检查 .env（缺了就复制模板并停下来，让你填密钥，而不是让容器起来后 502）
      3. 检查三个必填密钥是否为空（空着跑起来也是废的，不如现在报错）
      4. docker compose up -d [--build]
      5. 轮询 /health 直到真的返回 200（容器"启动了"不等于"能用了"）
      6. 打印本机 / Tailscale / 局域网三种地址

.PARAMETER SkipBuild
    不重新构建镜像，直接用现有镜像启动。改了代码但只想快速起服务时用。

.PARAMETER NoStart
    只做第 1~3 步的环境检查，不启动容器。

.PARAMETER TimeoutSeconds
    等待后端健康的上限秒数，默认 120。

.EXAMPLE
    .\scripts\deploy.ps1

.EXAMPLE
    .\scripts\deploy.ps1 -SkipBuild
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$NoStart,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    [OK] $Text" -ForegroundColor Green
}

function Write-Warn2([string]$Text) {
    Write-Host "    [!] $Text" -ForegroundColor Yellow
}

function Write-Fail([string]$Text) {
    Write-Host "    [X] $Text" -ForegroundColor Red
}

# 只认真正的局域网私有网段：10/8、172.16/12、192.168/16。
# Tailscale 的 100.64/10 也是私有地址，但它不是"同一个 WiFi"的意思，必须排除。
function Test-LanAddress([string]$Ip) {
    try { $b = ([System.Net.IPAddress]::Parse($Ip)).GetAddressBytes() } catch { return $false }
    if ($b[0] -eq 10) { return $true }
    if ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) { return $true }
    if ($b[0] -eq 192 -and $b[1] -eq 168) { return $true }
    return $false
}

# Windows PowerShell 5.1 会把原生命令写进 stderr 的内容当成错误，
# 配合 $ErrorActionPreference = "Stop" 会直接终止脚本——
# 所以探测类命令（docker info / tailscale ip）必须临时降级，只看退出码。
function Test-DockerEngine {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        docker info --format "{{.ServerVersion}}" | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Get-NativeOutput {
    param([string[]]$Command)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try { return (& $Command[0] $Command[1..($Command.Length - 1)]) } finally { $ErrorActionPreference = $prev }
}

function Write-RecentLogs {
    Write-Host "    ---- backend 最近 50 行 ----" -ForegroundColor DarkGray
    Get-NativeOutput @("docker", "compose", "logs", "--tail", "50", "backend") |
        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Write-Host "    ----------------------------" -ForegroundColor DarkGray
}

# ---------- 0. 定位仓库根 ----------
$repo = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $repo "docker-compose.yml"))) {
    Write-Fail "在 $repo 下找不到 docker-compose.yml，脚本必须放在仓库的 scripts\ 目录里"
    exit 1
}
Set-Location $repo
Write-Host "仓库：$repo" -ForegroundColor DarkGray

# ---------- 1. Docker ----------
Write-Step "检查 Docker"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Fail "没找到 docker 命令。请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/"
    exit 1
}
Write-Ok ("docker {0}" -f (docker --version))

$engineUp = Test-DockerEngine
if ($engineUp) {
    Write-Ok "Docker 引擎已在运行"
} else {
    Write-Warn2 "Docker 引擎没在运行，尝试启动 Docker Desktop"
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    $exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $exe) {
        Write-Fail "找不到 Docker Desktop.exe，请手动启动 Docker 后重试"
        exit 1
    }
    Start-Process $exe | Out-Null
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        if (Test-DockerEngine) { $engineUp = $true; break }
        Write-Host "." -NoNewline
    }
    Write-Host ""
    if (-not $engineUp) {
        Write-Fail "等了 90 秒引擎还没起来，请打开 Docker Desktop 看看它报了什么"
        exit 1
    }
    Write-Ok "Docker 引擎已就绪"
}

# ---------- 2. .env ----------
Write-Step "检查 .env"

$envFile = Join-Path $repo ".env"
$envExample = Join-Path $repo ".env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Warn2 "没有 .env，已从 .env.example 复制一份"
        Write-Warn2 "请打开 $envFile 填三个密钥后再跑一次：DEEPSEEK_API_KEY / EMBEDDING_API_KEY / GPT_API_KEY"
    } else {
        Write-Fail "既没有 .env 也没有 .env.example，仓库不完整"
    }
    exit 1
}
Write-Ok ".env 存在"

# ---------- 3. 必填密钥 ----------
Write-Step "检查必填配置"

$required = @("DEEPSEEK_API_KEY", "EMBEDDING_API_KEY", "GPT_API_KEY")
$values = @{}
foreach ($line in Get-Content $envFile -Encoding UTF8) {
    if ($line -match "^\s*#" -or $line -notmatch "=") { continue }
    $k, $v = $line.Split("=", 2)
    $values[$k.Trim()] = $v.Trim()
}

$missing = @()
foreach ($key in $required) {
    if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($values[$key])) {
        $missing += $key
    }
}
if ($missing.Count -gt 0) {
    Write-Fail ("这些密钥是空的：{0}" -f ($missing -join ", "))
    Write-Warn2 "填好 $envFile 里的对应行再跑一次（对话 / 向量化 / 图片描述各需要一个）"
    exit 1
}
foreach ($key in $required) { Write-Ok "$key 已填（长度 $($values[$key].Length)）" }

if ($NoStart) {
    Write-Step "只检查不启动（-NoStart）"
    Write-Ok "环境没问题，去掉 -NoStart 就会启动容器"
    exit 0
}

# ---------- 4. 启动 ----------
Write-Step "启动容器"
$composeArgs = @("compose", "up", "-d")
if (-not $SkipBuild) { $composeArgs += "--build" }
Write-Host ("    docker " + ($composeArgs -join " ")) -ForegroundColor DarkGray

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail "docker compose up 失败，最近日志："
    Write-RecentLogs
    exit 1
}
Write-Ok "容器已启动"

# ---------- 5. 等后端真的能用 ----------
Write-Step "等待后端就绪（最多 $TimeoutSeconds 秒）"

$healthy = $false
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    try {
        $resp = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 3
        # 接口统一回 {code, message, data}，所以真正的内容在 $resp.data 里
        if ($resp.data.status -eq "ok") { $healthy = $true; break }
    } catch {
        # 还没起来，继续等
    }
    Write-Host "." -NoNewline
    Start-Sleep -Seconds 2
}
Write-Host ""

if (-not $healthy) {
    Write-Fail "等了 $TimeoutSeconds 秒后端还没就绪，最近日志："
    Write-RecentLogs
    Write-Warn2 ("也可以手动看：docker compose logs -f backend")
    exit 1
}
Write-Ok ("后端就绪，用时 {0:N1} 秒" -f $sw.Elapsed.TotalSeconds)

# ---------- 6. 汇总 ----------
Write-Step "访问地址"

Write-Host "    本机        http://127.0.0.1:8000" -ForegroundColor White

$tailscaleExe = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $tailscaleExe) {
    $tsIp = (Get-NativeOutput @($tailscaleExe, "ip", "-4") | Select-Object -First 1)
    if ($tsIp) { Write-Host "    Tailscale   http://${tsIp}:8000   （手机任何网络都能用）" -ForegroundColor White }
} else {
    Write-Warn2 "没装 Tailscale，只有本机 / 局域网可用"
}

# 局域网地址：优先取"默认路由所在的那块网卡"上的 IPv4。
# 直接扫所有私有地址会先撞上 WSL/Hyper-V 的 vEthernet(172.29.x.x)，
# 那个地址手机连不上，写出来只会误导人。
$lanIp = $null
$defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
    Sort-Object RouteMetric | Select-Object -First 1
if ($defaultRoute) {
    $candidate = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $defaultRoute.InterfaceIndex -ErrorAction SilentlyContinue |
        Select-Object -First 1).IPAddress
    if ($candidate -and (Test-LanAddress $candidate)) { $lanIp = $candidate }
}
if (-not $lanIp) {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { (Test-LanAddress $_.IPAddress) -and $_.InterfaceAlias -notmatch "vEthernet|Loopback|WSL|Docker" } |
        Select-Object -First 1).IPAddress
}
if ($lanIp) { Write-Host "    局域网      http://${lanIp}:8000   （同一 WiFi 下）" -ForegroundColor White }

Write-Host ""
docker compose ps

Write-Step "接下来"
Write-Host "    桌面客户端：双击桌面上的「作品集」，或跑 desktop\启动作品集.ps1" -ForegroundColor DarkGray
Write-Host "    改完代码重新部署：再跑一次本脚本（会自动 --build）" -ForegroundColor DarkGray
Write-Host "    看日志：docker compose logs -f backend" -ForegroundColor DarkGray
Write-Host "    停服务：docker compose stop（数据不动）" -ForegroundColor DarkGray
Write-Host "    停并删容器：docker compose down（数据仍在卷里，加 -v 才会删数据）" -ForegroundColor DarkGray
