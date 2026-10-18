# CodeRill

CodeRill 是一个开源的 Python 编程 Agent，可以直接在终端中使用。给它一个代码任务，它会查看和搜索项目、提出修改、运行命令，再根据结果决定下一步；源码也适合用来学习编程 Agent 如何工作。

它重点处理长任务：用追加式会话日志记录执行过程，修改文件前请求审查，管理上下文长度，并在中断后恢复。CLI 和可选的 Textual TUI 共用一个异步运行时。

[English](README.md) · [架构与故障语义](docs/architecture.md) · [完整面经](docs/CodeRill项目面试完整版.md) · [FirstCoder 能力对照](docs/FirstCoder能力对照.md) · [实施状态](docs/implementation-status.md)

## pipx 一条命令安装

准备好 Python 3.10+ 和 [pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html)，一条命令从 [PyPI](https://pypi.org/project/coderill/) 安装命令行程序：

```bash
pipx install coderill
```

在环境变量中设置 `OPENAI_API_KEY`，进入想让 Agent 处理的项目目录，运行 `coderill "解释测试失败的原因"`。默认工作区是当前目录；`coderill --help` 可以查看其他参数。上述命令安装基础 CLI；TUI、MCP 的可选依赖见下文。

## 安装与使用

需要 Python 3.10 或更新版本。开发环境中运行：

```bash
pip install -e ".[tui,mcp]"
coderill                         # 交互式命令行
coderill "修复测试"                # 单次请求
coderill --tui                   # 可选的终端界面
coderill --resume SESSION_ID     # 恢复会话
```

在环境变量或项目 `.env` 中设置 `OPENAI_API_KEY`。原生 Anthropic 接口使用 `--provider anthropic` 和 `ANTHROPIC_API_KEY`；OpenAI 兼容服务可以设置 `OPENAI_BASE_URL`。`--cwd` 指定工作区，`--message` 指定单次请求，`--attach` 添加文本文件或图片。图片要求所选模型配置 `vision = true`。

配置依次来自默认值、`~/.coderill/config.toml`、项目 `.coderill.toml`、环境变量和命令行。可以定义模型配置并通过 `/model 名称` 或 TUI 切换：

```toml
[models.claude]
provider = "anthropic"
model = "claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"
vision = true
```

原有 `/new`、`/list`、`/resume`、`/fork`、`/share`、`/cost`、`/plan`、`/approve`、`/mode`、`/undo` 等命令继续可用；新增 `/attach`、`/model`、`/background` 和 `/apply`。

## 核心分层

| 模块 | 职责 |
| --- | --- |
| `coderill/app/` | CLI、TUI、命令与依赖装配 |
| `coderill/agent/runtime.py` | 异步回合循环、审批暂停与恢复、工具结果配对 |
| `coderill/providers/` | 厂商中立协议及 OpenAI、Anthropic 适配器 |
| `coderill/session/` | 版本化 JSONL 事件、回放、锁与附件 |
| `coderill/context/` | 请求预算、旧输出归档、压缩与摘要 |
| `coderill/permissions/` | 权限范围与持久化写前审查 |
| `coderill/runtime/` | 进程取消与后台任务状态 |
| `coderill/tools/`、`coderill/mcp.py` | 内置工具与 MCP SDK 集成 |

审批记录包含请求编号、原始参数、差异与文件摘要。如果审批期间文件发生变化，旧审批失效，需要查看新差异。进程退出时未完成的外部操作标记为“结果未知”，不会自动重放。旧 v1 会话仍能读取，新事件使用 v2。Python 调用方使用异步 `AgentRuntime`；旧同步 `Agent` 接口已移除。

连续只读工具可并行，写操作按顺序执行。后台子 Agent 有独立会话和预算；可写子任务使用 Git worktree，必须通过 `/apply 子任务ID` 预览并应用改动。Git worktree 只隔离工作目录，不是操作系统安全沙箱。

内置工具还支持批量读取、Git 状态／差异／历史、诊断命令、多文件补丁、删除、公开网页读取与搜索。补丁和删除在 aggressive 模式下仍须审查；网络工具拒绝本机与内网地址，也不跟随跳转。

可选 MCP 通过官方 SDK 连接 stdio 和 Streamable HTTP 服务，配置文件为 `~/.coderill/mcp.json`。原有 Jev 浏览器能力仍可选用；其 DOM 读取代码的来源与许可记录见 [browser_NOTICE.txt](coderill/browser_NOTICE.txt)。

## 离线验证

```bash
pip install -e ".[dev,tui,mcp]"
pytest tests/ -q
ruff check .
python examples/failure_demo.py
python -m pip wheel . --no-deps --wheel-dir dist
```

故障演示无需 API key，展示审批过期、重新审查和崩溃后的未知结果。CI 在 Linux 和 Windows 运行测试、Ruff、CLI 检查及打包。真实模型、浏览器和 MCP 服务仍需单独启用集成测试。

## 许可证

MIT。Jev 浏览器模块的第三方来源说明保留在 `coderill/browser_NOTICE.txt`。
