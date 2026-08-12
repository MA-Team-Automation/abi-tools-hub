"""ABI Tools Hub 统一 TUI 菜单。

基于 tui_kit 的统一设计原语（横幅 / 菜单 / 配色 / 提示）渲染，
与各工具项目保持一致的设计风格；供 hub.py --tui 及
Web 模块缺失时的降级入口使用。
"""

import core
import tui_kit

MENU_TITLE = "ABI Tools Hub"
MENU_SUBTITLE = "5 个 ABI 工具的统一入口"


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


def _show_statuses(tools: list, statuses: list) -> None:
    """打印各工具状态列表。"""
    print()
    for i, (tool, status) in enumerate(zip(tools, statuses), 1):
        print(f"  {tui_kit.BOLD}[{i}] {tool['display']}{tui_kit.RESET}")
        print(f"      {tui_kit.DIM}{_format_status(tool, status)}{tui_kit.RESET}")


def _menu_items(tools: list) -> list:
    """组装按键菜单：1-5 启动对应工具 + U/S/C/W/Q 操作。"""
    items = [(str(i), f"启动「{tool['display']}」") for i, tool in enumerate(tools, 1)]
    items += [
        ("U", "更新全部代码"),
        ("S", "搭建全部环境"),
        ("C", "检查更新状态"),
        ("W", "启动 Web 控制台"),
        ("Q", "退出"),
    ]
    return items


def _print_logs(title: str, logs) -> None:
    """打印一段操作日志。"""
    tui_kit.info(title)
    for entry in logs:
        for line in str(entry).splitlines():
            print(f"  {line}")


def _start_web() -> None:
    """尝试启动 Web 控制台；web.py 未就绪时给出降级提示，不崩溃。"""
    try:
        import web
    except ImportError:
        tui_kit.warn("Web 模块尚未就绪，请继续使用 TUI 模式")
        return
    web.serve(port=8765, open_browser=True)


def main() -> None:
    """TUI 主循环。"""
    tools = core.load_tools()
    tui_kit.clear()
    tui_kit.banner(MENU_TITLE, MENU_SUBTITLE)
    tui_kit.info("正在检查各工具状态（git fetch 可能需要几秒）...")
    statuses = [core.check_status(t) for t in tools]

    while True:
        tui_kit.clear()
        tui_kit.banner(MENU_TITLE, MENU_SUBTITLE)
        _show_statuses(tools, statuses)
        tui_kit.show_menu("请选择操作", _menu_items(tools))
        choice = tui_kit.ask().lower()

        if choice == "q":
            tui_kit.info("再见！")
            return
        if choice == "u":
            _print_logs("更新全部代码", [core.update_tool(t) for t in tools])
            statuses = [core.check_status(t) for t in tools]
            tui_kit.ok("更新完成")
        elif choice == "s":
            _print_logs("搭建全部环境", [core.setup_env(t) for t in tools])
            statuses = [core.check_status(t) for t in tools]
            tui_kit.ok("环境搭建完成")
        elif choice == "c":
            tui_kit.info("正在重新检查状态...")
            statuses = [core.check_status(t) for t in tools]
            tui_kit.ok("状态已刷新")
        elif choice == "w":
            _start_web()
        elif choice in ("1", "2", "3", "4", "5"):
            idx = int(choice) - 1
            if idx >= len(tools):
                tui_kit.warn("无效的序号")
                tui_kit.pause()
                continue
            tool = tools[idx]
            status = statuses[idx]
            if not status["exists"]:
                tui_kit.info(f"[{tool['id']}] 仓库尚未克隆，正在克隆...")
                print(f"  {core.ensure_repo(tool)}")
                statuses[idx] = core.check_status(tool)
                if not statuses[idx]["exists"]:
                    tui_kit.pause()
                    continue
            tui_kit.info(f"正在启动「{tool['display']}」...")
            try:
                core.launch_tool(tool)
                tui_kit.ok("已在独立窗口中启动")
            except Exception as exc:
                tui_kit.error(f"启动失败：{exc}")
        else:
            tui_kit.warn("无效输入，请重新选择")
        tui_kit.pause()


if __name__ == "__main__":
    main()
