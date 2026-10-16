# CodeRill 项目面试备考

分层架构与长任务可靠性口语问答完整版　2026 年 10 月

这份材料以当前 CodeRill 源码为准，帮助我从一次请求讲清每层为什么存在、交给下一层什么、失败后怎样恢复。回答中的“已实现”指仓库里有对应代码；“已验证”主要指离线测试。真实模型、真实 MCP 服务和浏览器没有在本次交付中完成现场验证，面试时不能把离线测试说成线上成功率。

答题时按“问题 → 为什么单独分层 → 输入与输出 → 正常流程 → 失败恢复 → 代码和测试证据”组织，不背功能清单。参考 FirstCoder 的高频追问，CodeRill 已独立实现任务边界观察、按内容类型保留旧输出线索、读取失效提示和 MCP 工具搜索；实现深度与 FirstCoder 不完全相同。FirstCoder 的 Harbor 成绩不能移植成 CodeRill 成绩，个人贡献也应按真实完成的部分陈述。

## 一 项目介绍与总体设计

### 01 面试官：请用两分钟介绍 CodeRill。

我：CodeRill 是我用 Python 做的 Coding Agent。用户在 CLI 或 TUI 提出任务后，它可以读取代码、搜索、修改文件、运行命令，并在一轮里继续根据工具结果推进。我的主线不是堆工具，而是解决长任务的可靠性：上下文会满、写操作需要人批准、工具会失败，进程也可能在执行中退出。

架构上，我把入口和界面、Agent 回合循环、模型适配、上下文构造、会话事件、权限审查、工具与进程、后台子任务分开。模型负责提出下一步，程序负责参数校验、权限、预算、记录与停止条件。会话用版本化 JSONL 记录事实；重启后从事件重建状态。写文件时保存差异和文件摘要，审批前再次核对；如果外部操作开始了却没有结果事件，恢复时标成“结果未知”，不会盲目重放。

目前 CLI 和 TUI 共用异步 AgentRuntime。离线测试覆盖主要状态转换；真实模型和外部服务仍需另做集成验证。这个项目最值得讲的是边界和故障语义，而不是代码行数。

追问：压成三十秒呢？

我：CodeRill 是一个可恢复的编程 Agent。我把模型调用、上下文、事件会话、权限和工具执行拆成独立层，让写操作能暂停审批、文件变化后重新审查，长对话能压缩并找回原文，崩溃后能识别结果未知的工具。CLI 和 TUI 使用同一运行时，核心行为有离线测试和故障演示。

### 02 面试官：为什么需要 Agent，直接调用模型回答不行吗？

我：解释一段代码时，一次模型调用可能够用；修复任务往往要先搜索、读文件、提出修改、执行测试，再根据失败结果调整。下一步取决于刚拿到的工具结果，所以需要一个“模型请求—工具执行—结果回填”的循环。这个循环并不等于让模型控制一切。模型只能请求已注册工具；真正的路径检查、审批、超时和预算由程序决定。

追问：为什么不把工作流每一步写死？

我：固定步骤适合已知流程，像“运行指定测试”就不需要 Agent。开放的代码库任务有不同入口和错误形态，工具选择需要根据证据改变。我保留确定性的执行边界，把不确定的探索交给模型。

### 03 面试官：你具体负责了什么，和调用现成 SDK 有什么区别？

我：模型 SDK 只解决某个厂商的请求与流事件。CodeRill 需要把模型、工具、审批、存储和恢复接成一个闭环。我实现了统一 ChatRequest/ChatResponse/ChatStreamEvent，AgentRuntime 的状态循环，SessionStore 与事件投影，写前审查、上下文归档和子任务边界。面试展示时我会从入口文件一路走到工具结果，再拿故障演示说明为何这些边界必要。

追问：你的原创点是“事件溯源”这个概念吗？

我：概念不是我发明的。我的工作是把它落到这个 Agent 的具体失败场景：待审批请求怎样跨重启保存、工具开始但结果未落盘怎样表示、压缩后原始输出怎样找回，以及会话日志损坏时怎样明确报错。

### 04 面试官：你会怎样画出总体架构？

我：从上到下画八个框：CLI/TUI → app 装配 → AgentRuntime → ContextBuilder 与 ChatProvider → ToolRegistry 与 ReviewManager → Process/MCP/子任务运行时 → Session → JSONL 和附件。箭头表达调用方向，不能把存储或工具画成反向依赖 UI。session、providers、permissions、tools 等核心包有导入方向测试，导入核心模块也不会主动启动网络、进程或界面。

追问：既然有这么多层，是不是过度设计？

我：每层都对应一个独立变化或故障边界。换模型不应改权限；换界面不应改回合语义；压缩上下文不应删掉审计日志；工具失败不应直接破坏会话。抽象只保留这些真实边界。Python、CLI 和 TUI 统一使用异步 AgentRuntime；旧同步 Agent 已移除。

源码落点：coderill/app/cli.py；coderill/app/state.py；coderill/agent/runtime.py；coderill/providers/types.py；tests/test_architecture.py。

## 二 每一层为什么存在

### 05 面试官：app 层为什么要独立，CLI 直接创建所有对象不行吗？

我：CLI 和 TUI 是交互方式，核心状态转换应当一致。app 层负责解析配置、选择或恢复会话、装配 Provider、工具、权限和计划，再把运行事件显示出来。CLI 的一次请求和 TUI 的按钮最终都调用 AgentRuntime 的 run_turn 或 resume_turn；界面只提交审批编号和选择，不自己执行文件工具。

追问：TUI 关闭再打开，待审批卡片还在吗？

我：审批请求已写进会话事件。TUI 加载会话时读取 pending_review 并显示卡片；审批仍由运行时检查当前请求 ID 和文件摘要。TUI 的显示不是持久事实。

### 06 面试官：AgentRuntime 层具体管什么？

我：它是单轮任务的状态机。入口是 run_turn、resume_turn、cancel_turn；内部负责记录用户消息、执行隐藏任务边界分类、准备模型请求、接流事件、保存 assistant 消息、找出尚未完成的 tool call、调用工具，再把 tool message 回填给模型。它还处理模型请求数、工具轮次和运行时长预算，以及超时、取消和异常终态。返回结果明确区分 complete、waiting、cancelled、failed、budget。

追问：为什么不用递归地“模型调用自己”？

我：一个异步主循环更容易看清何时记录、何时审批、何时停止。每次循环前都检查未结算工具和请求预算；工具失败作为结果写回，模型可以继续，但不会绕过状态控制。

### 07 面试官：Provider 层为什么单独定义消息协议？

我：OpenAI 兼容接口与 Anthropic Messages 的消息结构、工具调用和流事件不同。如果把 SDK 对象传进 Agent，切换厂商会把分支散落在循环和 UI。Provider 层把它们转换为同一组 ChatMessage、ToolCall、ChatResponse、TokenUsage 和错误类别；SDK 对象留在适配器里。Agent 只看“文本增量、工具调用、完成、限流或超时”。

追问：两个 Provider 算完全兼容吗？

我：共享的是 CodeRill 自己的契约，不保证厂商行为完全一样。两个适配器有同一组假客户端契约测试，包含工具参数拼接和用量；真实 API、具体模型的视觉能力和兼容服务差异仍要实测。配置的 vision 标志也不是模型能力的自动探测。

### 08 面试官：Context 层和 Session 层为什么分开？

我：Session 保存发生过什么，Context 决定这次给模型看什么。压缩是请求视图变化，不是历史事实变化。Context 估算系统提示、工具声明、消息、附件和输出预留后，依次裁剪旧工具输出、归档原文、裁剪较早内容，必要时写摘要 checkpoint。原始事件仍在日志里，归档可以按 ID 读取。

追问：摘要失败是不是任务就停了？

我：代码会退回到从已有消息构造的确定性索引；如果压缩后仍超预算或没有收益，就返回预算错误，不无限压缩。当前 token 数是字符与附件大小的估算值，不是厂商 tokenizer 的精确计数。

### 09 面试官：Session 为什么采用事件日志，而不是只保存一份 messages.json？

我：只保存最后状态，无法区分“从未执行”和“开始执行但进程退出”，也难以追查审批是谁在什么文件版本上做出的。CodeRill 将 start、message、tool_started、tool_finished、review_requested、review_resolved、checkpoint、plan 等作为顺序事件写入 JSONL。project_events 纯函数重建运行状态，Session 提供会话操作，SessionStore 管文件写入与读取。

追问：事件日志就保证副作用恰好执行一次吗？

我：不能。写文件或外部命令可能已经产生效果，但进程来不及写完成事件。此时恢复状态只能是 unknown，先核对实际状态，再决定是否重试。日志保证的是“系统知道什么”的可回放，不是外部世界的事务。

### 10 面试官：Permission 和 Tool 为什么不能合成一层？

我：Tool 知道怎样做，Permission 决定现在可不可以做。工具注册表提供 schema 和 dispatch；ReviewManager 根据工具类型、工作区、授权记录决定是否暂停。文件写入需要给用户看到 diff、目标路径和摘要。这样新增一种模型或界面时，不会复制一套审批逻辑。文件工具在执行时仍会检查路径是否落在工作区。

追问：模型自己在 prompt 里说“我批准了”有用吗？

我：没有。运行时只接受应用侧提交的当前 review ID 和明确选择，并会复核目标文件。模型输出和工具文本都是输入数据，不能代替权限判定。

### 11 面试官：共享 runtime 层解决什么？

我：它放与界面和模型厂商无关的执行机制，例如 ProcessManager 的单次命令超时、取消、输出上限，ServiceProcessManager 的长期进程与日志，以及 BackgroundTaskManager 的任务状态和完成通知。Agent 负责决定何时调用，工具通过这些能力执行。单次 Shell 超时后终止进程组；后台协程在原进程退出后不会凭空继续，恢复时显示 interrupted。长期进程的日志可恢复，但重启后的 PID 不自动用于停止，避免误杀复用 PID 的进程。

追问：Git worktree 和 ProcessManager 是沙箱吗？

我：不是。路径检查约束文件工具，worktree 隔离 Git 工作目录，进程管理负责超时和取消；它们不限制 Shell 对操作系统的全部访问。因此不能对面试官说“已经实现安全沙箱”。

### 12 面试官：planning、skills、MCP 为什么是独立能力？

我：任务计划保存任务 ID、状态、依赖与 revision，防止并发更新丢失；Skills 只把名称和描述放进初始上下文，模型调用 load_skill 才读取全文，首次驱动时会把版本快照放进会话资产；MCP 管外部服务连接和工具发现，模型先用 mcp_tool_search 找能力，命中后本轮才暴露对应 schema。它们通过工具注册或上下文进入 Agent，不需要反向控制主循环。MCP 使用官方 SDK 的 stdio 与 Streamable HTTP 传输，但真实服务连接未在这轮离线验证中运行。

追问：这些都叫 Agent 吗？

我：不是。计划是状态数据，Skill 是工作流说明，MCP 是外部工具协议。真正循环决策的是 AgentRuntime；子 Agent 另有独立会话和预算。

源码落点：coderill/app/factory.py；coderill/agent/runtime.py；coderill/context/builder.py；coderill/session/store.py；coderill/session/projection.py；coderill/permissions/review.py；coderill/runtime/process.py；coderill/taskplan.py；coderill/skills.py；coderill/mcp.py。

## 三 一次请求从入口到结束

### 13 面试官：输入 coderill 后，代码从哪里开始？

我：安装脚本和 python -m coderill 最终进入 coderill/cli.py，再转到 coderill/app/cli.py。它解析 --cwd、--provider、--message、--tui、--resume 等参数；Config.from_env 按默认值、全局文件、项目文件、环境变量、CLI 覆盖合成配置；然后创建或加载 Session。build_state 装配权限、Provider、工具、任务计划和 AgentRuntime。TUI 与 REPL 只是不同的事件展示和用户输入方式。

追问：恢复旧会话时会新建一段历史吗？

我：Session.load 从原有 JSONL 逐条读取，再由 project_events 重建状态；不会覆盖原日志。旧 v1 事件可读，新写入事件用 v2。

### 14 面试官：模型请求是怎样构造出来的？

我：run_turn 先取得会话驱动锁、记录 turn_started 和用户消息，必要时执行隐藏任务边界判断。_model_step 组合系统提示、Skill 索引和当前计划，取本轮可见工具的 schema，再让 AsyncContextManager 生成预算内的消息视图。最终形成 ChatRequest，交给所选 Provider 的 stream。模型响应的 SDK 形态在适配器中变成统一流事件。

追问：为什么工具 schema 也算上下文？

我：它们随请求发送，会占模型输入预算。只按聊天消息长度估算，工具很多时就会低估请求大小。当前估算仍不是精确 tokenizer，遇到厂商拒绝过长请求时，运行时会强制再压缩并重试一次。

### 15 面试官：模型决定调用两个工具后，执行顺序怎样保证？

我：Agent 先持久化 assistant 的工具调用，再检查每个调用的名称和参数。连续只读调用组成一个批次并行执行；遇到写操作先等前面的读结束，再走 hook、权限审查和执行；写操作后面的读要等写完成。每个调用最后都有 tool_finished 事件和一条带 tool_call_id 的工具消息，下一次模型请求才能看到完整配对。

追问：只读工具就永远不会失败吗？

我：会失败。文件不存在、路径越界、参数错误都会记录 failed 结果并回填给模型。只读表示不需要写前审批，不表示无需校验或没有异常。

### 16 面试官：模型什么时候停止？

我：如果回答没有工具调用，Agent 将 assistant 消息记录后返回 complete；如果要求工具，就执行并继续下一次模型请求。单轮请求数达到 max_turns 返回 budget，模型流超时或 Provider 错误返回 failed，用户取消返回 cancelled，等待审批返回 waiting。模型因长度限制结束时，运行时会发继续请求，但仍受请求预算约束。

追问：工具失败是否直接终止整轮？

我：通常不。它作为 failed 的工具结果进入消息，模型可以据此调整；真正无法继续的模型错误、上下文预算耗尽或持久化故障，才会使回合退出。

### 17 面试官：为什么要分别记录 tool_started、tool_finished 和 tool message？

我：tool_started 与 tool_finished 是恢复执行状态的事件；tool message 是给模型看的对话协议。两者服务不同需求。崩溃若只写了 started，回放能看出“结果未知”；如果只留下工具消息却没有对应 assistant tool call，下一次模型请求协议可能不合法。运行时会修复已完成事件缺少 tool message 的窗口，也会对中断中的调用写出 unknown 结果。

追问：这算严格的 exactly-once 吗？

我：不是。事件去重和消息配对只是 CodeRill 内部的一致性处理，不意味着外部命令没有执行两次的风险。对不可安全重放的操作，恢复时先检查实际世界。

源码落点：coderill/__main__.py；coderill/cli.py；coderill/app/cli.py；coderill/app/state.py；coderill/agent/runtime.py；coderill/providers/types.py；tests/test_runtime.py。

## 四 审批 上下文与崩溃恢复

### 18 面试官：用户批准写文件以前，CodeRill 具体保存什么？

我：ReviewManager 生成当前请求 ID，保存原始工具名与参数、工作区、目标文件路径、文件 SHA-256 摘要和写前 diff；Session 写入 review_requested，回合返回 waiting。界面可以退出，重启后仍能显示同一个待审请求。用户批准时 resume_turn 先校验 ID 和选择，再重新解析路径与计算摘要。

追问：文件在等待期间被人工改了呢？

我：旧摘要不匹配，旧请求会以 stale 解决，并生成新的 ID 和基于新文件内容的 diff。用户必须再次看新审查。离线演示就是“提出修改—人工改文件—旧审批失效—新审批通过”的完整过程。

### 19 面试官：只用“允许这次工具”开关不够吗？

我：审批对象必须具体到这次参数和目标状态。文件写入的风险取决于覆盖哪个文件、覆盖什么内容；Shell 命令也需要匹配实际命令而不是笼统的工具名。CodeRill 的持久授权绑定工作区和具体文件目标或完整 Shell 命令。一次性 approve 不保存授权，always 才写入范围受限的 grant。

追问：这样能彻底消除 TOCTOU 吗？

我：不能。复核摘要与真正写入之间还有极小的竞争窗口，当前没有文件系统事务或原子 compare-and-swap。面试时应把它作为已知边界，而不是宣称审批与文件修改完全原子。

### 20 面试官：进程执行写操作时崩溃，恢复怎么处理？

我：Session.load 投影出有 tool_started、没有 tool_finished 的调用。AgentRuntime 在继续模型请求前把它结算为 unknown，并给模型一条“先检查实际状态”的结果。尤其是写文件、Shell 和 MCP 等可能有副作用的工具，不能直接重放旧参数。完成结果和日志去重只约束内部记录，不会魔法般保证外部副作用恰好一次。

追问：如果文件明明没有变，能自动重放吗？

我：当前实现没有自动重放这类旧调用。可以根据检查结果发起新的执行决定；对 Shell 或远端 MCP，单靠本地文件也不能判断副作用，所以保守处理是必要的。

### 21 面试官：会话日志最后一行写了一半，和中间一行损坏，有何不同？

我：最后一行没有换行且 JSON 不完整，通常可视为写入中断，读取时忽略，下一次追加前把残片备份并截掉。中间行损坏或序号断裂意味着后续事实的顺序已经不可信，SessionStore 明确报错，不静默跳过。每次完整追加会 flush 和 fsync，提高已返回事件的持久性。

追问：为什么不直接修复中间坏行？

我：不知道缺失事件的真实内容。跳过以后继续执行，可能漏掉授权、工具结果或计划更新，风险比停下来排查更大。

### 22 面试官：上下文满了，具体按什么顺序压缩？

我：先估算系统提示、工具声明、消息、附件和输出预留。超预算时，优先处理较旧且很长的工具输出，把原文按摘要存入会话附件区，模型消息留前缀和 archive ID；仍过大则处理较早的长用户或助手内容；再将更早的完整消息归档，并用索引和模型摘要建立 checkpoint。最近的一段对话保持完整。压缩只改变模型请求视图，原始日志仍可回放。

追问：旧内容被压缩后怎样重新找到？

我：checkpoint 或截断提示保留 archive ID，模型可以调用只读的 archive_read 分页取回原文。这是按 ID 取回，不是语义向量搜索；没有 ID 时不能声称系统会自动检索所有旧消息。

### 23 面试官：摘要模型自己也会失败，怎么办？

我：_summarize 捕获失败，回退到确定性的消息索引；如果最终预算仍超限或压缩后没有减小，就抛 ContextBudgetExceeded 并结束本轮。这比无限重试或把工具调用关系截成一半安全。预算基于启发式字符估算，因此我还保留了模型返回“提示过长”时的一次强制压缩路径。

追问：是否可以保证保留了所有关键事实？

我：不能保证摘要完整。设计重点是保留原始事件和归档入口，让需要的证据可核验；测试覆盖消息配对和原文可取回，不等于证明所有真实任务的摘要质量。

### 24 面试官：同一会话同时来了两个请求怎么办？

我：run_turn 和 resume_turn 使用 SessionStore.driver_lock，对同一会话的驱动尝试获取进程级锁。拿不到锁会失败，不让两个进程同时推进同一条模型工具链。不同会话可以分别运行。计划更新还有 revision 检查，旧版本更新被拒绝，防止无声覆盖。

追问：这个锁让所有后台写入和外部副作用都串行了吗？

我：没有。它保护的是会话回合驱动入口；后台状态写入和外部系统需要各自的并发设计。不能把一个本地锁说成分布式事务。

### 25 面试官：取消和超时有什么区别？

我：模型流有请求 deadline，Shell 有命令 timeout；取消来自用户并设置取消事件。ProcessManager 超时或取消后终止进程组，并截断过长输出。异步工具收到取消时运行时尝试取消任务；若已经开始执行，结果保守标记 unknown，其余未开始的调用标记 interrupted，确保工具消息配对。

追问：把 Python 协程取消了，远端操作就一定停止吗？

我：不一定。远端 MCP 或操作系统进程可能已经产生效果，取消后仍要检查实际状态。CodeRill 的 unknown 状态就是为这种不确定性留出口。

源码落点：coderill/permissions/review.py；coderill/permissions/policy.py；coderill/session/store.py；coderill/session/projection.py；coderill/context/builder.py；coderill/tools/archive.py；coderill/runtime/process.py；tests/test_review_manager.py；tests/test_context_builder.py。

## 五 工具 计划 外部能力与委托

### 26 面试官：工具调用的参数是谁检查的？

我：AgentRuntime 在执行前检查工具是否存在、参数是否是 JSON 对象、必填字段和基本类型。Registry.dispatch 把常见 ToolError 和参数错误变成工具可读的 Error 结果；文件工具自身负责工作区路径解析。这是分层校验：运行时拦截明显坏请求，工具保护具体资源边界。

追问：你实现了完整 JSON Schema 校验吗？

我：没有。当前是必填字段与常见顶层类型检查，不支持 JSON Schema 所有关键字。面试不能把它说成完整 schema validator；更复杂工具应在适配器或工具内部继续验证业务参数。

### 27 面试官：Shell 运行是否安全，失败如何反馈？

我：标准权限模式下 Shell 要审查，always 授权按完整命令和工作区匹配。ProcessManager 设置超时、取消和输出上限，非零退出码记录失败；输出仍回填给模型，便于它修改方案。Windows 使用独立进程组和 taskkill，类 Unix 系统终止进程组。Shell 不是沙箱，审批和进程限制不等于禁止访问工作区之外的资源。

追问：为什么文件工具有路径边界，Shell 没有同等边界？

我：文件工具可以在 Python 层解析具体目标路径；任意 Shell 命令可启动其他程序或访问其他路径，仅凭工作目录无法约束。要做到强隔离，需要容器或 OS 级沙箱，目前没有实现。

### 28 面试官：TaskPlan 如何防止两个更新相互覆盖？

我：计划有稳定任务 ID 和 revision。create、update、revise 都要求调用方提供它看到的 expected_revision；不一致时抛 StaleRevision，调用方要重新读取再提交。依赖更新在真正修改前检查任务是否存在并检测环，验证通过才更新字段和 revision。计划快照作为事件保存，重启后恢复。

追问：计划就是 Agent 的真实执行状态吗？

我：不是。计划表达待办任务和依赖，实际调用、审批和结果以会话事件为准。模型可能忘记更新计划，所以要分别看计划与执行日志，不能把“任务标为 done”当成外部动作已经成功。

### 29 面试官：Skills 和 MCP 解决的是同一个问题吗？

我：Skills 是按需加载的工作流说明，减少初始 prompt 的体积；MCP 是接入外部工具的通信协议，提供发现和调用。CodeRill 的 SkillCatalog 先暴露名称和一行描述，load_skill 再读取正文。MCPManager 通过官方 SDK 管 stdio 或 Streamable HTTP 连接，发现的工具挂到 Registry。两者都提供能力，但一个是指令文本，一个是可执行外部工具。

追问：MCP 服务器断开后会自动成功重试写操作吗？

我：不会把可能有副作用的旧调用当作可安全重放。连接错误会变成工具失败并清理连接；需要先确认服务端状态，再重新决定是否调用。离线测试使用假 SDK 传输，真实服务仍需集成验证。

### 30 面试官：后台任务和子 Agent 是怎样被限制的？

我：委托任务创建独立 Session，child 的 max_turns 不超过父配置与固定上限中较小者。只读 child 只注册读文件、搜索和读取归档等工具；可写 child 才注册写和编辑工具，并在独立 Git worktree 工作。child 没有“再创建子 Agent”的工具，因此不会递归扩张。后台管理器保存运行状态、取消入口和一次性完成通知；进程退出后原来的后台协程不会自动续跑。

追问：子 Agent 的修改怎样进入主工作区？

我：先预览它相对 HEAD 的二进制 diff，再由主用户显式执行 /apply。应用前重新计算 diff，若和刚审过的不同就拒绝；然后 git apply --check，再应用。worktree 负责改动隔离，不是权限沙箱。

### 31 面试官：图片和文本附件如何进入模型上下文？

我：Session.attach_file 校验文件大小和媒体类型，按内容摘要保存到会话附件目录，消息里只保存名称、类型、大小和摘要。构造 ChatMessage 时再读取字节，文本作为内容片段，图片作为带媒体类型的图片片段交给 Provider 适配器。重启后文件仍可读。图片请求前还要检查配置的 ProviderCapabilities.vision，否则明确报错。

追问：这意味着所有模型都能看图吗？

我：不是。能力标志需要针对所选模型配置，适配器和实际服务也要支持对应格式；当前离线测试只验证数据流和拒绝路径，不能替代真实视觉模型调用。

源码落点：coderill/tools/base.py；coderill/tools/files.py；coderill/runtime/process.py；coderill/taskplan.py；coderill/skills.py；coderill/mcp.py；coderill/agent/subagents.py；coderill/runtime/background.py；coderill/session/model.py；tests/test_subagents.py。

## 六 测试证据与高压追问

### 32 面试官：你怎么证明这不是只能跑一次的演示？

我：我按故障边界做离线测试。Provider 用假客户端验证两个适配器的共同契约和限流重试；Session 验证 v1/v2、日志尾部与中间损坏、回放和驱动锁；Agent 验证过期审批、崩溃后 unknown、工具参数失败、流中断与取消后的消息配对；Context 验证原文归档可读；TUI 用假运行时验证流式显示、审批、取消和会话切换。还有架构依赖检查和可重复运行的故障演示。

追问：这些测试覆盖率是多少？

我：当前报告的是测试结果，不把“测试项数”当作覆盖率。最新本地完整运行结果以 `docs/implementation-status.md` 和终端输出为准；没有记录可引用的覆盖率百分比。真实模型、MCP 服务和 Linux CI 实际结果也没有在本地验证。

### 33 面试官：离线通过能说明生产可用吗？

我：只能说明被模拟的状态转换和错误路径按预期工作。真实服务可能有 SDK 版本、限流格式、模型工具调用差异、网络断开和操作系统行为。仓库提供可选的 examples/live_eval.py，用真实 API key 跑一个回合并输出状态、耗时和用量；这只是集成烟测入口，不等于系统性质量评测。

追问：那你会怎样补真实评测？

我：先固定任务集、仓库版本、模型与配置，记录成功标准、工具轨迹、耗时、token 和失败原因，再把故障注入案例与真实任务分开报告。当前没有这些真实任务统计，所以不能报“任务成功率 95%”之类数字。

### 34 面试官：计划要到两万行，为什么现在只有约六千行？

我：一到两万是最初范围估计，不是可靠性的验收指标。仓库用 `scripts/code_stats.py` 分别统计生产代码的物理行和有效行，当前数字见 `docs/implementation-status.md`。我优先完成可解释的职责边界和具体故障场景，没有为达到行数复制实现。若真实评测暴露能力缺口，再针对缺口扩展。

追问：那能说“完整工业级 Agent”吗？

我：不能。可以说“具备分层、恢复和离线故障验证的 Coding Agent 原型”。它仍缺少真实模型的大规模任务评测、跨平台 CI 实际结果、强 OS 沙箱和外部副作用事务保证。用已实现与未验证的边界回答，比一个大标签更可信。

### 35 面试官：这个项目最难的取舍是什么？

我：是把概率性的模型建议接到有副作用的确定性操作。模型可以说“下一步写文件”，但程序必须知道写的是什么、当前文件是否还是审过的版本、进程崩溃后是否可能已经写过。我的取舍是把模型限制在提出调用，关键状态由事件与代码维护；遇到无法证明安全重放的情况，明确记录 unknown，并要求检查实际状态。这牺牲了一点自动化，却让错误有可说明、可恢复的路径。

追问：若只能展示一个案例，你选哪个？

我：选 examples/failure_demo.py。它不需要 API key，能按顺序演示审批等待、人工改文件、旧 diff 被拒绝、新 diff 获批，以及崩溃后的写操作不自动重放。这一段能把 Agent、权限、会话和恢复四层串起来。

源码落点：tests/test_provider_contract.py；tests/test_runtime.py；tests/test_session.py；tests/test_context_builder.py；tests/test_tui.py；tests/test_architecture.py；examples/failure_demo.py；docs/implementation-status.md。

## 七 Agent 循环与工具系统深挖

### 36 面试官：这是不是 ReAct？为什么没有用 LangGraph？

我：从“观察仓库 → 提出工具调用 → 取得结果 → 再决定下一步”看，可以用 ReAct 思路解释 CodeRill。但主循环是 `AgentRuntime._drive` 的异步状态机，没有引入 LangGraph。这里真正需要控制的是审批等待、工具结果配对、请求预算和崩溃恢复；把这些状态写进会话事件，比画一个静态流程图更重要。无需展示模型内部思维链，工具轨迹已经足以说明执行过程。

追问：自己写循环比框架更强吗？

我：不能这样下结论。当前自己实现更便于学习和定位状态转换；代价是要自行验证异常、取消和恢复边界。复杂工作流如果出现大量分支，再评估专门的编排框架。

### 37 面试官：拿“修复失败测试”走一条完整链路。

我：用户问题先变成会话消息；`ContextBuilder` 把系统提示、历史、工具定义整理给 Provider。模型可能先搜索、读文件，再请求修改。读取结果作为 tool message 回填；写文件先生成 diff 并等待审查；批准后才执行。接着模型运行测试，程序保存实际退出码与输出，再让模型判断是否继续。最终回答要指出具体修改、真实运行的测试和未验证之处。

追问：模型说“测试通过”就算完成吗？

我：不算。要看是否有对应 Shell 调用、退出码是否为零、输出是否支持结论，以及工作区最终文件状态。自然语言回答是总结，不是执行证据。

### 38 面试官：如何控制无限循环、超时与费用？

我：`Config.max_turns` 默认 30，限制本轮模型请求；`max_tool_rounds` 默认 200，限制连续工具轮次；`max_turn_seconds` 默认 3600，限制本次运行或恢复后的活动执行时长。`request_timeout` 默认 120 秒，用于单次模型流；命令还有单独 timeout。触顶返回 `budget`，未执行的工具调用也会结算成带 ID 的失败结果。这不是货币费用硬上限，长时间阻塞的同步第三方工具也无法像子进程一样强制杀掉。

追问：等待用户审批也计入 120 秒吗？

我：120 秒是单次模型请求的 deadline。审批暂停发生在工具链；恢复审批时开始新的活动执行时钟，人工等待时间不计入该 3600 秒窗口。次数和工具轮次则由事件恢复，不会因重启清零。

### 38A 面试官：如何判断用户换了任务，为什么要稳定窗口？

我：第一条用户消息建立程序生成的 task hash。后续用户消息进入一个不展示给界面的分类请求，只允许 `same/new/uncertain` 三种 JSON 结果。第一次 `new` 只是候选，随后观察到 `same` 才确认切换；这样一句含糊的“顺便看看”不会立即压掉旧上下文。确认后，Context 以新任务开始的用户消息为安全边界尝试旧任务 checkpoint；原始事件和归档仍保留。分类失败回退 `uncertain`，不会让模型自由填写 hash。

追问：隐藏分类能保证准确吗？

我：不能。它是模型判断加程序约束，离线测试覆盖状态转换和压缩触发，不代表真实任务切换准确率。要量化误判，需要标注真实对话样本；目前没有这项统计。

### 39 面试官：流式返回的工具参数是一段段到的，什么时候执行？

我：两个 Provider 适配器先把片段按工具索引组装成完整 `ToolCall`，流事件只用于展示进度。只有收到完成事件后，Agent 才保存 assistant 调用并检查参数。JSON 解析失败会变成工具错误；若模型因长度限制停下，适配器不把半截调用交给执行器。

追问：流在中途断开呢？

我：本轮模型请求失败，未完成的参数不会执行。已经开始的外部工具则有另一套 `tool_started` 与 `unknown` 恢复语义，不能把两种中断混为一谈。

### 40 面试官：多个工具同时返回，为什么只并行连续读取？

我：模型允许一次提出多个调用，但本地是否并行由执行策略决定。`_run_pending` 把连续只读调用组成批次，经 `asyncio.gather` 执行，按原调用顺序记录结果。遇到写入先等待前面的读取完成；写入之后的读取也要等写完。这样一个“先读 A、再写 A、再读 A”的序列能看到正确版本。

追问：两个只读工具就一定没有风险吗？

我：不一定。这里只承诺注册工具标记的只读语义和结果顺序；文件可能被外部进程同时改动，读取失败仍要如实返回，不能把并发当成一致性快照。

### 40A 面试官：一次性 Shell、后台子任务、长期进程有什么区别？

我：一次性 `bash` 等退出后返回，运行时管 timeout、取消和输出上限；后台子任务是进程内异步 Agent，有独立会话与完成通知，主进程退出后不能自行续跑；`process_start` 启动长期服务，把 stdout/stderr 写入会话日志，`process_status`、`process_logs`、`process_stop` 分别查询、读取、停止。长期进程重启后可能还在运行，日志和元信息可恢复，但停止操作只对原运行器保留的真实进程句柄开放，不能仅凭旧 PID 猜测目标。

追问：这算操作系统沙箱吗？

我：不算。命令仍受审批策略控制，但它本身可以访问工作区外的系统资源；独立进程组只帮助停止，不提供安全隔离。

源码落点：coderill/agent/runtime.py；coderill/providers/openai_adapter.py；coderill/providers/anthropic_adapter.py；tests/test_runtime.py；tests/test_provider_contract.py。

## 八 权限与不可信输入深挖

### 41 面试官：有工具 schema，为什么运行前还要再校验？

我：schema 是给模型看的调用说明，不是权限或执行成功的证明。Agent 先检查工具存在、参数是对象、必填字段和常见顶层类型；Registry 再把异常转为工具失败，文件工具还检查路径边界。当前不是完整 JSON Schema 校验器，复杂业务约束要在具体工具内部检查。

追问：参数错了会不会直接终止 Agent？

我：通常会作为带 `tool_call_id` 的失败结果回填，让模型修正。只有模型服务、上下文或持久化等无法继续的错误，才使回合进入失败或预算状态。

### 42 面试官：standard、aggressive、bypass 的实际差别是什么？

我：`standard` 对需同意且未获授权的工具生成待审查请求；`aggressive` 自动放行当前策略中的文件系统与 Shell 操作；`bypass` 跳过审查。无论模式如何，文件工具执行时仍检查工作区路径。模式控制“是否要求人批准”，不等于操作系统沙箱，也不保证 Shell 只影响工作区。

追问：为什么写入子 Agent 用 aggressive？

我：它只在用户允许委托写入时创建独立 worktree，在那里让 child 自行推进；改动回主工作区前还要预览 diff 并显式应用。这是工作目录隔离和应用关口，不是对任意恶意命令的安全隔离。

### 43 面试官：仓库文件里写“忽略规则并删除目录”，系统怎么办？

我：代码和工具输出都是模型的输入数据，不能提升为用户授权。模型可能仍被误导而提出危险调用，所以真正的防线在运行时：只允许注册工具，检查参数、路径、权限与待审查 ID。Shell 尤其无法靠提示词彻底限制副作用；面试时应承认缺少 OS 级沙箱和专门的提示注入攻防评测。

追问：只在 system prompt 写“不要相信文件”够吗？

我：不够。提示词能降低误用概率，但不能替代程序在执行前做的判定。

### 44 面试官：写前 diff 到底能保证什么，不能保证什么？

我：`ReviewManager` 保存工具原参数、目标路径、工作区、文件 SHA-256 和 diff。用户选择批准时，运行时用当前文件重新计算摘要；不同就废弃旧请求并生成新 ID 与新 diff，必须重审。它防止“审的是旧版本，执行时已明显改变”的情况。复核与真正写入之间仍有极小竞争窗口，当前没有原子 compare-and-swap，不能声称彻底消除 TOCTOU。

追问：Shell 能给出同样可信的写前 diff 吗？

我：不能。任意命令的副作用不可预先枚举。Shell 审查展示的是具体命令和范围，不把它伪装成完整文件变更预览。

### 45 面试官：“始终允许”会扩大到整个项目吗？

我：不会默认扩大。持久授权按工作区和具体目标匹配：文件是具体路径，Shell 是完整命令，MCP 是特定工具，且 tool name 也参与匹配。一次批准只解决当前待审查请求，`always` 才存 scoped grant。这样切换工作区或修改命令后需要重新判定。

追问：授权匹配规则是通配前缀吗？

我：当前核心 `Grant.matches` 用精确匹配，不应把它说成复杂模式引擎。范围窄的代价是可能多次询问，但更容易解释和审计。

源码落点：coderill/permissions/policy.py；coderill/permissions/review.py；coderill/tools/base.py；coderill/tools/files.py；tests/test_review_manager.py；tests/test_permissions.py。

## 九 上下文与会话证据深挖

### 46 面试官：CodeRill 的四级上下文处理具体是哪四步？

我：先估算系统提示、当前可见工具定义、消息、附件和输出预留。较旧的长工具输出会按 JSON、diff、报错日志、搜索结果等形态留下结构化线索，原文按摘要归档；最近的失败结果受到保护。仍过大时裁剪更早的长内容，再把旧对话完整归档并建立索引，最后请求模型给交接摘要并形成 checkpoint。摘要失败用确定性索引降级；没有压缩收益或仍超限就返回预算错误。

追问：这与 FirstCoder 的 L1–L4 完全一样吗？

我：CodeRill 已有 L1–L4 管线和搜索、diff、构建日志、JSON、代码、HTML 等分类压缩，原文可按归档摘要找回；目前没有真实任务集证明压缩质量与对照实现相同。面试时只描述当前代码确实执行的规则。

### 47 面试官：为什么不只保留最近十条消息？

我：最近一条可能是很长的日志，早一条可能是用户关键约束；固定条数不能表达重要性，也可能截断 assistant 工具调用与 tool result 的配对。CodeRill 先在旧回合边界处理内容，最近一段保持完整；旧原文进入归档，通过 `archive_read` 按 ID 取回。原始事件仍保留在日志里。

追问：模型一定会主动找回旧证据吗？

我：不保证。checkpoint 提供归档 ID 和提示，但是否调用检索仍取决于模型。离线测试证明可取回，不等于证明真实模型总能在正确时机取回。

### 48 面试官：刚读过的源文件后来变了，旧结果会自动失效吗？

我：在会话内部，Context 会把成功的 `read_file` 与随后成功的 `write_file`/`edit_file` 按规范路径关联；构造模型请求时，旧读取末尾会出现“文件后来被修改，请重读”的提示。原始工具输出仍在事件日志。外部程序或人工改文件不会自动产生会话事件，因此这不是通用文件版本监视；写前审批另用 SHA-256 复核目标文件。

追问：现在如何降低风险？

我：在实施修改前重新读关键文件，依赖写前摘要和 diff 抓住目标版本变化；若涉及多个相关文件，测试和人工检查仍必要。旧读取提示只防止把会话内已知写入之前的结果误当现状。

### 49 面试官：resume、fork、share 分别复制了什么？

我：`resume` 读取同一会话日志，重建消息、计划、待审查和工具状态；`fork` 创建新会话并复制当前消息、附件内容和计划快照；`share` 输出经规则脱敏的纯文本 transcript。fork 不会复制 Git 工作区，也不应让新会话继续复用旧审批；share 不是公开发布或可执行会话导出，规则脱敏也不能保证清除所有秘密。

追问：原会话里压缩前的所有事件都会复制到 fork 吗？

我：`fork` 复制当前投影消息、附件、计划和会话资产，包括上下文归档与 Skill 快照，所以投影中已有的 archive ID 在新会话仍可读取；不会复制原始会话的每一条旧事件，也不会复制 Git 工作区。这两种“复制历史”的含义要分清。

### 50 面试官：日志损坏和旧格式如何定位？

我：`SessionStore.events` 逐行检查 JSON、版本和递增序号。最后一条没有换行且不完整时视为被截断，追加前会备份残尾；中间坏行、空行或序号缺口直接报错，不会悄悄跳过。解码接受 v1、v2 事件，新的写入用 v2。排查时先定位会话 ID 和行号，再看投影是否得到期望状态，不能直接删掉坏行继续跑。

追问：JSONL 就保证磁盘永远一致吗？

我：不能。追加时 flush 和 fsync 降低已返回事件丢失风险，但文件系统和硬件仍可能故障；日志校验的价值是尽早暴露不可信状态。

源码落点：coderill/context/builder.py；coderill/tools/archive.py；coderill/session/model.py；coderill/session/store.py；coderill/session/projection.py；tests/test_context_builder.py；tests/test_session.py。

## 十 Provider、附件与外部能力深挖

### 51 面试官：换模型只换 model 字符串就够了吗？

我：不够。配置先合并默认值、全局、项目、环境与 CLI，再由 Provider 工厂选择 OpenAI 兼容或原生 Anthropic 适配器。不同模型还涉及 API key、base URL、输出上限和 vision 能力。TUI 切换 profile 时重建运行时，并在会话里记录模型变化；历史事实继续使用当前会话，新的请求按新适配器编码。

追问：两个适配器完全等价吗？

我：只保证 CodeRill 自己的消息、工具调用、流事件和错误契约一致。具体模型的工具质量、视觉能力和兼容服务差异仍要真实集成测试。

### 52 面试官：限流和流中断怎么恢复？会无限重试吗？

我：Provider 把 SDK 异常归为限流、超时、过长等类别。建立请求时最多按配置尝试有限次数，并受 deadline 限制；流开始后若中途失败，当前回合返回失败，不会把半截工具参数执行。模型返回 prompt-too-long 时，Agent 强制压缩并最多重试一次请求。所有这些路径还受本轮 `max_turns` 约束。

追问：重试模型请求是否会重复执行已经写入的工具？

我：模型重试发生在工具执行前；已经启动却未完成的工具由会话恢复标记 unknown，不能自动当作可重试请求。外部副作用没有通用的 exactly-once 保证。

### 53 面试官：文本和图片附件为什么不直接放进 JSONL？

我：`Session.attach_file` 先限定为 10 MB 内的文本或图片，再把字节按 SHA-256 存在该会话附件目录，日志只存名称、类型、大小和摘要。构建请求时按摘要读回；文本变内容片段，图片变媒体片段。这样事件日志不被大块 base64 淹没，重启后还能找到同一附件。图片请求前检查所选 Provider 的 vision 标志。

追问：标志为 true 就保证模型能看图吗？

我：不能。标志来自配置或适配器能力声明，实际服务端是否支持仍需一次真实请求验证。

### 54 面试官：MCP 工具与本地工具的边界在哪里？

我：`MCPManager` 使用官方 SDK 连接 stdio 或 Streamable HTTP 服务，发现工具后包装成 `mcp__服务器__工具` 名称，挂到 Registry。Agent 仍按统一工具链处理参数、审批、结果配对；真正的执行通过 MCP 会话发往服务。服务断开时清理连接并返回错误，不把可能已产生副作用的调用直接重放。外部服务的凭证和权限还取决于其自身配置，当前没有真实服务联调结果。

追问：工具很多时 schema 会不会挤满上下文？

我：不会把所有远端 schema 一次送进去。连接后 Registry 知道完整目录，但模型最初只看到 `mcp_tool_search`；搜索按名称和描述选择最多 8 项，本轮才暴露这些工具定义。运行时也拒绝直接调用未激活的 MCP 工具。搜索结果和激活列表写入会话事件，审批中断后可恢复同一轮的可见范围。工具很多时仍要评估搜索召回率和总提示词成本，目前没有真实服务的规模化数据。

### 55 面试官：Skill 为什么按需加载，恢复时版本会固定吗？

我：目录里先展示名称与短描述，模型需要时调用 `load_skill` 读正文。发现顺序让项目 Skill 优先于全局同名 Skill，并拒绝解析到发现目录之外的符号链接。首次驱动会把目录条目与正文摘要写进会话事件及资产；重启和 fork 后按快照读取，即使原文件改了也不会悄悄换版本。新会话才会发现新版本。Skill 正文仍是提示内容，不具有授权效力。

追问：Skill 文本能授权工具吗？

我：不能。它只是工作流说明，仍受工具注册、路径和权限审查约束。

源码落点：coderill/config.py；coderill/providers/；coderill/session/model.py；coderill/mcp.py；coderill/skills.py；tests/test_provider_contract.py；tests/test_attachments.py；tests/test_mcp_sdk.py。

## 十一 计划、委托与界面深挖

### 56 面试官：TaskPlan 比模型在回答里列任务多了什么？

我：`TaskPlan` 有稳定任务 ID、状态、owner、依赖和 revision；`linear` 按顺序组织，`dag` 由依赖边决定 ready 任务。更新前先比较调用方看到的 revision，检查任务存在和依赖无环，全部通过才修改并加一。快照写进会话事件，恢复后仍能看见。计划反映意图和进度，不代替真实工具证据。

追问：模型标记 done 是否证明测试通过？

我：不是。要回查工具日志、退出码和工作区。计划是控制数据，外部副作用以执行证据为准。

### 57 面试官：子 Agent 是怎样工作的，怎样把修改带回来？

我：委托时创建独立 Session，并把 child 的模型请求数限到父配置与 10 的较小值。只读 child 只有读取、搜索和归档工具；允许写入的 child 在独立 Git worktree 工作，且不注册继续委托的工具。它完成后父会话可预览相对 HEAD 的 diff，应用前再比对预览版本并执行 `git apply --check`。主工作区不会因 child 结束自动改变。

追问：这个 worktree 是安全沙箱吗？

我：不是，它只隔离 Git 工作目录；系统资源和 Shell 的强隔离需要 OS 级机制。当前 child 工具面也不含 Shell，但不能把 worktree 本身说成沙箱。

### 58 面试官：后台任务完成后为什么只通知一次？

我：后台管理器先在父会话记 started，完成、失败或取消再写 finished。Runner 下一次向模型请求前会串行收集结果；自动通知和 `notified` 标志写在同一条事件里，恢复后不会重复插入通知。若委托绑定了计划任务，成功结果可按当前 revision 对账：`in_progress` 可完成，`pending` 还需启动时 revision 未变化且依赖已满足；失败结果不会自动标 done。主模型准备结束时，若计划仍有未完成项，会得到一次临时对账提示。这些机制不等于真实任务成功，仍要看执行证据。后台任务是进程内协程，退出后不会凭空续跑；恢复时应把未完成记录视为中断。

追问：首次工具结果和完成通知是不是两个同 ID 的 tool result？

我：不是。首次结果告诉模型任务已启动，后续通过独立的任务状态和通知查看结果，不能给一个 `tool_call_id` 再写第二条结果。

### 59 面试官：TUI 如何显示流式内容、审批和取消？

我：Textual 界面持有应用 `State`，运行时的事件回调驱动流文本、工具开始和结果展示。审批卡片显示当前请求参数与 diff，按钮只把 request ID 和用户选择交给 `resume_turn`。`Ctrl+C` 向运行时发取消，同时取消界面等待任务；会话切换和模型切换都重建同一类运行时。界面负责呈现，不自行更改权限状态。

追问：TUI 显示“工具开始”后卡住，先查哪里？

我：先查是否有匹配的 `tool_finished` 和 tool message，再看进程 timeout、MCP 连接和待审查状态。显示事件不是执行成功事件。

源码落点：coderill/taskplan.py；coderill/agent/subagents.py；coderill/runtime/background.py；coderill/app/tui.py；tests/test_taskplan.py；tests/test_subagents.py；tests/test_background.py；tests/test_tui.py。

## 十二 评测与个人贡献高压追问

### 60 面试官：FirstCoder 有 benchmark，CodeRill 的成绩怎么讲？

我：不能把 FirstCoder 的 Harbor 数字或 pass@1 归到 CodeRill。CodeRill 有离线单元、契约和故障注入测试；`examples/live_eval.py` 可做单项真实模型 smoke，`examples/benchmark_suite.py` 可以按固定清单复制工作区、执行一次任务和显式验证命令。Harbor 预检、单题入口及分类报告位于 `examples/harbor_run.py`，但尚无真实容器任务结果。面试只报告当前实测的本地测试结果，并说明真实模型、Harbor、MCP 和 Linux CI 尚未现场验证。通过数以 `pytest` 最新输出和 `docs/implementation-status.md` 为准。

追问：那你个人最大的贡献是什么？

我：把长任务的上下文、审批、会话和执行失败连接成可解释的分层链路，并用离线测试和故障演示验证关键状态；实际由我完成的设计和改动按提交与源码说明。最难的取舍是把无法确认的外部副作用明确标成 unknown，要求检查实际状态，而不是为了看起来自动化就盲目重放。

源码落点：tests/test_runtime.py；tests/test_architecture.py；examples/failure_demo.py；examples/live_eval.py；docs/implementation-status.md。

## 十三 从零认识项目的阅读和演示路线

### 第一步 先把项目看成一条请求链

从 coderill/__main__.py 和 coderill/cli.py 看入口，再看 coderill/app/cli.py 的 main、_run_once。此时只回答三个问题：配置从哪里来，会话从哪里来，最终调用哪个运行时。不要一开始把所有工具文件都读一遍。

### 第二步 追踪依赖装配

打开 coderill/app/factory.py 和 coderill/app/state.py。找到 State.rebuild：它创建 Registry、归档工具、后台服务、计划、Skills、Provider 和 AgentRuntime。画出“界面 → 运行时 → 核心服务”的箭头，并确认工具包不反向导入界面。

### 第三步 跟一轮模型工具循环

看 coderill/agent/runtime.py 的 run_turn、_drive、_model_step、_run_pending、_run_tool 和 _record_result。用四个事件记住流程：user message → assistant tool call → tool result → assistant final。再看 coderill/providers/types.py，理解统一协议让 Agent 不必知道具体 SDK。

### 第四步 观察会话恢复

看 coderill/session/store.py 的 append 和 events、coderill/session/projection.py 的 project_events，以及 model.py 的 Session.load。执行 python -m examples.failure_demo，观察旧审批为何失效、tool_started 没有 tool_finished 时为何是 unknown。这个演示在临时目录运行，不修改项目文件。

### 第五步 解释上下文与权限

看 coderill/context/builder.py 的 prepare，再看 coderill/tools/archive.py 的 archive_read；回答“压缩后原文在哪里”。接着看 coderill/permissions/review.py 的 request 和 refresh；回答“用户到底批准了哪个文件版本”。把“可恢复”拆成会话可回放、原文可找回、未知副作用不自动重放三件事。

### 第六步 再看外部能力和验证

看 coderill/runtime/process.py、coderill/runtime/services.py、coderill/mcp.py、coderill/agent/subagents.py，理解单次命令、长期进程、外部连接和 worktree 的边界。最后运行 pytest tests/ -q、ruff check .，对照 docs/implementation-status.md 说清哪些能力只有离线证据。准备口语时先复述 01、05、09、18、20、22、30、35、38A、40A、46、49、57、60 题，再练每题的追问。

### 第七步 跑一遍无需 API key 的故障演示

在项目根目录运行 python -m examples.failure_demo。第一幕，假模型请求把 example.txt 改为 agent，运行时给出 review ID 并停在 waiting；此时目标文件还保持 original。第二幕，演示代码模拟人工把文件改为 human edit，再提交旧 ID 的 approve；系统应拒绝旧 diff，生成新 ID 和以 human edit 为基线的 diff。第三幕，新审查批准后才写入 agent。第四幕，另起会话只记录 tool_started 而不写 tool_finished，恢复后该调用是 unknown，目标 crash.txt 不应出现。

沿演示回看四个源码位置：ReviewManager.request 生成审查；resume_turn 调用 refresh 复核；SessionStore.append 保存事实；AgentRuntime._settle_unknown_results 处理崩溃后的未完成调用。请自己回答“如果省掉这个位置，会观察到哪种错误”，这比背类名更能解释架构理由。

### 第八步 用一张纸复述十分钟面试版本

前两分钟讲用户任务与一条请求链；接下来的两分钟解释 app、Agent、Provider、Context、Session、Permission、Tool 各层输入输出；然后用三分钟讲旧 diff 失效、上下文归档和 unknown 恢复；最后三分钟展示离线演示与测试，并明确实际未测的真实模型、MCP 服务、Linux CI 和浏览器。面试官打断时，优先回到具体事件和文件：谁记录、谁判断、谁执行、谁验证。

自测三连问：①“模型请求写文件后，谁有最终决定权？”答 ReviewManager 与用户选择，Agent 只衔接；②“摘要丢了细节怎么办？”答原文仍在事件与归档中，但检索依赖 archive ID；③“进程崩溃后能直接重试吗？”答不能，先看 tool_started/finished 及实际文件或外部状态。这三问可以串起项目主线。

### 第九步 再做真实模型验证

只有在已有 API key 且愿意承担调用费用时，才运行 examples/live_eval.py 的可选 smoke 入口。要评估多任务，先准备 `examples/benchmark_suite.py` 接受的 JSON 清单：每项给出 id、workspace、prompt 和至少一条 verify 参数数组，例如 `["python", "-m", "pytest", "-q"]`。运行器复制任务工作区，各任务只执行一次模型回合，记录模型、Provider、用量、耗时和验证退出码；没有验证命令记为 unverified，不算成功。Harbor 的可选适配器和前提见 `docs/evaluation.md`。固定仓库版本、模型、配置、权限和预算，才有可复核的分母。不要把 FirstCoder 的 Harbor 成绩、CodeRill 的离线测试数量和真实任务成功率放在同一个数字里。
