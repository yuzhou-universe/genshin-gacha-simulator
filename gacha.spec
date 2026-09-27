# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格。

用 onedir 而不是 onefile：onefile 每次启动都要把全部内容解压到临时目录，
启动慢，而且出问题时看不到内部结构、难以排查。onedir 的代价只是一个目录。

构建：.venv/Scripts/python.exe -m PyInstaller gacha.spec --noconfirm
"""

from pathlib import Path

# .spec 是被 exec 执行的，没有 __file__，分析期用 SPECPATH 拿规格文件所在目录
ROOT = Path(SPECPATH)

# ---------------------------------------------------------------- 数据文件
# 这些是**只读资源**，运行时从 sys._MEIPASS 读取（见 app/core/paths.py）。
# 数据库与上传素材不在这里，它们在用户数据目录，见 app/core/bootstrap.py。
datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "static"), "static"),
    (str(ROOT / "alembic"), "alembic"),
    (str(ROOT / "alembic.ini"), "."),
]

# ---------------------------------------------------------------- 隐式依赖
# 下面这些包在运行时才被动态导入，PyInstaller 的静态分析扫不到。
# 漏一个的后果是：打包能成功，但一启动就 ModuleNotFoundError —— 而且
# 只有真正跑到那条路径才会暴露，很难在构建阶段发现。
hiddenimports = [
    # uvicorn 的协议/事件循环/生命周期实现全是按名字动态加载的
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # 数据库驱动与迁移
    "aiosqlite",
    "alembic.ddl.sqlite",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    # 密码哈希与模板
    "argon2",
    "argon2.profiles",
    "jinja2.ext",
    # pydantic-settings 读取 .env 需要
    "dotenv",
]

# ---------------------------------------------------------------- 排除项
# 明确排掉用不到的重型依赖，能显著缩小体积。
# 注意不能排 tkinter —— 启动器的图形界面要用。
excludes = [
    "pytest",
    "ruff",
    "black",
    "PIL",
    "numpy",
    "pandas",
    "matplotlib",
    "tkinter.test",
    "test",
    "unittest",
    "pydoc_data",
]

a = Analysis(
    [str(ROOT / "app" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="原神抽卡模拟器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压缩常被杀软误报，不用
    console=False,      # 不弹黑色控制台窗口；启动器自带 GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="原神抽卡模拟器",
)
