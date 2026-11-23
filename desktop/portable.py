"""打包版入口：双击「作品集.exe」= 起一个本地服务 + 开一个应用窗口。

给外行用，所以这里把所有技术细节都藏起来了：
- 不需要 Docker、不需要 Python、不需要 git、不需要改 .env
- 数据在 %LOCALAPPDATA%\\作品集（见 backend/app/config.py）
- 密钥在界面里填（见 backend/app/user_config.py）
- 重复双击不会起第二个服务：先探测本机是否已经有一个在跑，有就直接开窗

开发时也可以直接 `uv run python -m desktop.portable` 体验同一条路径。
"""

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def redirect_output() -> None:
    """打包成无控制台窗口时 stdout/stderr 是空的，日志会写不出去。

    统一转到 运行日志.txt：出问题时让用户把这个文件发过来就能定位，
    比自己猜「双击没反应」强。
    """
    base = Path(os.environ.get("PORTFOLIO_DATA_DIR") or (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "作品集"))
    try:
        base.mkdir(parents=True, exist_ok=True)
        # 这个句柄要活到进程结束（重定向 sys.stdout），不能进 with
        log = open(base / "运行日志.txt", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        return
    sys.stdout = log
    sys.stderr = log


def alive(url: str, timeout: float = 0.4) -> bool:
    """本机这个端口上是不是已经在跑我们自己的服务。"""
    try:
        with urllib.request.urlopen(f"{url}health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def free_port(start: int) -> int:
    for port in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
            return port
    return start


def open_window(url: str) -> None:
    """用 Edge 的 --app 模式：没有地址栏、没有标签页，看起来就是个桌面程序。"""
    time.sleep(1.0)
    for raw in EDGE_PATHS:
        exe = Path(raw)
        if exe.is_file():
            subprocess.Popen(
                [str(exe), f"--app={url}", "--window-size=1320,880"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    webbrowser.open(url)  # 没装 Edge 就退回默认浏览器


def main() -> None:
    if getattr(sys, "frozen", False):
        redirect_output()

    port = int(os.environ.get("PORTFOLIO_PORT") or DEFAULT_PORT)
    url = f"http://{HOST}:{port}/"

    if alive(url):
        # 已经有一个在跑（用户又双击了一次）：只开窗，不起第二个服务。
        # 起第二个会同时打开同一个向量库目录，Chroma 会报锁冲突。
        open_window(url)
        return

    if free_port(port) != port:
        port = free_port(port)
        url = f"http://{HOST}:{port}/"

    import uvicorn

    from backend.app.main import app

    threading.Thread(target=open_window, args=(url,), daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
