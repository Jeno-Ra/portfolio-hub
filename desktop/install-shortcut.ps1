# 在桌面和开始菜单创建「作品集」快捷方式
#
# 用法（在仓库根目录执行一次即可，换电脑/重装系统后也要重跑）：
#   powershell -ExecutionPolicy Bypass -File desktop\install-shortcut.ps1
#
# 为什么用脚本而不是手动右键新建：
#   快捷方式里要写死 powershell 的完整参数（隐藏窗口、绕过执行策略、传脚本路径），
#   手填容易漏引号；脚本用 $PSScriptRoot 自动定位仓库，换个目录克隆也能用。

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "启动作品集.ps1"
$icon = Join-Path $PSScriptRoot "app.ico"

if (-not (Test-Path $launcher)) { throw "找不到启动脚本: $launcher" }
if (-not (Test-Path $icon)) { throw "找不到图标: $icon（可运行 python desktop/make_icon.py 生成）" }

$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "作品集.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "作品集.lnk")
)

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in $targets) {
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    $sc.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcher + '"'
    $sc.WorkingDirectory = $repo
    $sc.IconLocation = "$icon,0"
    $sc.Description = "作品集 —— 本地多模态作品库"
    $sc.WindowStyle = 7
    $sc.Save()
    Write-Host "已创建: $lnk"
}

Write-Host "完成。双击桌面「作品集」即可打开（会自动拉起 Docker 和服务）。"
