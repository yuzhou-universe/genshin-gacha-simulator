"""一键构建 Windows 分发件。

依次完成：
  1. 从 SVG 生成多尺寸图标（packaging/app.ico）
  2. PyInstaller 打包成 onedir 可执行程序
  3. 压缩出免安装便携版 ZIP
  4. 用 Inno Setup 编译出安装包 EXE（内含卸载程序）

用法：
    .venv/Scripts/python.exe build_windows.py

前置：pip install pyinstaller；Inno Setup 6（winget install JRSoftware.InnoSetup）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# 中文 Windows 的控制台默认是 GBK。构建过程中会回显第三方工具的原始输出
# （Inno Setup 的日志里就带非 GBK 字符），直接 print 会抛 UnicodeEncodeError
# 让整个构建在最后一步崩掉。这里切成 UTF-8 并容错。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - 老环境或已重定向
        pass

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
APP_NAME = "原神抽卡模拟器"
VERSION = "1.0.0"

ISCC_CANDIDATES = [
    Path.home() / "AppData/Local/Programs/Inno Setup 6/ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def step(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(f"  $ {' '.join(str(c) for c in cmd[:3])} ...")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-3000:], file=sys.stderr)
        raise SystemExit(f"命令失败（退出码 {result.returncode}）：{cmd[0]}")
    # 只回显最后几行，避免刷屏
    for line in [ln for ln in result.stdout.splitlines() if ln.strip()][-3:]:
        print(f"    {line}")


def find_iscc() -> Path:
    for path in ISCC_CANDIDATES:
        if path.is_file():
            return path
    raise SystemExit(
        "找不到 Inno Setup 的 ISCC.exe。\n"
        "装一个即可（免管理员）：winget install JRSoftware.InnoSetup --scope user"
    )


def build_icon() -> None:
    step("1/4  生成图标")
    run([sys.executable, str(ROOT / "packaging" / "make_icon.py")])


def build_exe() -> None:
    step("2/4  PyInstaller 打包")
    if DIST.exists():
        shutil.rmtree(DIST, ignore_errors=True)
    run(
        [sys.executable, "-m", "PyInstaller", "gacha.spec", "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build")],
        cwd=ROOT,
    )
    target = DIST / APP_NAME
    if not (target / f"{APP_NAME}.exe").is_file():
        raise SystemExit(f"打包结果的目录结构不符合预期：{target}")
    print(f"    [OK] {target}")


def build_portable() -> Path:
    step("3/4  生成免安装便携版 ZIP")
    RELEASE.mkdir(exist_ok=True)
    out = RELEASE / f"{APP_NAME}-{VERSION}-便携版.zip"
    if out.exists():
        out.unlink()

    source = DIST / APP_NAME
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(source))
                count += 1
        # 说明文件放进压缩包的顶层，解压就能看到
        zf.write(ROOT / "packaging" / "使用说明.txt", "使用说明.txt")
    print(f"    [OK] {out.name}  （{count} 个文件，{out.stat().st_size // 1024 // 1024} MB）")
    return out


def build_installer() -> Path:
    step("4/4  编译安装包")
    iscc = find_iscc()
    RELEASE.mkdir(exist_ok=True)
    run([str(iscc), str(ROOT / "packaging" / "installer.iss")], cwd=ROOT)

    made = sorted(RELEASE.glob("*安装包.exe"), key=lambda p: p.stat().st_mtime)
    if not made:
        raise SystemExit("Inno Setup 没有产出安装包")
    out = made[-1]
    print(f"    [OK] {out.name}  （{out.stat().st_size // 1024 // 1024} MB）")
    return out


def main() -> int:
    print(f"\n构建 {APP_NAME} v{VERSION}")
    build_icon()
    build_exe()
    portable = build_portable()
    installer = build_installer()

    step("完成")
    print(f"  便携版：{portable}")
    print(f"  安装包：{installer}")
    print("\n  提示：两个产物都未做代码签名，首次运行 Windows 会提示「未知发布者」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
