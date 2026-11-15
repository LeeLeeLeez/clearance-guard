"""最小化的 MCP stdio 客户端（JSON-RPC 2.0，按行分隔）。

只依赖标准库，用于把 eda-agent 作为子进程拉起并与实时的 Altium 会话通信：

    initialize -> notifications/initialized -> tools/call

eda-agent 在 minimal toolset 下只暴露 tool_catalog / tool_invoke 两个工具，
本客户端通过 tool_invoke 调用其余全部工具。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2024-11-05"

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class McpError(RuntimeError):
    """MCP 通信失败。"""


class McpTimeout(McpError):
    """等待响应超时。"""


class McpStdioClient:
    """把 MCP 服务作为子进程运行，并按 JSON-RPC 调用其工具。"""

    def __init__(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        name: str = "clearance-gui",
    ):
        self.command = command
        self.cwd = cwd
        self._env = env
        self._name = name
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stderr_tail: list[str] = []
        self._dead_reason: str | None = None
        self._reader: threading.Thread | None = None
        self._err_reader: threading.Thread | None = None

    # ---- 生命周期 -------------------------------------------------
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = 60.0) -> dict[str, Any]:
        if self._proc is not None:
            return {}
        env = dict(os.environ)
        if self._env:
            env.update(self._env)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self._proc = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise McpError(f"无法启动 EDA Agent 服务: {exc}") from exc

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._err_reader.start()

        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self._name, "version": "1.0"},
            },
            timeout=timeout,
        )
        self.notify("notifications/initialized")
        return result

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin and proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:  # noqa: BLE001
            pass

    # ---- 底层收发 -------------------------------------------------
    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while True:
            try:
                line = stream.readline()
            except Exception:  # noqa: BLE001
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = message.get("id")
            if msg_id is None:
                continue
            with self._lock:
                waiter = self._pending.pop(int(msg_id), None)
            if waiter is not None:
                waiter.put(message)

        self._dead_reason = "EDA Agent 服务进程已退出"
        with self._lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for waiter in waiters:
            waiter.put({"error": {"message": self._dead_reason}})

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            text = line.rstrip()
            if not text:
                continue
            self._stderr_tail.append(text)
            del self._stderr_tail[:-40]

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-12:])

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.alive or self._proc.stdin is None:
            raise McpError(self._dead_reason or "EDA Agent 服务未运行")
        try:
            with self._write_lock:
                self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            raise McpError(f"写入 MCP 请求失败: {exc}") from exc

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def request(self, method: str, params: dict[str, Any] | None = None,
                timeout: float = 120.0) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            waiter: queue.Queue = queue.Queue()
            self._pending[msg_id] = waiter
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        try:
            message = waiter.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(msg_id, None)
            raise McpTimeout(
                f"MCP 请求超时（{method}，{timeout:.0f}s）。Altium 脚本桥可能未启动。"
            ) from None
        if "error" in message:
            error = message["error"]
            text = error.get("message") if isinstance(error, dict) else str(error)
            raise McpError(f"{method} 失败: {text}")
        return message.get("result", {}) or {}

    # ---- 工具调用 -------------------------------------------------
    def list_tools(self, timeout: float = 60.0) -> list[dict[str, Any]]:
        result = self.request("tools/list", {}, timeout=timeout)
        return result.get("tools", []) or []

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  timeout: float = 120.0) -> Any:
        """调用工具，返回工具结果（已把 content 里的 JSON 文本解码）。"""
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        if result.get("isError"):
            raise McpError(_content_text(result) or f"{name} 调用失败")
        text = _content_text(result)
        if text is None:
            return result
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"text": text}

    def invoke(self, tool: str, arguments: dict[str, Any] | None = None,
               timeout: float = 120.0) -> Any:
        """通过 tool_invoke 调用任意注册工具。"""
        payload = self.call_tool(
            "tool_invoke",
            {"name": tool, "arguments": arguments or {}},
            timeout=timeout,
        )
        if isinstance(payload, dict) and payload.get("error"):
            raise McpError(f"{tool}: {payload['error']}")
        return payload


def _content_text(result: dict[str, Any]) -> str | None:
    content = result.get("content")
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        joined = "".join(parts)
        return joined if joined else None
    if isinstance(content, str):
        return content
    return None


# --------------------------------------------------------------------------
# 服务进程命令探测
# --------------------------------------------------------------------------
DEFAULT_AGENT_ROOT = Path(r"E:\AI_model\eda-agent-0.8.1")

_COMMAND_CACHE: dict[tuple[str, str], list[str]] = {}


def find_server_command(agent_root: str | Path | None = None,
                        python_exe: str = "") -> list[str]:
    """找到能运行 eda-agent MCP 服务的命令。

    优先级：显式 python > 环境变量 EDA_AGENT_PYTHON > 仓库 .venv > PATH 上的
    eda-agent > 当前解释器。
    """
    candidates: list[list[str]] = []

    if python_exe:
        candidates.append([python_exe, "-m", "eda_agent", "--headless"])
    env_python = os.environ.get("EDA_AGENT_PYTHON", "").strip()
    if env_python:
        candidates.append([env_python, "-m", "eda_agent", "--headless"])

    root = Path(agent_root) if agent_root else Path(
        os.environ.get("EDA_AGENT_ROOT", DEFAULT_AGENT_ROOT)
    )
    for rel in (
        r".venv\Scripts\python.exe",
        r"venv\Scripts\python.exe",
    ):
        exe = root / rel
        if exe.is_file():
            candidates.append([str(exe), "-m", "eda_agent", "--headless"])

    exe = shutil.which("eda-agent")
    if exe:
        candidates.append([exe, "--headless"])

    candidates.append([os.sys.executable, "-m", "eda_agent", "--headless"])

    cache_key = (str(agent_root or DEFAULT_AGENT_ROOT), python_exe)
    cached = _COMMAND_CACHE.get(cache_key)
    if cached:
        return list(cached)

    for command in candidates:
        if _command_works(command):
            _COMMAND_CACHE[cache_key] = list(command)
            return command
    return candidates[0]


def _command_works(command: list[str]) -> bool:
    """用 --version 快速验证命令可用。"""
    try:
        completed = subprocess.run(
            command[:-1] + ["--version"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        return False
    return completed.returncode == 0 and "eda-agent" in (completed.stdout or "").lower()


def wait_for_workspace(workspace: Path, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if workspace.exists():
            return True
        time.sleep(0.2)
    return workspace.exists()
