"""ABI Tools Hub 统一 TUI 菜单。

纯标准库实现（input() 数字选择，不依赖第三方库），供 hub.py --tui 及
Web 模块缺失时的降级入口使用。
"""

import sys

import core

# Windows 控制台默认非 UTF-8 编码，先尝试切换，失败也不影响运行
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LINE = "=" * 56


def _format_status(tool: dict, status: dict) -> str:
    """把 check_status 结果格式化为单行状态文本。"""
    if not status["exists"]:
        return "未克隆"
    branch = status["branch"] or tool.get("branch", "?")
    if status["behind"] is None:
        remote = "远程未知"
    elif status["behind"] > 0:
        remote = f"落后 {status['behind']} 个提交"
    else:
        remote = "已是最新"
    env = "环境就绪" if status["env_ready"] else "环境未搭建"
    return f"{branch} | {remote} | {env}"


def _print_menu(tools: list, statuses: list) -> None:
    """打印工具状态列表与按键菜单。"""
    print()
    print(LINE)
    print("  ABI Tools Hub - 统一工具入口 (TUI)")
    print(LINE)
    for i, (tool, status) in enumerate(zip(tools, statuses), 1):
        print(f"  [{i}] {tool['display']}")
        print(f"      {_format_status(tool, status)}")
    print(LINE)
    print("  [1-5] 启动对应工具   [U] 更新全部代码")
    print("  [S] 搭建全部环境     [C] 检查更新状态")
    print("  [W] 启动 Web 控制台  [Q] 退出")
    print(LINE)


def _print_logs(title: str, logs) -> None:
    """打印一段操作日志。"""
    print(f"\n--- {title} ---")
    for line in logs:
        print(f"  {line}")


def _start_web() -> None:
    """尝试启动 Web 控制台；web.py 未就绪时给出降级提示，不崩溃。"""
    try:
        import web
    except ImportError:
        print("\nWeb 模块尚未就绪，敬请期待；请继续使用 TUI 模式。")
        return
    web.serve(port=8765, open_browser=True)


def main() -> None:
    """TUI 主循环。"""
    tools = core.load_tools()
    print("正在检查各工具状态（git fetch 可能需要几秒）...")
    statuses = [core.check_status(t) for t in tools]

    while True:
        _print_menu(tools, statuses)
        choice = input("请选择: ").strip().lower()

        if choice == "q":
            print("再见！")
            return
        if choice == "u":
            _print_logs("更新全部代码", [core.update_tool(t) for t in tools])
            statuses = [core.check_status(t) for t in tools]
        elif choice == "s":
            _print_logs("搭建全部环境", [core.setup_env(t) for t in tools])
            statuses = [core.check_status(t) for t in tools]
        elif choice == "c":
            print("正在重新检查状态...")
            statuses = [core.check_status(t) for t in tools]
        elif choice == "w":
            _start_web()
        elif choice in ("1", "2", "3", "4", "5"):
            idx = int(choice) - 1
            if idx >= len(tools):
                print("无效的序号。")
                continue
            tool = tools[idx]
            status = statuses[idx]
            if not status["exists"]:
                print(f"\n[{tool['id']}] 仓库尚未克隆，正在克隆...")
                print(f"  {core.ensure_repo(tool)}")
                statuses[idx] = core.check_status(tool)
                if not statuses[idx]["exists"]:
                    continue
            print(f"\n正在启动「{tool['display']}」...")
            try:
                core.launch_tool(tool)
                print("  已在独立窗口中启动。")
            except Exception as exc:
                print(f"  启动失败：{exc}")
        else:
            print("无效输入，请重新选择。")


if __name__ == "__main__":
    main()
