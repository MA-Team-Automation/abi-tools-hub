"""ABI Tools Hub Web 控制台。

纯标准库实现：http.server.ThreadingHTTPServer 提供 JSON API 与单页前端，
前端页面为 static/index.html（内嵌 CSS/JS，无框架无构建）。
所有耗时操作（更新 / 搭建环境 / 全量刷新）均在后台线程执行，不阻塞 HTTP 响应。
"""

import json
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import core

ROOT = Path(__file__).resolve().parent
INDEX_HTML = ROOT / "static" / "index.html"

# 内存日志环形缓冲：保留最近 500 行，每行带单调递增序号供前端增量拉取
MAX_LOG_LINES = 500
_log_lock = threading.Lock()
_log_lines = deque(maxlen=MAX_LOG_LINES)  # 元素为 (seq, text)
_log_seq = 0

# 各工具正在执行的操作（"update" / "setup"），前端据此禁用对应按钮
_state_lock = threading.Lock()
_running = {}
_refresh_running = False


def log(message: str) -> None:
    """追加一行带时间戳的日志到内存环形缓冲。"""
    global _log_seq
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    with _log_lock:
        _log_seq += 1
        _log_lines.append((_log_seq, line))


def _find_tool(tool_id: str):
    """按 id 在 tools.json 清单中查找工具，找不到返回 None。"""
    for tool in core.load_tools():
        if tool["id"] == tool_id:
            return tool
    return None


def _run_action(tool: dict, action: str, func) -> None:
    """后台线程执行耗时操作，写日志并维护 running 状态。"""
    action_name = {"update": "更新", "setup": "搭建环境"}[action]
    with _state_lock:
        _running[tool["id"]] = action
    log(f"[{tool['id']}] 开始{action_name}...")
    try:
        for line in str(func(tool)).splitlines():
            log(line)
        log(f"[{tool['id']}] {action_name}完成")
    except Exception as exc:
        log(f"[{tool['id']}] 错误：{action_name}失败：{exc}")
    finally:
        with _state_lock:
            _running.pop(tool["id"], None)


def _refresh_all_background() -> None:
    """后台线程：自动检查更新、拉取主分支最新代码、搭建环境。"""
    global _refresh_running
    with _state_lock:
        if _refresh_running:
            return
        _refresh_running = True
    log("开始自动检查更新并搭建环境（git fetch 可能需要几分钟）...")
    try:
        for line in core.refresh_all():
            log(line)
        log("自动更新与环境检查完成。")
    except Exception as exc:
        log(f"错误：自动更新失败：{exc}")
    finally:
        with _state_lock:
            _refresh_running = False


def _status_payload() -> dict:
    """组装 /api/status 响应：5 个工具的 check_status 结果 + 展示字段 + running 状态。"""
    with _state_lock:
        running = dict(_running)
        refresh_running = _refresh_running
    tools = []
    for tool in core.load_tools():
        status = core.check_status(tool)
        status["display"] = tool["display"]
        status["description"] = tool["description"]
        status["running"] = running.get(tool["id"])
        tools.append(status)
    return {"tools": tools, "refresh_running": refresh_running}


def _logs_payload(since: int) -> dict:
    """组装 /api/logs 响应：序号大于 since 的日志行 + 当前最大序号。"""
    with _log_lock:
        lines = [text for seq, text in _log_lines if seq > since]
        last = _log_seq
    return {"lines": lines, "last": last}


class HubRequestHandler(BaseHTTPRequestHandler):
    """路由处理：静态页面 + JSON API。"""

    server_version = "AbiToolsHub/1.0"

    # 默认日志打到 stderr 太吵，静默处理
    def log_message(self, format, *args):
        pass

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        body = INDEX_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send_index()
        elif parsed.path == "/api/status":
            self._send_json(_status_payload())
        elif parsed.path == "/api/logs":
            qs = parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._send_json(_logs_payload(since))
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        if parsed.path == "/api/refresh-all":
            threading.Thread(target=_refresh_all_background, daemon=True).start()
            self._send_json({"ok": True, "message": "已开始全量更新与环境搭建（后台执行）"})
            return

        # /api/<update|setup|launch>/<tool_id>
        if len(parts) == 3 and parts[0] == "api" and parts[1] in ("update", "setup", "launch"):
            action, tool_id = parts[1], parts[2]
            tool = _find_tool(tool_id)
            if tool is None:
                self._send_json({"ok": False, "error": f"未知工具：{tool_id}"}, status=404)
                return

            if action == "launch":
                # launch 本身很快（os.startfile / Popen 立即返回），同步执行
                try:
                    core.launch_tool(tool)
                    log(f"[{tool_id}] 已启动「{tool['display']}」")
                    self._send_json({"ok": True, "message": f"已启动「{tool['display']}」"})
                except Exception as exc:
                    log(f"[{tool_id}] 错误：启动失败：{exc}")
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return

            # update / setup 耗时较长，放后台线程执行
            func = core.update_tool if action == "update" else core.setup_env
            with _state_lock:
                if tool_id in _running:
                    self._send_json({"ok": False, "error": "该工具已有操作在进行中"}, status=409)
                    return
            threading.Thread(target=_run_action, args=(tool, action, func), daemon=True).start()
            action_name = "更新" if action == "update" else "搭建环境"
            self._send_json({"ok": True, "message": f"已开始{action_name}「{tool['display']}」（后台执行）"})
            return

        self._send_json({"error": "not found"}, status=404)


def serve(port: int, open_browser: bool = True, auto_refresh: bool = True) -> None:
    """启动 Web 控制台。

    auto_refresh 为 True 时先开 daemon 线程执行 refresh_all（写日志缓冲）；
    open_browser 为 True 时延迟 1 秒用默认浏览器打开控制台页面。
    """
    if auto_refresh:
        threading.Thread(target=_refresh_all_background, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", port), HubRequestHandler)
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    log(f"Web 控制台已启动：{url}")
    print(f"Web 控制台已启动：{url}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 Web 控制台...")
    finally:
        server.server_close()
