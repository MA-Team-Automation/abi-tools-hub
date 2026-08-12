"""ABI Tools Hub 核心模块。

负责读取 tools.json、检查各工具仓库状态、克隆/更新代码、搭建环境、启动工具。
所有函数仅使用 Python 标准库，web.py（Web 控制台）与 tui.py 均基于本模块开发。
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 项目根目录（core.py 所在目录），tools.json 与其同级
ROOT = Path(__file__).resolve().parent
TOOLS_JSON = ROOT / "tools.json"

# git 子命令超时时间（秒）；fetch 走网络，给足 60 秒
GIT_TIMEOUT = 60
# uv sync 可能首次安装依赖，超时放宽
UV_TIMEOUT = 300


def load_tools() -> list:
    """读取 tools.json，把每个 tool 的 "dir" 解析为绝对路径存入 "path" 键后返回列表。"""
    data = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    tools = []
    for tool in data["tools"]:
        tool = dict(tool)
        # dir 是相对 Hub 根目录的相对路径，可能带空格或中文，resolve 为绝对路径
        tool["path"] = str((ROOT / tool["dir"]).resolve())
        tools.append(tool)
    return tools


def _run_git(path: str, args: list, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """在指定仓库执行 git 子命令，统一编码与超时。

    可能与其他自动化进程并发操作同一仓库，遇到 .git lock 错误时等待 2 秒重试一次。
    """
    for attempt in (1, 2):
        try:
            result = subprocess.run(
                ["git", "-C", path, *args],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 1, "", "git 命令超时")
        output = result.stdout + result.stderr
        if result.returncode != 0 and "unable to lock" in output and attempt == 1:
            time.sleep(2)  # 等待并发的 git 操作释放锁后重试
            continue
        return result


def _fill_remote_counts(status: dict, tool: dict) -> None:
    """基于现有 origin 引用计算 behind/ahead/update_available（不执行 fetch）。"""
    path = tool["path"]
    remote_ref = f"origin/{tool.get('branch', 'main')}"
    rb = _run_git(path, ["rev-list", "--count", f"HEAD..{remote_ref}"])
    ra = _run_git(path, ["rev-list", "--count", f"{remote_ref}..HEAD"])
    if rb.returncode == 0 and ra.returncode == 0:
        status["behind"] = int(rb.stdout.strip())
        status["ahead"] = int(ra.stdout.strip())
        status["update_available"] = status["behind"] > 0


def local_status(tool: dict) -> dict:
    """本地仓库状态（不执行 git fetch，纯本地命令）。

    返回 {"id", "exists", "branch", "behind", "ahead", "last_commit",
          "env_ready", "update_available"}。behind/ahead 基于现有 origin 引用，
    适合在刚完成一轮 fetch/pull 后使用（此时引用已是最新）。
    """
    path = tool["path"]
    status = {
        "id": tool["id"],
        "exists": False,
        "branch": None,
        "behind": None,
        "ahead": None,
        "last_commit": None,
        "env_ready": False,
        "update_available": None,
    }
    if not (Path(path) / ".git").is_dir():
        return status

    status["exists"] = True
    # 虚拟环境是否已搭建
    status["env_ready"] = (Path(path) / ".venv").is_dir()

    # 当前分支
    r = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if r.returncode == 0:
        status["branch"] = r.stdout.strip() or None

    # 最后一条提交的一行摘要
    r = _run_git(path, ["log", "-1", "--pretty=%h %s (%cr)"])
    if r.returncode == 0:
        status["last_commit"] = r.stdout.strip() or None

    _fill_remote_counts(status, tool)
    return status


def check_status(tool: dict) -> dict:
    """检查单个工具仓库状态（含 git fetch）。

    在 local_status 基础上先 fetch 远程最新引用再计算 behind/ahead；
    fetch 失败（无网络等）时静默降级，behind/ahead 保持 None。
    """
    status = local_status(tool)
    if not status["exists"]:
        return status
    branch = tool.get("branch", "main")
    r = _run_git(tool["path"], ["fetch", "origin", branch])
    if r.returncode == 0:
        # fetch 成功，基于最新引用重算（覆盖 local_status 中基于旧引用的结果）
        status["behind"] = status["ahead"] = status["update_available"] = None
        _fill_remote_counts(status, tool)
    else:
        # fetch 失败时与旧行为一致：behind/ahead 置 None
        status["behind"] = status["ahead"] = status["update_available"] = None
    return status


def ensure_repo(tool: dict) -> str:
    """确保仓库存在：path 不存在时 git clone 到对应位置并检出对应分支。返回日志文本。"""
    path = Path(tool["path"])
    if (path / ".git").is_dir():
        return f"[{tool['id']}] 仓库已存在：{path}"
    if path.exists():
        return f"[{tool['id']}] 警告：目录已存在但不是 git 仓库，跳过克隆：{path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    branch = tool.get("branch", "main")
    try:
        r = subprocess.run(
            ["git", "clone", "--branch", branch, tool["repo"], str(path)],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=UV_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"[{tool['id']}] 错误：git clone 超时：{tool['repo']}"
    if r.returncode != 0:
        return f"[{tool['id']}] 错误：git clone 失败：{r.stderr.strip()}"
    return f"[{tool['id']}] 已克隆 {tool['repo']}（分支 {branch}）到 {path}"


def update_tool(tool: dict) -> str:
    """更新单个仓库：fetch 后 git pull --ff-only。

    若工作区有未提交改动或无法 fast-forward，返回中文警告日志，绝不强行覆盖用户改动。
    若当前检出分支不是配置的主分支（如未合并的功能分支），fetch 照常执行但跳过 pull，
    避免把主分支代码拉进功能分支。
    """
    path = tool["path"]
    branch = tool.get("branch", "main")
    logs = []

    if not (Path(path) / ".git").is_dir():
        return f"[{tool['id']}] 仓库不存在，跳过更新（请先克隆）"

    # 工作区有未提交改动时不更新，保护用户改动
    r = _run_git(path, ["status", "--porcelain"])
    if r.returncode == 0 and r.stdout.strip():
        return f"[{tool['id']}] 警告：检测到未提交的本地改动，已跳过更新以保护你的修改"

    r = _run_git(path, ["fetch", "origin", branch])
    if r.returncode != 0:
        return f"[{tool['id']}] 警告：git fetch 失败（可能无网络），跳过本次更新：{r.stderr.strip()}"
    logs.append(f"[{tool['id']}] 已 fetch origin/{branch}")

    # 当前检出分支 ≠ 配置主分支时跳过 pull，防止主分支代码进入未合并的功能分支
    r = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    current = r.stdout.strip() if r.returncode == 0 else ""
    if current and current != branch:
        logs.append(f"[{tool['id']}] 当前在 {current} 分支，已跳过拉取（请先合并功能分支）")
        return "\n".join(logs)

    r = _run_git(path, ["pull", "--ff-only", "origin", branch])
    if r.returncode != 0:
        logs.append(
            f"[{tool['id']}] 警告：无法 fast-forward 更新（可能存在本地提交或分叉），"
            f"已跳过以保护你的改动：{r.stderr.strip()}"
        )
    else:
        msg = r.stdout.strip()
        logs.append(f"[{tool['id']}] {msg}")
    return "\n".join(logs)


def setup_env(tool: dict) -> str:
    """在仓库下执行 uv sync 搭建虚拟环境。uv 不可用时返回中文错误提示与安装指引。"""
    if shutil.which("uv") is None:
        return (
            f"[{tool['id']}] 错误：未找到 uv，无法自动搭建环境。\n"
            f"请先安装 uv（https://docs.astral.sh/uv/getting-started/installation/），"
            f"或在仓库目录手动执行 python -m venv .venv"
        )
    path = tool["path"]
    if not (Path(path) / ".git").is_dir():
        return f"[{tool['id']}] 仓库不存在，跳过环境搭建（请先克隆）"
    try:
        r = subprocess.run(
            ["uv", "sync"],
            cwd=path,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=UV_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"[{tool['id']}] 错误：uv sync 超时"
    if r.returncode != 0:
        return f"[{tool['id']}] 错误：uv sync 失败：{r.stderr.strip()}"
    tail = (r.stderr.strip() or r.stdout.strip()).splitlines()
    summary = tail[-1] if tail else "完成"
    return f"[{tool['id']}] 环境已就绪（uv sync：{summary}）"


def launch_tool(tool: dict) -> None:
    """启动工具。

    Windows 下若存在 <path>/启动.bat，用 os.startfile() 打开（弹出独立控制台窗口
    运行交互式工具）；否则退回 uv run python <entry> 后台启动。
    """
    path = Path(tool["path"])
    bat = path / "启动.bat"
    if sys.platform == "win32" and bat.is_file():
        os.startfile(str(bat))  # noqa: S606 —— 打开本机 bat 文件
        return
    subprocess.Popen(
        ["uv", "run", "python", tool["entry"]],
        cwd=str(path),
        start_new_session=True,
    )


# Hub 自身的主分支（自更新仅在该分支、工作区干净且可 ff 时执行）
HUB_BRANCH = "main"


def self_update() -> dict:
    """检查并更新 Hub 自身（与工具更新同等级别的保护）。

    fetch origin main 后，仅当当前检出分支是 main、工作区无未提交改动、
    可 --ff-only 时才 pull；任何不满足的情况返回中文说明日志并跳过。
    返回 {"updated": bool, "behind": int | None, "log": str}，
    updated=True 表示拉到了新版本（需重启进程后生效）。
    """
    path = str(ROOT)
    logs = []
    behind = None

    result = {"updated": False, "behind": None, "log": ""}

    def _finish():
        result["behind"] = behind
        result["log"] = "\n".join(logs)
        return result

    r = _run_git(path, ["fetch", "origin", HUB_BRANCH])
    if r.returncode != 0:
        logs.append(f"[hub] 警告：git fetch 失败（可能无网络），跳过 Hub 自更新：{r.stderr.strip()}")
        return _finish()

    rb = _run_git(path, ["rev-list", "--count", f"HEAD..origin/{HUB_BRANCH}"])
    if rb.returncode == 0:
        behind = int(rb.stdout.strip())
        logs.append(f"[hub] 已 fetch origin/{HUB_BRANCH}（落后 {behind} 个提交）")

    # 仅 main 分支允许自更新，避免把主分支代码拉进功能分支
    r = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    current = r.stdout.strip() if r.returncode == 0 else ""
    if current != HUB_BRANCH:
        logs.append(f"[hub] 当前在 {current or '未知'} 分支，跳过 Hub 自更新（仅 {HUB_BRANCH} 分支自动更新）")
        return _finish()

    # 工作区有未提交改动时不更新，保护用户改动
    r = _run_git(path, ["status", "--porcelain"])
    if r.returncode == 0 and r.stdout.strip():
        logs.append("[hub] 检测到未提交的本地改动，跳过 Hub 自更新以保护你的修改")
        return _finish()

    if behind == 0:
        logs.append("[hub] 已是最新")
        return _finish()

    r = _run_git(path, ["pull", "--ff-only", "origin", HUB_BRANCH])
    if r.returncode != 0:
        logs.append(
            f"[hub] 警告：无法 fast-forward 更新 Hub（可能存在本地提交或分叉），"
            f"已跳过以保护你的改动：{r.stderr.strip()}"
        )
        return _finish()
    result["updated"] = True
    logs.append("[hub] 已拉取到最新版本，重启 Hub 后生效")
    return _finish()


def refresh_all(on_tool_start=None, on_tool_done=None) -> list:
    """对每个工具执行 ensure_repo、update_tool、setup_env，汇总日志行返回。

    5 个工具之间用 ThreadPoolExecutor 并发执行（max_workers=5），git fetch 等
    网络操作不再串行等待，总耗时接近最慢的单个工具；单个工具内部仍按
    ensure_repo → update_tool → setup_env 顺序执行（后两步依赖前一步）。
    日志行通过锁追加，保证线程安全。

    on_tool_start(tool) / on_tool_done(tool)：可选进度回调，在各工具开始/
    完成时于工作线程中调用（调用方需自行保证线程安全）。
    """
    lines = []
    lock = threading.Lock()

    def _process(tool: dict) -> None:
        if on_tool_start is not None:
            on_tool_start(tool)
        tool_lines = []
        try:
            for step in (ensure_repo, update_tool, setup_env):
                tool_lines.extend(step(tool).splitlines())
        finally:
            if on_tool_done is not None:
                on_tool_done(tool)
        with lock:
            lines.extend(tool_lines)

    with ThreadPoolExecutor(max_workers=5) as pool:
        # pool.map 会在所有工具完成后返回，异常在此抛出
        list(pool.map(_process, load_tools()))
    return lines
