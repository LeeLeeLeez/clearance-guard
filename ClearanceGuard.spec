# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件 GUI exe，图标与窗口/任务栏保持一致。

注意：excludes 里只能写**模块名**。工程里的 json/、dev/、backup/ 只是数据目录，
不能写进来——``json`` 会误伤标准库 json 模块，导致 exe 启动即崩。
"""

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "pytest", "_pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ClearanceGuard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/app_icon.ico",
    version=None,
)
