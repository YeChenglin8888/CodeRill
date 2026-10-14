```
  ____            __         ____    _      _      _
 / ___   ___    __| |   ___  |  _ \  (_)    | |    | |
| |      / _ \  / _  |  / _ \ | |_) |  | |    | |    | |
| |___  | (_) | | (_| | |  __/ |  _ <  | |    | |    | |
 \____  \___/  \__,_|  \___| |_| \_\  |_|    |_|    |_|
```

> **一个小到你可以从头读到尾的编程 Agent。**

把它指向一个项目，说清你要什么，它就会自己读代码、写代码、改代码、跑命令。
在标准模式下，修改文件、执行命令或访问网络前会先征求同意。

[English](README.md) · [中文](README.zh-CN.md)

[![python](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://www.python.org/)
[![CI](https://github.com/YeChenglin8888/CodeRill/actions/workflows/ci.yml/badge.svg)](https://github.com/YeChenglin8888/CodeRill/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)

---

## 为什么还写一个

编程 Agent 不稀缺，**能读懂的**编程 Agent 才稀缺。

CodeRill 有几千行 Python 和针对性的测试。你打开 `agent.py`
就能跟完一整轮：拼请求、流式收回复、跑工具、继续循环——中间没有五层抽象跳来跳去。
每个子系统就几个文件，每个文件的名字就是它唯一负责的那件事。

这就是全部的设计主张：**小本身是特性，不是借口。**

```console
you > utils.py 有个 bug —— parse() 遇到空输入会崩

allow edit_file? {"file_path": "utils.py", ...}
--- a/utils.py
+++ b/utils.py
-    return json.loads(text)
+    return json.loads(text) if text else {}
  [y]es / [n]o / [a]lways > y

→ edit_file (file_path="utils.py")
→ bash (command="python -m pytest tests/test_utils.py")

修好了。parse() 现在遇到空输入返回 {}，不再抛异常。
```

## 它怎么跑

整个 agent 就是一个循环。每一轮它从模型那里流式收回复；如果回复里要调工具，
就执行工具、把结果喂回去、继续循环。当模型不再要工具、直接给出回答，这一轮就结束。

```
        用户消息
             |
             v
   +----------------------+
   | 压缩 + 拼装请求       |  <-- context.py, prompt.py, skills.py
   | （技能、任务计划、     |
   |   待办、系统提示词）   |
   +----------------------+
             |
             v
   +----------------------+
   |   流式接收模型回复     |  <-- llm.py（流式 + 重试 + 超时）
   +----------------------+
             |
        要调工具吗？
        /        \
      要          不要 --> 结束
       |
       v
   +-------------------------------------------------+
   |  hooks -> 同意 -> 跑工具 -> post hooks           |  <-- agent.py
   |  （只读工具并行执行）                             |
   +-------------------------------------------------+
             |
        结果追加进会话日志，继续循环
```

## 几个我会在 code review 上坚持的设计

- **文件工具的路径出不了工作区。** 路径相对项目根目录解析，越界直接拒绝——校验只写
  一处，而不是散落在每个工具里。Shell 命令尚未沙箱化。
- **同意是代码，不是提示词。** 会改东西的调用要走一条策略：分类动作，要么命中
  已保存的授权，要么问你，写文件还会先给你看 diff。"Always" 存的是**受限**授权——
  一个路径、一个命令前缀、一个主机——而不是一个无限期的"都行"。
- **会话日志只追加。** 压缩时写入一个 checkpoint，之前的 event 原地保留，所以审计
  链不会断；会话可以重放、fork、分享，都不会动到那份耐久历史。
- **工具报错是数据，不是崩溃。** 工具失败会返回一段模型能读、能自我修复的错误
  文本，而不是把整个循环掀掉。
- **只读工具并行，写操作不并行。** 能白拿的并发就拿，该讲顺序的地方讲顺序。

## 目录里有什么

| 文件 | 职责 |
| --- | --- |
| `coderill/agent.py` | 主循环：轮次、并行工具、hooks、同意、子 agent、计划协调。 |
| `coderill/llm.py` | 模型客户端：流式、工具调用重组、重试、超时、用量统计。 |
| `coderill/tools/` | 各个工具，以及分发它们的注册表。 |
| `coderill/permissions.py` | 同意机制：分类、模式、受限的持久授权。 |
| `coderill/review.py` | 文件改动前展示的可信 diff。 |
| `coderill/hooks.py` | 工具调用前后的用户 shell hook。 |
| `coderill/skills.py` | 基于文件的技能：按需发现与加载。 |
| `coderill/taskplan.py` | 任务计划，带基于版本号的并发控制。 |
| `coderill/context.py` | 三层上下文压缩。 |
| `coderill/session.py` | 只追加的 JSONL 事件日志，重放、fork、分享。 |
| `coderill/checkpoints.py` | 写文件类工具的撤销。 |
| `coderill/mcp.py` | 一个精简的 MCP stdio 客户端。 |
| `coderill/browser.py` | 可选的 Jev 浏览器任务循环。 |
| `coderill/cli.py` | 终端界面。 |
| `coderill/config.py` | 环境变量与配置。 |

## 工具箱

- **read_file** / **write_file** / **edit_file** —— 带行号读文件、写文件，以及
  替换一段精确唯一的字符串（返回 unified diff）。
- **bash** —— 从工作区根目录执行 shell 命令（Windows 上走 Git Bash）。
- **glob** / **grep** —— 按模式找文件、按正则搜内容。
- **todo_write** —— agent 的轻量清单。
- **task_list** / **task_create** / **task_update** / **task_revise** —— 真正带稳定
  ID 和版本号的任务计划。
- **load_skill** —— 把一个技能的说明拉进对话。
- **agent** —— 把一个自成一体的小任务交给全新的子 agent。
- **browser_task** —— 把整个网页任务交给 Jev 模型（可选）。

## 安装

先安装 Python、Git 和 [pipx](https://pipx.pypa.io/stable/how-to/install-pipx.html)，然后执行：

```bash
pipx install git+https://github.com/YeChenglin8888/CodeRill.git
coderill --version
```

目前从 GitHub 当前源码安装，尚未发布带版本的 PyPI 包。开始 Agent 会话前，
在环境变量或运行项目的 `.env` 文件中设置 `OPENAI_API_KEY`。

如果要从源码参与开发：

```bash
git clone https://github.com/YeChenglin8888/CodeRill
cd CodeRill
pip install -e .
cp .env.example .env      # 然后填 OPENAI_API_KEY
```

任何 OpenAI 兼容的端点都能用——给 `OPENAI_BASE_URL` 填上 DeepSeek、Moonshot、
本地的 vLLM 服务，或者你手头任何一个。

## 使用

```bash
coderill                               # 交互式会话
coderill "给 llm.py 加上类型标注"        # 一次性执行
coderill --cwd ~/myproject             # 对另一个目录运行
coderill --plan                        # 以 plan 模式启动（只读）
coderill --auto-approve                # 不再逐一询问同意
coderill --list                        # 列出已保存的会话
coderill --resume 20260101-1200        # 恢复其中一个
```

REPL 内部：`/help`、`/new`、`/list`、`/resume <id>`、`/where`、`/cost`、
`/undo`、`/share`、`/fork`、`/plan`、`/approve`、`/mode`、`/reasoning`、
`/mcp`、`/exit`。

## 技能

把 `SKILL.md`（或 `<name>.md`）放进 `<项目>/.agents/skills/`、`<项目>/skills/`、
`~/.agents/skills/` 或 `~/.coderill/skills/` 即可。模型平时只看到每个技能的名字
和一句话描述；只有它调用 `load_skill` 时，正文才会进入对话。同名的项目级技能
会覆盖全局技能。

## 会话

一次对话就是一份位于 `~/.coderill/sessions/<id>.jsonl` 的事件日志。消息列表是
从这份日志重放出来的投影，所以你可以恢复它、`/fork` 成一条分支，或者打印一份
脱敏的 `/share` 记录——三者都不会动到耐久历史本身。任务计划的更新也会入日志，
恢复和 fork 时会保留计划与版本号。

## Jev 浏览器任务（可选）

`browser_task` 把一个完整的网页任务交给 TypeSafe 的 Jev 模型：一个亚秒级、
"System 1"式的决策模型，返回的是带类型的选择而不是自然语言。CodeRill 用
Playwright 驱动真实浏览器，向 Jev 询问下一步操作，返回 JSON 形式的页面证据和
停止原因；`model_done` 是 Jev 自己的说法，不等于独立验证。

它的 DOM 读取器（`browser_snapshot.js`）改编自 `browser-use/jev-ultrafast`；
MIT 声明与参考 commit 见
[`coderill/browser_NOTICE.txt`](coderill/browser_NOTICE.txt)。安装：
`pip install -e ".[browser]"`，然后设置 `CODERILL_BROWSER_ENABLED=1` 和各
`TYPESAFE_*` 变量。

## 配置

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | 必填。 |
| `OPENAI_BASE_URL` | OpenAI | 任何兼容端点。 |
| `CODERILL_MODEL` | `gpt-4o-mini` | 模型名。 |
| `CODERILL_MAX_TOKENS` | `4096` | 单次回复上限。 |
| `CODERILL_TEMPERATURE` | `0` | 采样温度。 |
| `CODERILL_MAX_TURNS` | `30` | 每轮用户输入允许的工具轮数。 |
| `CODERILL_MAX_CONTEXT` | `128000` | 触发压缩的上下文预算。 |
| `CODERILL_BROWSER_ENABLED` | `0` | 是否注册浏览器工具。 |
| `CODERILL_BROWSER_CHANNEL` | `msedge` | `msedge` 或 `chrome`。 |
| `TYPESAFE_API_KEY` | — | Jev 浏览器模型的密钥。 |
| `TYPESAFE_MODEL` | `jev-latest` | Jev 模型名。 |

状态存放在 `~/.coderill/`：`hooks.json`、`mcp.json`、`permissions.json`、
`skills/`、`sessions/`。

## 测试

```bash
pip install -e ".[dev]"
pytest tests/ -q       # 离线：不联网、不需要 API key
ruff check .
```

GitHub Actions 会在 Linux 和 Windows 上运行这些检查。六个真实浏览器集成测试
只有显式启用时才会运行。

主循环、工具、权限、hooks、技能、任务计划、上下文、review 和会话的测试都用
fake 实现，所以整套测试不需要网络。浏览器测试跑在假浏览器和假 Jev 响应之上；
真实浏览器的检查需要显式打开 `CODERILL_BROWSER_TEST=1`。

## 接下来

- 一层 provider 抽象，外加一等公民的 Anthropic 适配器（目前通过
  `OPENAI_BASE_URL` 支持任何 OpenAI 兼容端点）。
- 沙箱化的命令执行，以及多模态附件。
- 一个 Textual TUI，作为同一套 runtime 的客户端。

## 致谢

Jev 浏览器模块改编自
[`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast)（MIT）的
DOM 读取代码——声明随 JS 一起发布，见
[`coderill/browser_NOTICE.txt`](coderill/browser_NOTICE.txt)。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
