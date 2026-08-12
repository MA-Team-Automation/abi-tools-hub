# ABI Tools Hub

ABI 工具集（5 个工具项目）的统一用户入口（Web 控制台 + TUI）。

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
| GET | `/api/tools` | 工具列表（展示字段 + actions 定义，供渲染操作表单） |
| POST | `/api/run/{tool_id}/{action_id}` | 启动工具操作任务，请求体 `{"params": {key: value}}`，返回 `{"job_id": "..."}`；参数校验失败返回 400 + 中文错误信息 |
| GET | `/api/jobs` | 所有任务摘要（按时间倒序，最多保留最近 50 个） |
| GET | `/api/job/{job_id}?since=N` | 任务增量日志：`{status, exit_code, lines: [{seq, text}]}`，lines 只含 `seq > N` |

## Web 工作台

5 个工具的功能已全部搬进 Web 界面：在浏览器中填写表单（文件路径等参数）、点击运行、实时查看输出，无需再打开各工具的终端菜单。

- 每个工具的 `actions`（操作列表）声明在 `tools.json` 中，前端按 `inputs` 渲染表单；
- 点击运行后 Hub 在后台线程启动子进程（工作目录为对应工具仓库），逐行采集输出；
- 前端按 `GET /api/job/{job_id}?since=N` 轮询增量日志，直到 `status` 变为 `done` / `error`；
- 少数无需子进程的检查类操作（如 MediaTools 数据导出的「检查 .env 配置」）以 `"kind": "builtin"` 声明，由 Hub 内置实现。

### tools.json actions 配置格式

每个工具可增加 `actions` 数组，元素格式：

```json
{
  "id": "split-data",
  "label": "中文操作名",
  "description": "一句话说明",
  "inputs": [
    {"key": "file", "label": "Excel 文件路径", "type": "path", "required": true, "default": "", "placeholder": "拖入或粘贴文件路径"}
  ],
  "command": ["uv", "run", "python", "Some Tool.py", "{file}", {"args": ["--sheet", "{sheet}"], "omit_if_empty": true}]
}
```

- `inputs`：表单字段。`type` 仅支持 `path`（文件路径，后端自动去引号 / 去 `& ` 前缀 / 去空白）与 `text`（普通文本/数字）；`required` 为 `true` 且值为空时启动失败并返回 400。无输入的操作（如数据导出）可省略 `inputs` 或置空数组。
- `command`：argv 数组（不使用 shell），在工具仓库目录下执行。字符串元素中的 `{key}` 由同名输入值替换；元素整体恰好是 `{key}` 且值为空时该元素被省略。
- 可选参数组：`{"args": ["--sheet", "{sheet}"], "omit_if_empty": true}` —— 组内任一 `{key}` 值为空时，整组参数省略（用于 `--sheet` 这类可选 CLI 参数）。
- 需要调用包内函数而非 CLI 时，可用 `python -X utf8 -c "<驱动代码>" <参数>` 的形式，参数通过 `sys.argv` 传入（避免 -c 内嵌路径的引号转义问题）。
- `"kind": "builtin"` 的特殊 action 不跑 subprocess，由 Hub 内置实现（在 `jobs.py` 的 `_BUILTINS` 中按 action id 注册），适合本机检查类操作。


## Hub 自更新

启动 Web 控制台（或点击主页「检查更新」）时，Hub 会先检查并更新**它自己**，再并行更新 5 个工具：

- 仅当当前检出分支是 `main`、工作区无未提交改动、可以 `--ff-only` 时才会 `git pull`；否则打印中文说明并跳过，绝不覆盖本地改动。
- 拉到新版本后，页面底部会浮起「已更新到最新版本」提示，点击「重启生效」即原地重启进程（`POST /api/restart`，有任务运行时返回 409 并拒绝重启），服务恢复后页面自动刷新。

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
- `actions`：Web 工作台的操作列表（可选；格式见上文「Web 工作台」小节）

## 目录结构

```
ABI Tools Hub/
├── hub.py          # 入口：解析参数，启动 Web 控制台或 TUI
├── core.py         # 核心：状态检查 / 克隆 / 更新 / 环境搭建 / 启动
├── web.py          # Web 控制台（纯标准库 HTTP 服务 + JSON API）
├── jobs.py         # 任务执行器（后台线程跑子进程、实时采集输出）
├── tui.py          # 统一 TUI 菜单
├── tui_kit.py      # 统一 TUI 界面工具包（与各工具项目保持字节级一致，勿修改）
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
