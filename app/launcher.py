"""桌面启动器。

打包成 exe 后的入口：起本地服务、开浏览器、把访问地址与管理员密码显示出来。

**导入顺序是刻意的**：必须先 ``ensure_runtime_env()`` 在数据目录里写好 `.env`，
再 ``import app.main``。反过来的话 ``Settings`` 会先被初始化，读到的是不存在的
`.env`，配置就永久冻结成默认值了（包括那个不该出现在成品里的开发占位密钥）。
详见 ``app/core/paths.py``。
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# 只导入不依赖配置的模块，此时还没碰 Settings
from app.core.bootstrap import RuntimeCredentials, ensure_runtime_env
from app.core.paths import DATA_DIR

APP_TITLE = "原神风格抽卡模拟系统"
DEFAULT_PORT = 8000


def ensure_output_streams(data_dir) -> None:
    """把 std 流接到日志文件上。

    **打包成窗口程序（console=False）后，``sys.stdout`` 与 ``sys.stderr`` 都是
    ``None``。** 此时任何 ``print`` 或库的日志输出（uvicorn 的 logging、
    Python 的 warnings）都会抛 ``AttributeError: 'NoneType' object has no
    attribute 'write'``，把整个进程打崩 —— 而且窗口模式下看不到任何报错。

    所以启动最早期就把它们接到数据目录的日志文件，既避免崩溃，
    也让出问题时有一份可查的记录。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        stream = open(data_dir / "launcher.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def find_free_port(preferred: int = DEFAULT_PORT, attempts: int = 20) -> int:
    """找一个能用的端口。

    不直接用 8000：用户机器上它很可能被别的东西占着，
    那样程序会以一个看不懂的 socket 错误退出。
    """
    for offset in range(attempts):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{preferred} 起的 {attempts} 个端口都被占用了")


def detect_lan_ip() -> str:
    """本机在局域网里的地址，给手机 App 用。

    靠 UDP socket 的 connect 探测出口网卡 —— 不会真的发包。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def wait_until_ready(port: int, timeout: float = 40.0) -> bool:
    """轮询直到服务真的能响应。

    不能只 sleep 一个固定值：首次启动要跑数据库迁移与写入默认卡池，
    机器慢的时候会超过任何拍脑袋定的等待时间，结果浏览器打开是个错误页。
    """
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/login"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.4)
    return False


class ServerThread(threading.Thread):
    """在后台线程里跑 uvicorn。"""

    def __init__(self, app, host: str, port: int) -> None:
        super().__init__(daemon=True, name="uvicorn")
        self._app = app
        self._host = host
        self._port = port
        self.error: BaseException | None = None

    def run(self) -> None:
        import uvicorn

        try:
            config = uvicorn.Config(
                self._app,
                host=self._host,
                port=self._port,
                log_level="warning",
                access_log=False,
            )
            uvicorn.Server(config).run()
        except BaseException as exc:  # noqa: BLE001 - 跑到这里必须让用户看见
            self.error = exc


def run_console(creds: RuntimeCredentials, host: str, port: int) -> int:
    """没有图形界面时的退路（--console，或 tkinter 不可用）。

    出问题时这个模式更有用：日志直接打在终端里，而 GUI 只能弹一个框。
    """
    from app.main import app

    lan = detect_lan_ip()
    # 逐行 flush：输出被重定向到文件时 stdout 是块缓冲的，
    # 不刷的话用户看不到地址与密码，只会在退出时才一次性冒出来。
    banner = [
        "",
        f"  {APP_TITLE}",
        "-" * 46,
        f"  本机访问 : http://127.0.0.1:{port}",
        f"  手机访问 : http://{lan}:{port}",
        f"  管理员   : {creds.admin_username} / {creds.admin_password}",
        f"  数据目录 : {DATA_DIR}",
        "-" * 46,
        "  Ctrl+C 停止",
        "",
    ]
    print("\n".join(banner), flush=True)

    server = ServerThread(app, host, port)
    server.start()
    try:
        while server.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  已停止")
    if server.error:
        print(f"  启动失败: {server.error!r}")
        return 1
    return 0


def run_gui(creds: RuntimeCredentials, host: str, port: int) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    from app.main import app

    lan = detect_lan_ip()
    local_url = f"http://127.0.0.1:{port}"
    lan_url = f"http://{lan}:{port}"

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("560x440")
    root.minsize(520, 400)

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text=APP_TITLE, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
    status = ttk.Label(frame, text="正在启动…", foreground="#b8862c")
    status.pack(anchor="w", pady=(4, 14))

    def add_row(label: str, value: str, *, mono: bool = False, button: tuple | None = None):
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=10, foreground="#6d7b8c").pack(side="left")
        entry = ttk.Entry(row, font=("Consolas", 10) if mono else ("Microsoft YaHei UI", 10))
        entry.insert(0, value)
        entry.configure(state="readonly")
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        if button:
            text, cmd = button
            ttk.Button(row, text=text, width=8, command=cmd).pack(side="left")

    add_row("本机访问", local_url, button=("打开", lambda: webbrowser.open(local_url)))
    add_row("手机访问", lan_url)
    add_row("管理员", creds.admin_username)
    add_row("密码", creds.admin_password, mono=True)

    def copy_password():
        root.clipboard_clear()
        root.clipboard_append(creds.admin_password)
        status.configure(text="密码已复制到剪贴板", foreground="#2f9460")

    ttk.Button(frame, text="复制密码", command=copy_password).pack(anchor="w", pady=(2, 12))

    add_row("数据目录", str(DATA_DIR), button=("打开", lambda: open_in_explorer(DATA_DIR)))

    if creds.generated:
        tip = "这是首次运行自动生成的密码，请记下；可在数据目录的 .env 里修改。"
    else:
        tip = "如需重置密码，请编辑数据目录下 .env 中的 ADMIN_PASSWORD 后重启。"
    ttk.Label(frame, text=tip, foreground="#9fabb8", wraplength=500).pack(anchor="w", pady=(10, 0))

    ttk.Separator(frame).pack(fill="x", pady=14)

    def stop_and_exit():
        status.configure(text="正在停止…", foreground="#b8862c")
        root.after(120, root.destroy)

    def on_window_close():
        """关窗口 = 停服务。必须确认，否则用户会以为这只是个无关的提示框。

        之前没有这一步：用户点右上角的 ×，服务就悄悄停了，
        浏览器里的页面随即变成「127.0.0.1 拒绝连接」，
        而且完全看不出是自己关了那个小窗口造成的。
        """
        if messagebox.askokcancel(
            "确定要退出吗？",
            f"关闭这个窗口会同时停止服务，浏览器里的抽卡界面将无法访问。\n\n"
            f"下次使用需要重新打开「{APP_TITLE}」。\n\n确定退出吗？",
            parent=root,
        ):
            stop_and_exit()

    root.protocol("WM_DELETE_WINDOW", on_window_close)

    ttk.Button(frame, text="停止并退出", command=stop_and_exit).pack(anchor="e")
    ttk.Label(
        frame,
        text="提示：使用期间请不要关闭本窗口，否则网页会打不开。",
        foreground="#cf4a44",
    ).pack(anchor="e", pady=(6, 0))

    server = ServerThread(app, host, port)
    server.start()

    def poll_server_error():
        if server.error is not None:
            status.configure(text="启动失败", foreground="#cf4a44")
            messagebox.showerror(
                "启动失败",
                f"{server.error!r}\n\n数据目录：\n{DATA_DIR}\n\n"
                "可在命令行用 --console 参数启动以查看完整日志。",
            )
            return
        root.after(500, poll_server_error)

    def on_ready(ok: bool):
        if ok:
            status.configure(text=f"运行中 · 端口 {port}", foreground="#2f9460")
        else:
            status.configure(text="启动超时", foreground="#cf4a44")
            messagebox.showwarning(
                "启动超时",
                "服务在 40 秒内没有就绪。\n首次启动需要建库，稍等片刻后重试即可。",
            )

    threading.Thread(
        target=lambda: root.after(0, on_ready, wait_until_ready(port)),
        daemon=True,
    ).start()
    root.after(500, poll_server_error)

    # 等就绪再开浏览器：否则打开的是"无法连接"，用户会以为程序坏了
    def open_when_ready():
        if wait_until_ready(port, timeout=60):
            webbrowser.open(local_url)

    threading.Thread(target=open_when_ready, daemon=True).start()

    root.mainloop()
    return 0 if server.error is None else 1


def open_in_explorer(path: Path) -> None:
    """在文件管理器里打开目录（跨平台，虽然成品主要是 Windows）。"""
    if sys.platform.startswith("win"):
        import os

        os.startfile(path)  # noqa: S606 - 打开的是程序自己的数据目录
    else:
        webbrowser.open(path.as_uri())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0（手机可连）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--console", action="store_true", help="不开窗口，只用终端")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动开浏览器")
    args = parser.parse_args(argv)

    # 最先处理输出流：窗口模式下 stdout/stderr 是 None，
    # 后面任何一行日志都可能把进程打崩，且看不到任何提示。
    ensure_output_streams(DATA_DIR)

    # ★ 顺序不能动：先写 .env，再导入应用
    creds = ensure_runtime_env(DATA_DIR)

    try:
        port = find_free_port(args.port)
    except RuntimeError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    if args.console or not _tk_available():
        return run_console(creds, args.host, port)
    return run_gui(creds, args.host, port)


def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
