"""ABI Tools Hub 任务执行器。

纯标准库实现：后台线程以 subprocess 运行 tools.json 中声明的 action 命令，
实时逐行采集输出到内存日志，供 Web 前端轮询。

command 占位符规则（与 tools.json 配合）：
- command 为 argv 数组（不使用 shell），字符串元素中的 ``{key}`` 由同名输入值替换；
- 元素也可以是 ``{"args": ["--sheet", "{sheet}"], "omit_if_empty": true}``：
  若组内任一 ``{key}`` 对应的输入值为空，则整组参数省略（用于可选 CLI 参数）；
- 普通字符串元素整体恰好是 ``{key}`` 且值为空时，该元素同样被省略，
  嵌在长字符串（如 -c 驱动代码）中的 ``{key}`` 替换为空字符串。
"""

import itertools
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# 内存中最多保留的任务数（超出后删除最旧的）
MAX_JOBS = 50
# 单任务日志行数上限（超出后截断头部）
MAX_JOB_LOG_LINES = 5000

_lock = threading.Lock()
_jobs = {}  # job_id -> job dict
_order = []  # job_id 按启动顺序排列，用于淘汰最旧任务
_id_counter = itertools.count(1)

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _clean_path(value: str) -> str:
    """清理拖拽/粘贴进来的路径：去首尾空白、去引号、去 PowerShell 的 ``& `` 前缀。"""
    value = str(value).strip()
    if value.startswith("&"):
        value = value[1:].strip()
    return value.strip('"').strip("'").strip()


def _validate_and_build_params(action: dict, params: dict) -> dict:
    """校验输入并按类型清理，返回 key -> 字符串值 的映射。

    必填输入为空时抛出 ValueError（中文错误信息），供 Web 层返回 400。
    """
    params = params or {}
    values = {}
    for spec in action.get("inputs", []):
        key = spec["key"]
        raw = params.get(key)
        if raw is None or str(raw).strip() == "":
            raw = spec.get("default", "")
        if spec.get("type") == "path":
            value = _clean_path(raw)
        else:
            value = str(raw).strip()
        if spec.get("required") and not value:
            raise ValueError(f"缺少必填参数：{spec.get('label', key)}")
        values[key] = value
    return values


def _build_argv(command: list, values: dict) -> list:
    """按占位符规则把 command 模板展开为最终 argv 数组。"""
    argv = []

    def _substitute(text: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), text)

    for element in command:
        if isinstance(element, dict):
            # {"args": [...], "omit_if_empty": true}：组内任一占位符值为空则整组省略
            group = element.get("args", [])
            keys = {k for item in group for k in _PLACEHOLDER_RE.findall(item)}
            if element.get("omit_if_empty") and any(not values.get(k) for k in keys):
                continue
            argv.extend(_substitute(item) for item in group)
            continue
        # 普通字符串元素：整体为 {key} 且值为空时省略该元素
        match = _PLACEHOLDER_RE.fullmatch(element)
        if match and not values.get(match.group(1), ""):
            continue
        argv.append(_substitute(element))
    return argv


def _builtin_check_env(tool: dict, action: dict, values: dict) -> tuple:
    """内置操作：检查工具目录下 .env 是否存在、MTOOL_USER / MTOOL_PASS 是否已填。

    只报告是否已设置，绝不输出明文。返回 (日志行列表, exit_code)。
    """
    env_file = Path(tool["path"]) / ".env"
    lines = []
    if not env_file.exists():
        lines.append("⚠ .env 文件不存在，请复制 .env.example 并填写账号密码")
        return lines, 1
    lines.append("✔ .env 文件存在")
    found = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            found[key.strip()] = value.strip().strip('"').strip("'")
    ok = True
    for key in ("MTOOL_USER", "MTOOL_PASS"):
        if found.get(key):
            lines.append(f"✔ {key} 已设置")
        else:
            lines.append(f"⚠ {key} 未设置")
            ok = False
    return lines, 0 if ok else 1


# kind == "builtin" 的 action 由 Hub 自己实现，按 action id 注册
_BUILTINS = {"check-env": _builtin_check_env}


def _new_job(tool: dict, action: dict) -> dict:
    """创建任务记录并登记到内存表（调用方需已持有 _lock）。"""
    job_id = f"job-{next(_id_counter)}"
    job = {
        "id": job_id,
        "tool_id": tool["id"],
        "tool_display": tool["display"],
        "action_id": action["id"],
        "action_label": action["label"],
        "status": "running",
        "exit_code": None,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seq": 0,
        "lines": [],
    }
    _jobs[job_id] = job
    _order.append(job_id)
    while len(_order) > MAX_JOBS:
        _jobs.pop(_order.pop(0), None)
    return job


def _append_line(job: dict, text: str) -> None:
    """追加一行日志（带单调递增 seq），超过上限截断头部。"""
    with _lock:
        job["seq"] += 1
        job["lines"].append({"seq": job["seq"], "text": text})
        if len(job["lines"]) > MAX_JOB_LOG_LINES:
            del job["lines"][: len(job["lines"]) - MAX_JOB_LOG_LINES]


def _finish_job(job: dict, exit_code: int) -> None:
    """记录退出码并落定状态：0 为 done，非 0 为 error。"""
    with _lock:
        job["exit_code"] = exit_code
        job["status"] = "done" if exit_code == 0 else "error"


def _run_subprocess(job: dict, argv: list, cwd: str) -> None:
    """后台线程：启动子进程并逐行采集输出（stdout/stderr 合并）。"""
    # Windows 下用 CREATE_NO_WINDOW 避免子进程弹出控制台窗口
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,  # 交互式脚本读不到输入会尽快报错，而不是卡死
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except Exception as exc:
        _append_line(job, f"⚠ 进程启动失败：{exc}")
        _finish_job(job, 1)
        return

    for line in proc.stdout:
        _append_line(job, line.rstrip("\r\n"))
    proc.wait()
    _finish_job(job, proc.returncode)


def start_job(tool: dict, action: dict, params: dict) -> str:
    """校验输入并启动任务，返回 job_id。校验失败抛 ValueError（中文错误信息）。"""
    values = _validate_and_build_params(action, params)

    with _lock:
        job = _new_job(tool, action)
    job_id = job["id"]

    if action.get("kind") == "builtin":
        # 内置操作不跑 subprocess，同步执行后直接落定结果
        handler = _BUILTINS.get(action["id"])
        if handler is None:
            _append_line(job, f"⚠ 未实现的内置操作：{action['id']}")
            _finish_job(job, 1)
        else:
            try:
                lines, exit_code = handler(tool, action, values)
            except Exception as exc:
                lines, exit_code = [f"⚠ 内置操作执行失败：{exc}"], 1
            for line in lines:
                _append_line(job, line)
            _finish_job(job, exit_code)
        return job_id

    argv = _build_argv(action["command"], values)
    _append_line(job, f"$ {' '.join(argv)}")
    threading.Thread(
        target=_run_subprocess, args=(job, argv, tool["path"]), daemon=True
    ).start()
    return job_id


def get_jobs() -> list:
    """所有任务摘要，按启动时间倒序。"""
    with _lock:
        jobs = [
            {
                "id": job["id"],
                "tool_id": job["tool_id"],
                "tool_display": job["tool_display"],
                "action_label": job["action_label"],
                "status": job["status"],
                "started_at": job["started_at"],
                "exit_code": job["exit_code"],
            }
            for job in (_jobs[job_id] for job_id in _order)
        ]
    jobs.reverse()
    return jobs


def get_job_log(job_id: str, since: int = 0) -> dict:
    """任务增量日志：{status, exit_code, lines: [{seq, text}]}，lines 只含 seq > since。"""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {
            "status": job["status"],
            "exit_code": job["exit_code"],
            "lines": [line for line in job["lines"] if line["seq"] > since],
        }
