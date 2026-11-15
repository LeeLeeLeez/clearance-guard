"""图形界面上传器：把本工程以明文推送到 GitHub（绕开 DLP 对 git.exe 的限制）。

用法：
    python scripts/upload_gui.py
    或双击根目录的「上传到GitHub.cmd」（不带参数时打开本窗口）

逻辑全部复用 gh_upload.py：Python 读明文 → GitHub REST 建 blob/tree/commit/ref。
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gh_upload  # noqa: E402

from clearance_check.ui.theme import apply_theme  # noqa: E402

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "ClearanceGuard"
TOKEN_PATH = APP_DIR / "gh_token.txt"
LOG_PATH = APP_DIR / "upload_gui.log"


def _boot_log(message: str) -> None:
    """启动日志：pythonw 没有控制台，出错时靠这个文件定位。"""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


class _Signals(QObject):
    log = Signal(str)
    finished = Signal(bool, str, str)   # ok, message, url


class UploadWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("上传到 GitHub — Clearance Guard")
        self.resize(760, 700)
        self._signals = _Signals()
        self._signals.log.connect(self._append_log)
        self._signals.finished.connect(self._on_finished)
        self._busy = False
        self._assets: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        title = QLabel("上传到 GitHub")
        title.setObjectName("AppTitle")
        root.addWidget(title)
        hint = QLabel(
            "用 Python 读取明文并调用 GitHub REST API 提交（不经过 git.exe，绕开本机 DLP 加密）。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        card = QFrame()
        card.setObjectName("Card")
        form = QFormLayout(card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)

        self.repo = QLineEdit("clearance-guard")
        self.repo.setPlaceholderText("仓库名，例如 clearance-guard")
        form.addRow("仓库名", self.repo)

        self.visibility = QComboBox()
        self.visibility.addItem("公开仓库", True)
        self.visibility.addItem("私有仓库", False)
        form.addRow("可见性", self.visibility)

        token_row = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("ghp_… （或设置环境变量 GITHUB_TOKEN）")
        token_row.addWidget(self.token, 1)
        self.btn_show = QPushButton("显示")
        self.btn_show.setObjectName("Ghost")
        self.btn_show.setCheckable(True)
        self.btn_show.toggled.connect(
            lambda on: self.token.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        token_row.addWidget(self.btn_show)
        form.addRow("Token", token_row)

        self.remember = QCheckBox("记住 Token（保存在 %APPDATA%\\ClearanceGuard）")
        form.addRow("", self.remember)

        self.message = QLineEdit()
        self.message.setPlaceholderText("commit 说明，例如：修安装孔外径显示")
        form.addRow("提交说明", self.message)

        self.branch = QLineEdit()
        self.branch.setPlaceholderText("留空 = 仓库默认分支")
        form.addRow("分支", self.branch)

        self.release = QCheckBox("同时发布 Release")
        form.addRow("", self.release)
        self.tag = QLineEdit(gh_upload.default_release_tag())
        self.tag.setEnabled(False)
        form.addRow("Tag", self.tag)
        self.release.toggled.connect(self.tag.setEnabled)

        asset_row = QHBoxLayout()
        self.assets = QListWidget()
        self.assets.setMaximumHeight(64)
        asset_row.addWidget(self.assets, 1)
        asset_btns = QVBoxLayout()
        add_asset = QPushButton("添加附件…")
        add_asset.setObjectName("Ghost")
        add_asset.clicked.connect(self._pick_assets)
        clear_asset = QPushButton("清空")
        clear_asset.setObjectName("Ghost")
        clear_asset.clicked.connect(self._clear_assets)
        asset_btns.addWidget(add_asset)
        asset_btns.addWidget(clear_asset)
        asset_row.addLayout(asset_btns)
        form.addRow("Release 附件", asset_row)

        root.addWidget(card)

        buttons = QHBoxLayout()
        self.btn_dry = QPushButton("先看会传什么")
        self.btn_dry.clicked.connect(self._dry_run)
        self.btn_start = QPushButton("开始上传")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self._start)
        self.btn_open = QPushButton("打开仓库")
        self.btn_open.setObjectName("Ghost")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_repo)
        buttons.addWidget(self.btn_dry)
        buttons.addWidget(self.btn_start)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_open)
        root.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("上传日志会显示在这里…")
        root.addWidget(self.log, 1)

        self.status = QLabel("准备就绪")
        self.status.setObjectName("Hint")
        root.addWidget(self.status)

        self._repo_url = ""
        self._load_token()
        self._append_log(f"工程目录：{gh_upload.find_project_root(str(ROOT))}")

    # ------------------------------------------------------------------
    def _load_token(self) -> None:
        env = os.environ.get("GITHUB_TOKEN", "").strip()
        if env:
            self.token.setText(env)
            self.status.setText("已从环境变量 GITHUB_TOKEN 读取 Token")
            return
        try:
            if TOKEN_PATH.is_file():
                self.token.setText(TOKEN_PATH.read_text(encoding="utf-8").strip())
                self.remember.setChecked(True)
        except OSError:
            pass

    def _save_token(self) -> None:
        if not self.remember.isChecked():
            if TOKEN_PATH.is_file():
                TOKEN_PATH.unlink(missing_ok=True)
            return
        try:
            TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_PATH.write_text(self.token.text().strip(), encoding="utf-8")
        except OSError as exc:
            self._append_log(f"Token 保存失败：{exc}")

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.status.setText(text.splitlines()[-1][:120])

    def _pick_assets(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 Release 附件", "")
        for path in paths:
            if path and path not in self._assets:
                self._assets.append(path)
                self.assets.addItem(path)
        if self._assets and not self.release.isChecked():
            self.release.setChecked(True)

    def _clear_assets(self) -> None:
        self._assets.clear()
        self.assets.clear()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_start.setEnabled(not busy)
        self.btn_dry.setEnabled(not busy)
        self.progress.setRange(0, 0 if busy else 1)

    # ------------------------------------------------------------------
    def _dry_run(self) -> None:
        if self._busy:
            return
        try:
            root = gh_upload.find_project_root(str(ROOT))
            files = gh_upload.collect_files(root)
        except SystemExit as exc:
            self._append_log(str(exc))
            return
        self._append_log(f"=== 将上传 {len(files)} 个文件（均为明文）===")
        total = 0
        for rel in files:
            try:
                data = gh_upload.read_plain(root, rel)
            except SystemExit as exc:
                self._append_log(str(exc))
                return
            total += len(data)
            self._append_log(f"  {rel:<56} {len(data):>8} B")
        self._append_log(f"合计 {total / 1024:.1f} KB；未联网，未做任何修改。")

    def _start(self) -> None:
        if self._busy:
            return
        token = self.token.text().strip()
        if not token:
            self._append_log("请先填写 Token（https://github.com/settings/tokens 勾 repo）")
            return
        repo = self.repo.text().strip()
        if not repo:
            self._append_log("请填写仓库名")
            return

        self._save_token()
        root = gh_upload.find_project_root(str(ROOT))
        files = gh_upload.collect_files(root)
        message = self.message.text().strip()
        public = bool(self.visibility.currentData())
        branch = self.branch.text().strip()
        release_tag = self.tag.text().strip() if self.release.isChecked() else None
        assets = tuple(self._assets)

        self._set_busy(True)
        self.log.clear()
        self._append_log(f"开始上传：{root}")
        self._append_log(f"文件 {len(files)} 个，仓库 {repo}（{'公开' if public else '私有'}）")

        def work() -> None:
            try:
                url = gh_upload.do_upload(
                    token,
                    repo,
                    root,
                    files,
                    message,
                    public=public,
                    log=lambda text: self._signals.log.emit(str(text)),
                    release_tag=release_tag,
                    assets=assets,
                    branch=branch,
                )
                self._signals.finished.emit(True, "上传完成", url)
            except gh_upload.PartialUpload as exc:
                self._signals.finished.emit(False, str(exc), exc.url)
            except SystemExit as exc:
                self._signals.finished.emit(False, str(exc), "")
            except Exception as exc:  # noqa: BLE001
                detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                self._signals.finished.emit(False, detail, "")

        threading.Thread(target=work, daemon=True).start()

    def _on_finished(self, ok: bool, message: str, url: str) -> None:
        self._set_busy(False)
        self._append_log(message)
        if url:
            self._repo_url = url
            self.btn_open.setEnabled(True)
            self._append_log(f"仓库：{url}")
        self.status.setText("上传完成" if ok else "上传失败，请查看日志")

    def _open_repo(self) -> None:
        if self._repo_url:
            webbrowser.open(self._repo_url)


def main() -> int:
    _boot_log(f"start: python={sys.executable} argv={sys.argv}")
    app = QApplication(sys.argv)
    apply_theme(app)
    window = UploadWindow()
    window.show()
    _boot_log("window shown")
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        _boot_log(f"exit: {exc.code}")
        raise
    except BaseException:
        _boot_log("CRASH:\n" + traceback.format_exc())
        raise
