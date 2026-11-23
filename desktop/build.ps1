# 打包「作品集.exe」——给不想装 Docker / Python 的人用。
#
# 用法（仓库根执行）： powershell -ExecutionPolicy Bypass -File desktop\build.ps1
# 产物： desktop\dist\作品集.exe
#
# 为什么不用 .spec 文件：spec 里的相对路径依赖 SPECPATH，换个目录执行就会找错文件；
# 这里全部用绝对路径传参，从任何目录执行都对。

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$uv = "C:\Users\Jeno_Ra\.local\bin\uv.exe"
if (-not (Test-Path $uv)) { $uv = "uv" }

Push-Location $root
try {
  & $uv run pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "作品集" `
    --icon "$root\desktop\app.ico" `
    --paths "$root" `
    --add-data "$root\frontend;frontend" `
    --collect-data docx `
    --collect-data pptx `
    --collect-data pypdf `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.http.h11_impl `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    --hidden-import sqlalchemy.dialects.sqlite `
    --exclude-module onnxruntime `
    --exclude-module torch `
    --exclude-module transformers `
    --exclude-module matplotlib `
    --exclude-module pandas `
    --exclude-module pytest `
    --distpath "desktop\dist" `
    --workpath "desktop\build" `
    --specpath "desktop\build" `
    "desktop\portable.py"
} finally {
  Pop-Location
}

$exe = Join-Path $root "desktop\dist\作品集.exe"
if (Test-Path $exe) {
  $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
  Write-Host "打包完成：$exe（$mb MB）"
} else {
  throw "打包失败：没有生成 $exe"
}

