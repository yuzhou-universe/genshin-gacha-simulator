"""路径解析。**刻意不依赖任何其它模块**。

单独拆出来是为了解决一个导入顺序陷阱：首次运行时必须先往数据目录写 `.env`，
再让 ``Settings`` 去读它。如果路径解析住在 ``config.py`` 里，那么第一次
``import`` 就会连带初始化 ``settings`` —— 那时 `.env` 还不存在，配置会被
永久冻结成默认值（包括那个不该用的开发占位密钥）。

所以启动器的顺序是：``paths`` → ``bootstrap``（写 .env）→ ``app.main``（读配置）。
本模块不 import 任何项目内模块，这个顺序才成立。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: 打包后的应用名，同时用作用户数据目录名
APP_DIR_NAME = "GenshinGacha"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的可执行文件里。"""
    return getattr(sys, "frozen", False)


def _resolve_resource_dir() -> Path:
    """只读资源目录：模板、静态文件、Alembic 脚本。

    打包后 PyInstaller 把 ``--add-data`` 的内容解包到 ``sys._MEIPASS``；
    onefile 下那是临时目录，onedir 下就是程序所在目录。
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    # 开发时：app/core/paths.py -> app/core -> app -> <项目根>
    return Path(__file__).resolve().parent.parent.parent


def _resolve_data_dir() -> Path:
    """可写数据目录：数据库、上传素材、运行时 .env。

    打包后必须在用户目录。程序可能装在 Program Files（只读），
    而且卸载时不该连用户的存档一起删掉。
    """
    override = os.environ.get("APP_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / f".{APP_DIR_NAME.lower()}"

    # 开发时仍用项目根，开发与测试行为完全不变
    return _resolve_resource_dir()


RESOURCE_DIR = _resolve_resource_dir()
DATA_DIR = _resolve_data_dir()

# 数据目录必须存在：数据库与上传都要往里写。这里早建一次，
# 免得等到数据库引擎连接时才报 "unable to open database file"。
DATA_DIR.mkdir(parents=True, exist_ok=True)
