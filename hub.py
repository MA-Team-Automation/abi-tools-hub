"""ABI Tools Hub 入口。

默认启动 Web 控制台（web.py 由另一位开发者基于 core.py 接口实现）；
web 模块缺失时自动降级进入 TUI 模式，绝不崩溃。
"""

import argparse
import sys
import threading

import core

# Windows 控制台默认非 UTF-8 编码，先尝试切换，失败也不影响运行
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _refresh_in_background() -> None:
    """后台线程：自动检查更新、拉取主分支最新代码、搭建环境。"""
    print("[后台] 正在检查更新并搭建环境（git fetch 可能需要几分钟）...")
    for line in core.refresh_all():
        print(f"[后台] {line}")
    print("[后台] 更新与环境检查完成。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ABI Tools Hub - 5 个 ABI 工具的统一入口"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--web", action="store_true",
                      help="启动 Web 控制台（默认行为）")
    mode.add_argument("--tui", action="store_true",
                      help="启动终端菜单（TUI）模式")
    parser.add_argument("--no-update", action="store_true",
                        help="跳过启动时的自动更新与环境搭建")
    parser.add_argument("--port", type=int, default=8765,
                        help="Web 控制台端口（默认 8765）")
    args = parser.parse_args()

    # 除非显式跳过，否则后台自动更新 + 搭建环境（daemon 线程，不阻塞退出）
    if not args.no_update:
        threading.Thread(target=_refresh_in_background, daemon=True).start()

    # TUI 模式直接进菜单
    if args.tui:
        import tui
        tui.main()
        return

    # 默认行为：启动 Web 控制台；web.py 未就绪时降级到 TUI
    try:
        import web
    except ImportError:
        print("Web 模块尚未就绪，自动进入 TUI 模式")
        import tui
        tui.main()
        return
    web.serve(port=args.port, open_browser=True)


if __name__ == "__main__":
    main()
