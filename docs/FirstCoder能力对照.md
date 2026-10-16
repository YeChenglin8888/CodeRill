# FirstCoder 能力对照与 CodeRill 的讲法

这份对照按用户提供的《FirstCoder 面试题单》组织。它是功能边界核对表，不是把 FirstCoder 的设计、评测数字或代码归为 CodeRill 的成果。面试中先讲 CodeRill 实际代码，再说两者取舍。

| 题单范围 | CodeRill 当前落点 | 面试中应说明的边界 |
| --- | --- | --- |
| 01–05 分层与请求链 | `app/` 装配 CLI/TUI，`agent/runtime.py` 是唯一执行接口；Provider、Session、Context、Tools、Permissions 分层 | 仓库内旧同步调用已迁移；直接使用旧 Python API 的外部调用方需要迁移 |
| 06–10 循环、预算、任务边界 | 异步 `run_turn`/`resume_turn`/`cancel_turn`；隐藏分类记录候选和稳定任务 hash；请求、工具轮数、时间预算 | 分类没有真实对话集的准确率数据；超时不能强制抢占所有同步工具 |
| 11–15 工具与执行 | 统一工具 schema 和结果配对；Git、批量读取、诊断、补丁、删除及网络工具；后台子任务和长期进程分别管理 | SDK schema 不等于完整 JSON Schema 运行时校验；获批 Shell 的副作用无法完整预览 |
| 16–20 审批与权限 | 程序审查路径和授权、生成写前 diff、持久化待审批请求，批准时核对文件摘要 | 摘要检查到实际写入之间仍有竞争窗口；Git worktree 不是 OS 沙箱 |
| 21–30 长上下文 | 完整事件与模型视图分离；L1–L4 管线按搜索、diff、构建、JSON、代码、HTML 等类型压缩旧输出，归档原文并做摘要 checkpoint | 分类压缩有离线测试，尚无真实任务集的质量基准；外部人工改文件不会自动生成 stale-read 提示 |
| 31–35 会话 | v1/v2 JSONL 回放、尾行损坏处理、fork 附件与归档、share 脱敏、未完成工具标 unknown | fork 复制当前投影和资产，不复制原日志每条事件或 Git 工作区；外部副作用没有 exactly-once 保证 |
| 36–40 Provider 与附件 | OpenAI compatible、Anthropic 适配；统一流事件和错误；文本/图片附件按摘要保存 | 两种真实模型的工具质量和视觉兼容性仍需现场调用验证 |
| 41–45 MCP 与 Skill | 官方 MCP SDK 传输；工具搜索后按需暴露 schema；Skill 目录与会话快照 | 真实 MCP 服务负载和断连恢复未做集成运行 |
| 46–50 计划与委托 | revision、依赖环、ready；独立子会话与工作树；后台完成通知、关联任务对账和一次最终检查 | 完成通知不能证明任务质量；迟到的结果仍需检查源码、测试和 owner 变化 |
| 51–55 终端交互 | CLI、可选 Textual TUI 共用 Runner 与模型切换；TUI 支持多行输入、会话与模型选择、剪贴板图片和文件附件 | TUI 自动化主要用假运行时；终端类型和多平台人工体验需实测 |
| 56–60 评测 | 离线测试与故障演示；固定清单评测、Harbor 预检、单题入口和分类报告 | 尚无真实 CodeRill Harbor 奖励或公开任务成功率；FirstCoder 的数字不能移植 |

从零讲项目的顺序见 `CodeRill项目面试完整版.md` 第十三章；评测口径见 `evaluation.md`。复核当前代码行数和验证范围，请运行 `scripts/code_stats.py` 并阅读 `implementation-status.md`。随着实现或实测变化，这张表和面经应同步更新。
