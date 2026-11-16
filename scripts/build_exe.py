"""打包 Clearance Guard 为单文件 exe。

用法：
    python scripts/build_exe.py            # 构建
    python scripts/build_exe.py --clean    # 先清 build/dist 再构建

产物：dist/ClearanceGuard.exe（图标 = assets/app_icon.ico，无控制台窗口）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "ClearanceGuard.spec"
ICON = ROOT / "assets" / "app_icon.ico"
EXE = ROOT / "dist" / "ClearanceGuard.exe"


def main() -> int:
    if not ICON.is_file():
        print(f"[ERROR] 缺少图标文件：{ICON}")
        return 1
    if not SPEC.is_file():
        print(f"[ERROR] 缺少 spec：{SPEC}")
        return 1

    if "--clean" in sys.argv:
        for path in (ROOT / "build", ROOT / "dist"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                print(f"已清理 {path}")

    started = time.time()
    command = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"]
    print("执行：", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print(f"[ERROR] PyInstaller 失败，退出码 {result.returncode}")
        return result.returncode

    if not EXE.is_file():
        print(f"[ERROR] 未生成 {EXE}")
        return 1
    size_mb = EXE.stat().st_size / 1024 / 1024
    print(f"\n完成：{EXE}  （{size_mb:.1f} MB，耗时 {time.time() - started:.0f}s）")
    print("图标：exe 资源、标题栏、任务栏、开始菜单快捷方式均使用 assets/app_icon.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
