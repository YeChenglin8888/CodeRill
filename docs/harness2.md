# CodeRill 的 Harness² 升级

参考：[Google Research Harness² 论文](https://arxiv.org/abs/2610.14224)、[官方实现](https://github.com/google-research/harness2)；比较对象：[RRSI](https://arxiv.org/abs/2609.24972)。Harness² 面向每个新任务，通过多次独立执行和轨迹比较来调整模型外围的工作方法；RRSI 更适合有固定题集和验证分数的持续演化。CodeRill 面对开放编码任务，所以采用 Harness²。论文中的效果不等于本项目的实测收益。

## 已接入的流程

每次运行从同一源码快照和共同基础 Harness 出发，执行 `k` 次基线、`2k` 次候选和 `k` 次组合，总计 `4k` 次任务执行。`seq` 模式把本轮组合结果作为下一轮起点；`agg` 模式从同一基线独立提出 `2k` 个候选，再生成 `k` 个组合。候选编辑无效时继承当前 Harness；组合必须提供能在当前及候选执行记录中逐字找到的证据，否则回退。`k > 1` 时用打乱顺序的匿名候选 ID 展示最终产物摘要供选择。最终选中的 Harness 与有证据支持的经验可存入按项目和领域隔离的 Improvement Harness 库；下个任务从共同基础重新开始，可从多条经验中复用不同组件。

八类可编辑组件在 CodeRill 中的实际作用：

| 组件 | 运行时效果 |
| --- | --- |
| `system_prompt` | 向当前任务的系统提示追加指导 |
| `guardrails` | 向当前任务追加约束指导 |
| `memory` | 当前任务的短期事实提示 |
| `tool_guidance` | 当前任务的工具使用指导 |
| `skills` | 生成可由 `load_skill` 读取的技能文件 |
| `scripts` | 在隔离工作区生成 Python 脚本，供 Docker 命令工具调用 |
| `subagents` | 注册 `harness_agent` 工具，让指定角色的只读子代理执行调查 |
| `middleware` | 通过声明式插件拦截匹配的工具调用，或向匹配的工具结果追加提示 |

候选编辑采用“未提及即继承”的组件补丁；名称、大小和脚本语法在执行前验证。每个候选在 `output/harnesses/<候选名>/` 保存完整文件树：`systemprompt.md`、`guardrails.md`、`LongTermMEMORY.md`、`tool_descriptions/tool_guidance.md`、`skills/`、`scripts/`、`sub_agents/` 和 `plugin/tool_loop.json`。中间件只执行经验证的声明式规则，不执行模型生成的宿主机钩子代码。基线、候选和最终运行各有独立工作区与会话；文件命令禁网并交给 Docker。源码工作区不会被自动改动。

经验库中的每条采纳记录保存完整 Harness 文件树和 `EXPERIENCE.md`；同领域 `INDEX.md` 列出经验线索。`result.json` 保存每步组件文件差异、轨迹、产物差异、选择、模型用量和耗时，方便审计及回退。

## Jev 决策层

Jev 的 [官方 System One API](https://api.typesafe.ai/docs) 接收状态和固定候选，返回结构化选择、概率与置信度。因此它只参与**选择**，不负责生成 Harness 补丁或经验文本：

- `coderill-refine --decision-model jev`：有经验库时先按任务文本匹配和新近程度筛出至多七条经验，交给 Jev 选择一条，再由生成模型选择要复用的组件；`k > 1` 时还用 Jev 在匿名最终交付摘要之间选择。
- 普通 CodeRill 会话：安装 `.[jev]`、配置 `TYPESAFE_API_KEY` 和 `CODERILL_JEV_DECISIONS=1` 后启用 `jev_decide` 工具。模型可在测试方案、实现候选等固定选项间请求 Jev 决策。工具调用遵循现有权限审核。原浏览器插件继续可用。

Jev 无法验证代码是否正确；API 错误或格式不合法会显式失败，不把失效响应当作有效选择。发送给 Jev 的任务状态和候选内容会离开本机，使用者应只提供适合发送到该服务的证据。

## 使用

先按 [Docker 沙箱说明](../README.md#optional-docker-sandbox-source-checkout)准备镜像，配置 CodeRill 原有的生成模型密钥。需要 Jev 时额外安装 `.[jev]` 并设置 `TYPESAFE_API_KEY`。源码可直接用模块入口，安装后也有 `coderill-refine` 命令。

```powershell
$run = Join-Path $env:TEMP ("coderill-harness2-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$library = Join-Path $env:TEMP 'coderill-harness2-experience'
python -m coderill.harness2 C:\path\to\project '修复分页边界错误并运行测试' --output $run --library $library --domain code --mode seq --k 2 --decision-model jev
```

`--mode seq|agg` 默认 `seq`，`--k` 为 1–8，默认 1；`--decision-model editor|jev` 默认 `editor`；`--domain` 默认 `general`。`--output` 必须是原工作区外尚不存在的目录；`--library` 也必须在工作区外。省略经验库时不跨任务保存。旧 `.jsonl` 经验库路径仍可读取。检查 `result.json`、其中 `workspace` 指向的最终文件、`harnesses/` 和 `sessions/` 中的执行记录，再决定是否把改动应用到源码。

## 边界和验证

同任务的执行轨迹与文件差异可以支撑行为比较，不能替代测试或人工审查。多次执行会增加模型调用与耗时，故该流程使用显式入口，不会让日常 CLI/TUI/Web 会话自动执行 `4k` 次。当前运行复制源码时跳过 `.git`、虚拟环境、`node_modules` 和 `.env`；依赖 Git 历史、网络或这些文件的任务不适用这个入口。脚本仅做 Python 语法验证，最终任务代码仍需按项目测试。经验库是提示参考，不是可信事实库。

离线测试覆盖八组件文件树、工具钩子实际生效、两种 `4k` 调度、证据引用、匿名选择、选择性经验复用和 Jev 响应校验。CodeRill 采用自己的运行时与声明式插件格式，未直接运行官方 OpenCode/Codex 插件代码。真实 Docker + 模型端到端运行、Windows Unicode 路径和与论文相同任务集的效果对比仍需环境验证。
