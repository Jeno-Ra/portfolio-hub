"""桌面入口：双击「作品集.exe」打开 Docker 正式版。

正式数据只保存在 Docker 的 PostgreSQL、Chroma 和媒体卷中；此入口不再启动
内置 SQLite/Chroma，也不会产生第二套作品库。Docker 未启动时，入口会调用仓库
中的 desktop/启动作品集.ps1 自动启动 Docker Desktop 和 compose。

开发时直接运行本模块仍可启动本地服务，便于测试；只有 PyInstaller 冻结版走
Docker 客户端模式。
"""

import logging
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

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


def backup_database() -> None:
    """每天备份 SQLite 数据库；不包含上传文件等其他文件，也不删除旧备份。"""
    temp_path = None
    try:
        from backend.app.config import settings

        db = Path(settings.db_path)
        if not db.is_file():
            return
        stamp = time.strftime("%Y%m%d")
        target = db.with_name(f"{db.stem}.db.bak-{stamp}")
        if target.exists():
            return

        # 临时文件与目标同目录：确保原子发布且不同进程不会共享临时文件。
        fd, name = tempfile.mkstemp(prefix=f".{db.name}.backup-", suffix=".tmp", dir=db.parent)
        temp_path = Path(name)
        os.close(fd)
        source = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            destination = sqlite3.connect(str(temp_path))
            try:
                source.backup(destination)
                result = destination.execute("PRAGMA integrity_check").fetchall()
                if result != [("ok",)]:
                    raise sqlite3.DatabaseError(f"backup integrity check failed: {result}")
            finally:
                destination.close()
        finally:
            source.close()

        # 硬链接以原子方式创建目标，并且绝不覆盖并发进程已发布的备份。
        os.link(temp_path, target)
    except Exception:
        # 备份不可用不应阻止应用启动；失败时不清理旧备份。
        logger.exception("数据库备份失败（仅数据库，不含其他文件）")
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("无法删除数据库备份临时文件")


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


def _run_docker(*args: str, timeout: float = 15) -> bool:
    """Run a Docker CLI command without treating diagnostic stderr as failure."""
    try:
        result = subprocess.run(
            ["docker", *args],
            cwd=r"F:\Projects\portfolio-hub",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def start_docker_formal() -> bool:
    """Start the formal Docker stack; frozen exe never imports the local backend."""
    if not _run_docker("version", "--format", "{{.Server.Version}}", timeout=5):
        docker = Path(r"C:\Users\Jeno_Ra\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe")
        if docker.is_file():
            subprocess.Popen([str(docker)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if _run_docker("version", "--format", "{{.Server.Version}}", timeout=5):
                break
            time.sleep(2)
        else:
            return False
    return _run_docker("compose", "up", "-d", timeout=120)


def open_formal_client() -> None:
    """Frozen entry point: start Docker, then open the shared formal service."""
    redirect_output()
    url = "http://127.0.0.1:8000/"
    if not alive(url):
        if not start_docker_formal():
            logger.error("无法启动 Docker 正式版")
            return
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not alive(url):
            time.sleep(2)
    if alive(url):
        open_window(url)
    else:
        logger.error("Docker 正式版启动超时")


def main() -> None:
    if getattr(sys, "frozen", False):
        # The distributed exe is a desktop client, not a second local database.
        open_formal_client()
        return

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

    backup_database()

    threading.Thread(target=open_window, args=(url,), daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
