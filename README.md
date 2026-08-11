# ABI Tools Hub

5 个 ABI 姐妹工具的统一用户入口（Web 控制台 + TUI）。

## 环境要求

- Python 3.9 或更高版本
- [uv](https://docs.astral.sh/uv/getting-started/installation/)（用于自动搭建各工具的虚拟环境）
- git（用于自动克隆与更新各工具仓库）

## 快速开始

双击 **「启动 Hub.bat」** 即可：它会自动检查各工具更新、拉取主分支最新代码、自动搭建环境，并打开浏览器中的 Web 控制台。

- 如果某个工具的目录缺失，Hub 会自动 `git clone` 对应仓库到父目录。
- Web 控制台模块（web.py）缺失时，会自动降级进入 TUI 菜单模式，不影响使用。

## Web 控制台

```bash
python hub.py --web          # 默认行为，可省略 --web
python hub.py --port 9000    # 指定端口（默认 8765）
```

浏览器访问 `http://127.0.0.1:8765`：

- 顶部「全部更新并搭建环境」按钮：后台对所有工具执行检查更新、拉取代码、搭建环境。
- 每个工具一张卡片：显示当前分支、落后/超前远程的徽标、环境就绪状态、最后提交摘要，并提供「启动 / 更新 / 搭建环境」按钮（操作进行中按钮自动禁用）。
- 底部日志面板实时滚动显示后台操作日志。

Web API（供前端页面使用）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/status` | 5 个工具的状态（分支 / behind / ahead / 环境 / running） |
| POST | `/api/update/{id}` | 后台更新单个工具 |
| POST | `/api/setup/{id}` | 后台搭建单个工具环境 |
| POST | `/api/launch/{id}` | 启动单个工具 |
| POST | `/api/refresh-all` | 后台全量更新并搭建环境 |
| GET | `/api/logs?since=N` | 日志环形缓冲（最近 500 行，按序号增量拉取） |

## TUI 模式

```bash
python hub.py --tui
```

菜单按键：

| 按键  | 功能           |
| ----- | -------------- |
| 1 - 5 | 启动对应工具   |
| U     | 更新全部代码   |
| S     | 搭建全部环境   |
| C     | 检查更新状态   |
| W     | 启动 Web 控制台 |
| Q     | 退出           |

## 命令行参数

```bash
python hub.py              # 默认启动 Web 控制台
python hub.py --web        # 同上，显式指定
python hub.py --tui        # 终端菜单模式
python hub.py --no-update  # 跳过启动时的自动更新与环境搭建
python hub.py --port 9000  # 指定 Web 控制台端口（默认 8765）
```

## tools.json 配置项说明

`tools` 数组中每个工具包含以下字段：

- `id`：工具唯一标识（供程序内部引用）
- `name`：仓库名称
- `display`：界面上显示的中文名称
- `description`：工具功能简介
- `dir`：工具仓库目录（相对 Hub 根目录，可为绝对路径）
- `repo`：git 远程仓库地址（目录缺失时自动克隆用）
- `branch`：跟踪的主分支（fetch / pull / clone 均使用该分支）
- `entry`：工具入口脚本文件名（无「启动.bat」时通过 `uv run python <entry>` 启动）

## 目录结构

```
ABI Tools Hub/
├── hub.py          # 入口：解析参数，启动 Web 控制台或 TUI
├── core.py         # 核心：状态检查 / 克隆 / 更新 / 环境搭建 / 启动
├── web.py          # Web 控制台（纯标准库 HTTP 服务 + JSON API）
├── tui.py          # 统一 TUI 菜单
├── tui_kit.py      # 统一 TUI 界面工具包（与姐妹工具字节级一致，勿修改）
├── static/
│   └── index.html  # Web 前端单页（内嵌 CSS/JS，无框架无构建）
├── tools.json      # 5 个工具的清单配置
├── 启动 Hub.bat    # Windows 一键启动入口
└── README.md
```

## 说明

- 更新工具代码时使用 `git pull --ff-only`；若检测到未提交的本地改动或无法快进合并，会自动跳过并在日志中提示，绝不覆盖你的修改。
- 若仓库当前检出分支不是 tools.json 配置的主分支（如未合并的功能分支），更新时只 fetch 不 pull，并在日志中提示，避免把主分支代码拉进功能分支。
- Windows 下若工具目录内存在「启动.bat」，点击启动会弹出独立控制台窗口运行该交互式工具。
