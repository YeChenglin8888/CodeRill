# CodeRill 客户端设计稿

以用户提供的桌面截图为视觉基准。完整设计规范位于 [项目根目录 DESIGN.md](../../DESIGN.md)，包含页面结构、精确尺寸、颜色、字体、控件状态、键盘操作、响应式布局及 CLI 能力映射。

这是设计评审原型。示例模型、会话、工具结果、费用与子任务均不代表真实运行；点击只更新本页内存，刷新恢复初始状态。不读取真实配置，不执行命令，不上传附件内容，不修改工作区。

## 打开预览

可以直接用浏览器打开 [index.html](index.html)。也可以从 CodeRill 项目根目录启动本地预览：

~~~powershell
py -m http.server 8766 --bind 127.0.0.1 --directory docs/client-design
~~~

然后打开 http://127.0.0.1:8766/ 。该端口用于独立设计预览；现有 Agent 客户端仍使用其原来的入口。

## 建议评审顺序

1. 新建页：参考侧栏比例、欢迎标题、输入框和项目条；点击建议填草稿。
2. 输入框：切换模型、执行 / 只读计划；按 Ctrl+K 搜索命令；添加和移除附件。附件只显示名称。
3. 点击左侧“完善会话恢复流程”：展开工具活动，查看 diff，批准或拒绝。右上角可切换任务详情。
4. 项目：搜索名称或路径，选择工作区，添加一个只存在于预览列表的路径。
5. 后台任务：切换运行中 / 已完成，查看报告和可写子任务审查入口。
6. 左下设置：通用、模型、权限、扩展和快捷键。底部“设计预览”说明设置不会写入本机。
7. 收窄浏览器：手机导航改为抽屉，详情覆盖主区，输入工具保持可达。

## 已导出的界面

| 界面 | 图片 |
| --- | --- |
| 新建任务 · 1440 × 900 | [01-new-task.png](previews/01-new-task.png) |
| 对话与审批 · 1440 × 900 | [02-approval.png](previews/02-approval.png) |
| 项目 | [03-projects.png](previews/03-projects.png) |
| 后台任务 | [04-background.png](previews/04-background.png) |
| 权限设置 | [05-settings.png](previews/05-settings.png) |
| 与参考图相同尺寸 · 1208 × 801 | [06-reference-size.png](previews/06-reference-size.png) |
| 平板 · 834 × 1112 | [07-tablet.png](previews/07-tablet.png) |
| 手机新建页 · 390 × 844 | [08-mobile.png](previews/08-mobile.png) |
| 手机对话页 · 390 × 844 | [09-mobile-chat.png](previews/09-mobile-chat.png) |

## 设计与生产边界

正式实现继续复用现有 web/src 的 TypeScript 和 Python AgentRuntime。本目录独立保存设计资产，未替换现有生产界面。模型配置保存、项目元数据持久化等后续接口需求已在 DESIGN.md 中标注。
