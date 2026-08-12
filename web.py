"""ABI Tools Hub Web 控制台。

纯标准库实现：http.server.ThreadingHTTPServer 提供 JSON API 与单页前端，
前端页面为 static/index.html（内嵌 CSS/JS，无框架无构建）。
所有耗时操作（更新 / 搭建环境 / 全量刷新）均在后台线程执行，不阻塞 HTTP 响应。
"""

import json
import shutil
import subprocess
import threading
import time
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import core
import jobs

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

# 快速状态缓存：启动时后台线程并行 check_status（git fetch 较慢），
# 就绪前 /api/status 只返回本地瞬时信息（exists/env_ready，不走网络）
_status_lock = threading.Lock()
_status_cache = None  # dict: tool_id -> check_status 结果；None 表示尚未就绪
_status_checking = False


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


def _find_action(tool: dict, action_id: str):
    """在工具的 actions 中按 id 查找操作，找不到返回 None。"""
    for action in tool.get("actions", []):
        if action["id"] == action_id:
            return action
    return None


def _tools_payload() -> dict:
    """组装 /api/tools 响应：工具展示字段 + actions 定义，供前端渲染操作表单。"""
    tools = []
    for tool in core.load_tools():
        tools.append({
            "id": tool["id"],
            "name": tool["name"],
            "display": tool["display"],
            "description": tool["description"],
            "branch": tool["branch"],
            "entry": tool["entry"],
            "actions": tool.get("actions", []),
        })
    return {"tools": tools}


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
    """后台线程：自动检查更新、拉取主分支最新代码、搭建环境。完成后刷新状态缓存。"""
    global _refresh_running
    with _state_lock:
        if _refresh_running:
            return
        _refresh_running = True
    log("开始自动检查更新并搭建环境（并行执行，git fetch 可能需要几分钟）...")
    try:
        for line in core.refresh_all():
            log(line)
        log("自动更新与环境检查完成。")
    except Exception as exc:
        log(f"错误：自动更新失败：{exc}")
    finally:
        with _state_lock:
            _refresh_running = False
    # 更新/搭建完成后重新并行拉取各工具状态，让缓存反映最新结果
    threading.Thread(target=_check_status_background, daemon=True).start()


def _quick_status(tool: dict) -> dict:
    """本地瞬时状态：只看文件系统（exists/env_ready），不执行 git 网络操作。"""
    path = Path(tool["path"])
    exists = (path / ".git").is_dir()
    return {
        "id": tool["id"],
        "exists": exists,
        "branch": None,
        "behind": None,
        "ahead": None,
        "last_commit": None,
        "env_ready": exists and (path / ".venv").is_dir(),
        "update_available": None,
    }


def _check_status_background() -> None:
    """后台线程：并行 check_status 全部工具并填充缓存（总耗时接近最慢单个 fetch）。"""
    global _status_cache, _status_checking
    with _status_lock:
        if _status_checking:
            return
        _status_checking = True
    try:
        results = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            for status in pool.map(core.check_status, core.load_tools()):
                results[status["id"]] = status
        with _status_lock:
            _status_cache = results
        log("后台状态检查完成。")
    except Exception as exc:
        log(f"错误：后台状态检查失败：{exc}")
    finally:
        with _status_lock:
            _status_checking = False


def _status_payload() -> dict:
    """组装 /api/status 响应。

    缓存就绪后返回完整 check_status 结果；未就绪时返回本地瞬时信息
    （exists/env_ready，git 字段为 None）。refreshing 表示后台状态检查
    或全量更新是否仍在进行。
    """
    with _state_lock:
        running = dict(_running)
        refresh_running = _refresh_running
    with _status_lock:
        cache = dict(_status_cache) if _status_cache is not None else None
        checking = _status_checking
    tools = []
    for tool in core.load_tools():
        if cache is not None and tool["id"] in cache:
            status = dict(cache[tool["id"]])
        else:
            status = _quick_status(tool)
        status["display"] = tool["display"]
        status["description"] = tool["description"]
        status["running"] = running.get(tool["id"])
        tools.append(status)
    return {
        "tools": tools,
        "refresh_running": refresh_running,
        "refreshing": refresh_running or checking,
    }


def _logs_payload(since: int) -> dict:
    """组装 /api/logs 响应：序号大于 since 的日志行 + 当前最大序号。"""
    with _log_lock:
        lines = [text for seq, text in _log_lines if seq > since]
        last = _log_seq
    return {"lines": lines, "last": last}


# /api/inspect-file 在该工具 .venv 中执行的驱动代码：
# 用 openpyxl 只读模式列出 Sheet 名或指定 Sheet 的首行表头，结果以标记行输出 JSON
_INSPECT_MARKER = "__INSPECT_RESULT__"
_INSPECT_CODE = r"""
import json
import sys

from openpyxl import load_workbook

path, what = sys.argv[1], sys.argv[2]
sheet = sys.argv[3] if len(sys.argv) > 3 else ""

wb = load_workbook(path, read_only=True)
try:
    if what == "sheets":
        result = list(wb.sheetnames)
    else:
        name = sheet or wb.sheetnames[0]
        if name not in wb.sheetnames:
            raise ValueError(f"Sheet 不存在：{name}")
        row = next(wb[name].iter_rows(min_row=1, max_row=1, values_only=True), ())
        result = [str(c).strip() for c in row if c is not None and str(c).strip()]
    print("__INSPECT_RESULT__" + json.dumps(result, ensure_ascii=False))
finally:
    wb.close()
"""


def _inspect_file(body: dict) -> tuple:
    """读取 Excel 文件的 Sheet 列表或首行表头（供前端动态下拉选项）。

    在对应工具的 .venv 里同步执行 openpyxl（read_only 模式），超时 120 秒。
    返回 (payload, http_status)；所有失败路径均为中文错误信息。
    """
    tool_id = str(body.get("tool_id") or "")
    what = str(body.get("what") or "")
    path_text = str(body.get("path") or "").strip().strip('"').strip("'")
    sheet = str(body.get("sheet") or "").strip()

    tool = _find_tool(tool_id)
    if tool is None:
        return {"ok": False, "error": f"未知工具：{tool_id}"}, 404
    if what not in ("sheets", "columns"):
        return {"ok": False, "error": "what 必须是 sheets 或 columns"}, 400
    if not path_text:
        return {"ok": False, "error": "请先填写文件路径"}, 400
    file_path = Path(path_text)
    if not file_path.is_file():
        return {"ok": False, "error": f"文件不存在：{path_text}"}, 400
    if file_path.suffix.lower() not in (".xlsx", ".xlsm"):
        return {"ok": False, "error": "只能读取 .xlsx 格式的 Excel 文件"}, 400
    if shutil.which("uv") is None:
        return {"ok": False, "error": "未找到 uv，无法读取文件"}, 500

    try:
        r = subprocess.run(
            ["uv", "run", "python", "-X", "utf8", "-c", _INSPECT_CODE,
             str(file_path), what, sheet],
            cwd=tool["path"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "读取超时（120 秒），文件可能过大或被占用"}, 500

    if r.returncode != 0:
        detail = ""
        for line in (r.stderr or "").strip().splitlines():
            line = line.strip()
            if line and "Warning" not in line:
                detail = line  # 取最后一行有效报错
        return {"ok": False, "error": f"读取 Excel 失败：{detail or '未知错误'}"}, 400

    for line in (r.stdout or "").splitlines():
        if line.startswith(_INSPECT_MARKER):
            try:
                options = json.loads(line[len(_INSPECT_MARKER):])
            except json.JSONDecodeError:
                break
            return {"ok": True, "options": options}, 200
    return {"ok": False, "error": "读取 Excel 失败：未获得有效结果"}, 500


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
        parts = [p for p in parsed.path.split("/") if p]

        if parsed.path in ("/", "/index.html"):
            self._send_index()
        elif parsed.path == "/api/status":
            self._send_json(_status_payload())
        elif parsed.path == "/api/tools":
            self._send_json(_tools_payload())
        elif parsed.path == "/api/jobs":
            self._send_json({"jobs": jobs.get_jobs()})
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "job":
            # /api/job/<job_id>?since=N —— 任务增量日志轮询
            qs = parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            result = jobs.get_job_log(parts[2], since=since)
            if result is None:
                self._send_json({"error": f"任务不存在：{parts[2]}"}, status=404)
            else:
                self._send_json(result)
        elif parsed.path == "/api/logs":
            qs = parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._send_json(_logs_payload(since))
        else:
            self._send_json({"error": "not found"}, status=404)

    def _read_json_body(self):
        """读取并解析请求体 JSON，失败返回 None（调用方负责回 400）。"""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        if parsed.path == "/api/refresh-all":
            threading.Thread(target=_refresh_all_background, daemon=True).start()
            self._send_json({"ok": True, "message": "已开始全量更新与环境搭建（后台执行）"})
            return

        # /api/inspect-file —— 读取 Excel 的 Sheet 列表 / 首行表头，供动态下拉选项
        if parsed.path == "/api/inspect-file":
            body = self._read_json_body()
            if body is None or not isinstance(body, dict):
                self._send_json({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)
                return
            payload, status = _inspect_file(body)
            self._send_json(payload, status=status)
            return

        # /api/run/<tool_id>/<action_id> —— 启动工具操作任务，返回 {"job_id": "..."}
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "run":
            tool_id, action_id = parts[2], parts[3]
            tool = _find_tool(tool_id)
            if tool is None:
                self._send_json({"ok": False, "error": f"未知工具：{tool_id}"}, status=404)
                return
            action = _find_action(tool, action_id)
            if action is None:
                self._send_json({"ok": False, "error": f"未知操作：{action_id}"}, status=404)
                return
            body = self._read_json_body()
            if body is None or not isinstance(body, dict):
                self._send_json({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)
                return
            try:
                job_id = jobs.start_job(tool, action, body.get("params") or {})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            log(f"[{tool_id}] 已开始「{action['label']}」（{job_id}）")
            self._send_json({"job_id": job_id})
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
    无论是否 auto_refresh，都会开后台线程并行检查各工具状态填充缓存；
    open_browser 为 True 时延迟 1 秒用默认浏览器打开控制台页面。
    """
    threading.Thread(target=_check_status_background, daemon=True).start()
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
