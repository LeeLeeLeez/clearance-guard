"""Clearance Guard —— 本地 Web 服务（API + 前端托管）。

界面从 PySide6 换成了浏览器 / Tauri 窗口，判定逻辑仍是原来那套：
本文件只负责 HTTP 路由，业务在 `api/` 里，算法在 `clearance_check/` 里。

    python server.py                 # 默认 127.0.0.1:8770，自动开浏览器
    python server.py --no-browser --port 8800
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import socket
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# pythonw 启动时 sys.stdout / sys.stderr 可能是 None，print 会直接抛异常
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from api import dialog_service, edit_service, result_service, scan_service  # noqa: E402
from api.store import STATE, board_summary, web_dist  # noqa: E402

DEFAULT_PORT = 8770
DEFAULT_HOST = "127.0.0.1"

_SERVER: list = []          # [httpd]，供 /api/shutdown 用


# ---------------------------------------------------------------- 处理器
def _h_shutdown(h, q, body):
    """优雅退出本服务（前端顶栏「退出」用）。"""
    def _stop():
        import time as _t
        _t.sleep(0.2)
        if _SERVER:
            try:
                _SERVER[0].shutdown()
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=_stop, daemon=True).start()
    return {"ok": True, "shutdown": True}


def _h_health(h, q, body):
    return {"ok": True, "app": "Clearance Guard", "version": "2.0"}


def _h_status(h, q, body):
    data = scan_service.status()
    data["summary"] = board_summary()
    return data


def _h_connect(h, q, body):
    return scan_service.connect()


def _h_disconnect(h, q, body):
    return scan_service.disconnect()


def _h_projects(h, q, body):
    return scan_service.list_projects()


def _h_open_project(h, q, body):
    return scan_service.open_project(str((body or {}).get("path") or ""))


def _h_scan(h, q, body):
    b = body or {}
    return scan_service.start_scan(
        str(b.get("project_path") or ""), bool(b.get("with_netlist", True))
    )


def _h_dialog_open(h, q, body):
    b = body or {}
    kind = str(b.get("kind") or "")
    if kind:
        return dialog_service.open_by_type(kind)
    return dialog_service.ask_open(
        title=str(b.get("title") or "选择文件"),
        initialdir=str(b.get("dir") or ""),
        initialfile=str(b.get("name") or ""),
    )


def _h_dialog_save(h, q, body):
    b = body or {}
    kind = str(b.get("kind") or "")
    if kind:
        return dialog_service.save_by_type(
            kind, str(b.get("name") or ""), str(b.get("dir") or "")
        )
    return dialog_service.ask_save(
        title=str(b.get("title") or "另存为"),
        initialdir=str(b.get("dir") or ""),
        initialfile=str(b.get("name") or ""),
    )


def _h_reveal(h, q, body):
    return dialog_service.reveal(str((body or {}).get("path") or ""))


def _h_scan_pcb(h, q, body):
    b = body or {}
    return scan_service.start_scan_pcb(
        str(b.get("project_path") or ""),
        region=b.get("region"),
        use_scan_box=bool(b.get("use_scan_box")),
        local=str(b.get("mode") or "") == "local",
    )


def _h_scan_box_get(h, q, body):
    return scan_service.get_scan_box()


def _h_scan_box_set(h, q, body):
    return scan_service.set_scan_box((body or {}).get("box"))


def _h_components(h, q, body):
    return edit_service.components((q.get("q") or [""])[0])


def _h_component_update(h, q, body):
    b = body or {}
    return edit_service.update_component(
        str(b.get("designator") or ""), b.get("patch") or {}
    )


def _h_scan_cancel(h, q, body):
    return scan_service.cancel_task()


def _h_task(h, q, body):
    return scan_service.task_status()


def _h_recover(h, q, body):
    return scan_service.recover_bridge()


def _h_demo(h, q, body):
    return scan_service.load_demo()


def _h_snapshot_export(h, q, body):
    return scan_service.export_snapshot(str((body or {}).get("path") or ""))


def _h_snapshot_import(h, q, body):
    return scan_service.import_snapshot(str((body or {}).get("path") or ""))


def _h_snapshots(h, q, body):
    return scan_service.snapshot_files()


def _h_summary(h, q, body):
    return board_summary()


def _h_nets(h, q, body):
    return edit_service.nets()


def _h_net_update(h, q, body):
    b = body or {}
    return edit_service.update_net(str(b.get("name") or ""), b.get("patch") or {})


def _h_nets_bulk(h, q, body):
    b = body or {}
    return edit_service.bulk_update(list(b.get("names") or []), b.get("patch") or {})


def _h_follow(h, q, body):
    query = (q.get("q") or [""])[0]
    return edit_service.follow_candidates(query)


def _h_reclassify(h, q, body):
    return edit_service.reclassify()


def _h_sheet_secondary(h, q, body):
    return edit_service.set_sheet_secondary(str((body or {}).get("sheet") or ""))


def _h_common_route(h, q, body):
    b = body or {}
    return edit_service.route_common(list(b.get("names") or []), str(b.get("side") or "power"))


def _h_sheets(h, q, body):
    return edit_service.sheets()


def _h_objects(h, q, body):
    return edit_service.objects()


def _h_mount(h, q, body):
    b = body or {}
    return edit_service.update_mount(str(b.get("name") or ""), b.get("patch") or {})


def _h_floating(h, q, body):
    b = body or {}
    return edit_service.update_floating(
        str(b.get("designator") or ""), b.get("patch") or {}
    )


def _h_cutout(h, q, body):
    b = body or {}
    return edit_service.update_cutout(int(b.get("index") or 0), b.get("patch") or {})


def _h_cutout_outlines(h, q, body):
    return edit_service.start_cutout_outlines(str((body or {}).get("board_path") or ""))


def _h_rules(h, q, body):
    return edit_service.rules()


def _h_rules_update(h, q, body):
    return edit_service.update_rules(body or {})


def _h_clearance(h, q, body):
    return result_service.run_clearance()


def _h_violations(h, q, body):
    return result_service.results(
        (q.get("kind") or ["component"])[0],
        (q.get("severity") or ["all"])[0],
        (q.get("search") or [""])[0],
        (q.get("rule") or [""])[0],
        (q.get("isolation") or [""])[0] in ("1", "true", "yes"),
    )


def _h_export_csv(h, q, body):
    return result_service.export_csv(str((body or {}).get("path") or ""))


def _h_layout(h, q, body):
    return result_service.layout()


def _h_outline_html(h, q, body):
    return result_service.import_outline_html(str((body or {}).get("path") or ""))


def _h_solve(h, q, body):
    return result_service.solve(list((body or {}).get("rect") or []))


def _h_apply_moves(h, q, body):
    return result_service.apply_moves()


def _h_locate(h, q, body):
    return result_service.locate(str((body or {}).get("designator") or ""))


def _h_near_miss(h, q, body):
    return result_service.near_miss()


ROUTES = {
    ("GET", "/api/health"): _h_health,
    ("POST", "/api/shutdown"): _h_shutdown,
    ("GET", "/api/status"): _h_status,
    ("POST", "/api/connect"): _h_connect,
    ("POST", "/api/disconnect"): _h_disconnect,
    ("GET", "/api/projects"): _h_projects,
    ("POST", "/api/open-project"): _h_open_project,
    ("POST", "/api/scan"): _h_scan,
    ("POST", "/api/scan-cancel"): _h_scan_cancel,
    ("POST", "/api/dialog/open"): _h_dialog_open,
    ("POST", "/api/dialog/save"): _h_dialog_save,
    ("POST", "/api/reveal"): _h_reveal,
    ("POST", "/api/scan-pcb"): _h_scan_pcb,
    ("GET", "/api/scan-box"): _h_scan_box_get,
    ("POST", "/api/scan-box"): _h_scan_box_set,
    ("GET", "/api/components"): _h_components,
    ("POST", "/api/component"): _h_component_update,
    ("GET", "/api/task"): _h_task,
    ("POST", "/api/recover"): _h_recover,
    ("POST", "/api/demo"): _h_demo,
    ("POST", "/api/snapshot/export"): _h_snapshot_export,
    ("POST", "/api/snapshot/import"): _h_snapshot_import,
    ("GET", "/api/snapshots"): _h_snapshots,
    ("GET", "/api/summary"): _h_summary,
    ("GET", "/api/nets"): _h_nets,
    ("POST", "/api/net"): _h_net_update,
    ("POST", "/api/nets/bulk"): _h_nets_bulk,
    ("GET", "/api/follow-candidates"): _h_follow,
    ("POST", "/api/reclassify"): _h_reclassify,
    ("POST", "/api/sheet/secondary"): _h_sheet_secondary,
    ("POST", "/api/common/route"): _h_common_route,
    ("GET", "/api/sheets"): _h_sheets,
    ("GET", "/api/objects"): _h_objects,
    ("POST", "/api/mount"): _h_mount,
    ("POST", "/api/floating"): _h_floating,
    ("POST", "/api/cutout"): _h_cutout,
    ("POST", "/api/cutout/outlines"): _h_cutout_outlines,
    ("GET", "/api/rules"): _h_rules,
    ("POST", "/api/rules"): _h_rules_update,
    ("POST", "/api/clearance"): _h_clearance,
    ("GET", "/api/violations"): _h_violations,
    ("POST", "/api/export-csv"): _h_export_csv,
    ("GET", "/api/layout"): _h_layout,
    ("POST", "/api/outline-html"): _h_outline_html,
    ("POST", "/api/solve"): _h_solve,
    ("POST", "/api/apply-moves"): _h_apply_moves,
    ("POST", "/api/locate"): _h_locate,
    ("GET", "/api/near-miss"): _h_near_miss,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ClearanceGuard/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默掉默认的每请求日志
        return

    # -------------------------------------------------- 基础
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # -------------------------------------------------- 路由
    def _handle(self, method: str):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        q = parse_qs(parsed.query)

        if path.startswith("/api/"):
            fn = ROUTES.get((method, path))
            if fn is None:
                self._send_json(404, {"ok": False, "error": "接口不存在", "path": path})
                return
            try:
                body = self._read_body() if method in ("POST", "PUT") else {}
                self._send_json(200, fn(self, q, body))
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                if name in ("ServiceError", "SnapshotError"):
                    self._send_json(400, {"ok": False, "error": str(exc)})
                else:
                    traceback.print_exc()
                    self._send_json(
                        500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    )
            return

        self._serve_static(path)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -------------------------------------------------- 静态
    def _serve_static(self, path: str):
        dist = str(web_dist())
        if path in ("/", ""):
            path = "/index.html"
        rel = posixpath.normpath(path).lstrip("/")
        target = os.path.join(dist, rel.replace("/", os.sep))
        if not os.path.abspath(target).startswith(os.path.abspath(dist)):
            self._send_json(403, {"ok": False, "error": "非法路径"})
            return
        if not os.path.isfile(target):
            target = os.path.join(dist, "index.html")
        if not os.path.isfile(target):
            body = (
                "<h1>前端尚未构建</h1><p>请在 web/ 目录执行 "
                "<code>npm install &amp;&amp; npm run build</code>。</p>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port(host: str, port: int) -> int:
    for p in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return port


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Clearance Guard（oink-ui 版）")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--strict-port", action="store_true")
    args = ap.parse_args(argv)

    port = args.port if args.strict_port else _free_port(args.host, args.port)
    server_cls = ThreadingHTTPServer
    if args.strict_port:
        class _Strict(ThreadingHTTPServer):
            allow_reuse_address = False

        server_cls = _Strict
    try:
        httpd = server_cls((args.host, port), Handler)
    except OSError as exc:
        print(f"[ERROR] 端口 {args.host}:{port} 无法绑定：{exc}", file=sys.stderr)
        return 2

    _SERVER.clear()
    _SERVER.append(httpd)

    url = f"http://{args.host}:{port}/"
    print("=" * 58)
    print("  Clearance Guard（oink-ui 版）已启动")
    print(f"  地址：{url}")
    print("  关闭：在本窗口按 Ctrl+C")
    print("=" * 58)

    # 启动时尝试恢复上次快照（和原版一致）
    try:
        from api.store import last_scan_path

        path = last_scan_path()
        if path.is_file():
            scan_service.import_snapshot(str(path))
            print("  已恢复上次扫描快照")
    except Exception as exc:  # noqa: BLE001
        print(f"  （恢复上次快照失败：{exc}）")

    # 文件对话框要在专用 Tk 线程里弹（tkinter 单线程约束）
    dialog_service.start()

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出 …")
    finally:
        with STATE.lock:
            sess = STATE.session
        if sess is not None:
            try:
                sess.disconnect()
            except Exception:  # noqa: BLE001
                pass
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
