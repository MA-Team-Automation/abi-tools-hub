"""统一 TUI 界面工具包 —— 所有姐妹工具共用同一份拷贝，保证一致的设计与操作逻辑。

统一操作逻辑约定：
1. 启动后清屏，显示双线框横幅（工具名 + 一句话描述）。
2. 主菜单编号从 1 开始，[Q] 退出。
3. 每次操作完成后「按回车返回主菜单」并清屏回到菜单。
4. 无效输入给出警告并重新提示；Ctrl+C 优雅取消当前操作。
"""
import os
import sys


def _enable_ansi():
    """Windows 终端开启 ANSI 颜色支持（Win10+），失败时静默降级为无色。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


_enable_ansi()
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
CYAN, GREEN, YELLOW, RED, BLUE = "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[34m"

WIDTH = 56  # 界面统一宽度


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def _line(ch="─"):
    print(DIM + ch * WIDTH + RESET)


def banner(title, subtitle=""):
    """统一顶部横幅：双线框 + 工具名 + 一句话描述。"""
    _line("═")
    print(BOLD + CYAN + "  " + title + RESET)
    if subtitle:
        print(DIM + "  " + subtitle + RESET)
    _line("═")


def show_menu(title, items):
    """items: [(按键, 描述)]，按键如 "1"、"Q"。"""
    print()
    print(BOLD + "  " + title + RESET)
    _line()
    for key, desc in items:
        print("  " + GREEN + "[" + key + "]" + RESET + "  " + desc)
    _line()


def ask(prompt="请选择"):
    return input(YELLOW + prompt + ": " + RESET).strip()


def pause(text="按回车返回主菜单"):
    input("\n" + DIM + text + "..." + RESET)


def info(msg):
    print(BLUE + "[i]" + RESET + " " + msg)


def ok(msg):
    print(GREEN + "[✓]" + RESET + " " + msg)


def warn(msg):
    print(YELLOW + "[!]" + RESET + " " + msg)


def error(msg):
    print(RED + "[✗]" + RESET + " " + msg)


def clean_path(raw):
    """清理拖入终端的文件路径：去除引号与 PowerShell 的 & 包装。"""
    return raw.strip().strip("'").strip('"').removeprefix("& ").strip()


def run_menu(title, subtitle, options):
    """统一操作逻辑主循环。options: [(显示文本, 无参数可调用对象)]。

    自动生成 1..n 编号并追加 [Q] 退出；每次执行完 pause 后清屏回到菜单。
    """
    while True:
        clear()
        banner(title, subtitle)
        items = [(str(i + 1), text) for i, (text, _) in enumerate(options)]
        items.append(("Q", "退出"))
        show_menu("请选择功能", items)
        choice = ask().upper()
        if choice in ("Q", "0"):
            info("再见！")
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(options)):
            warn("无效的选择，请重新输入")
            pause()
            continue
        try:
            options[int(choice) - 1][1]()
            ok("操作完成")
        except KeyboardInterrupt:
            warn("操作已取消")
        except Exception as exc:
            error("出错了：" + str(exc))
        pause()


if __name__ == "__main__":
    # 自演示：python tui_kit.py 可预览统一界面效果
    run_menu("TUI Kit 演示", "统一设计与操作逻辑", [("示例功能一", lambda: info("功能一执行完毕")), ("示例功能二", lambda: info("功能二执行完毕"))])
