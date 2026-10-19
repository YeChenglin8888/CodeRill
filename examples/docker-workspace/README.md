# CodeRill Docker 演示工作区

这个目录通过 bind mount 映射到容器的 /workspace。只挂载演示项目，便于观察宿主机和容器里的文件如何对应。

hello.py 是一个无依赖的示例。进入容器后可以运行 python /workspace/hello.py。

初始目录不是 Git 仓库。若要练习可写子 Agent 的 Git worktree，先在这个演示目录初始化并提交 Git，再使用子任务能力。

部署检查脚本不会调用真实模型。要体验 AI 修改，需要在运行时配置真实 API key，保留操作审批。
