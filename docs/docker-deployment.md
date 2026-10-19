# CodeRill Docker 部署实践与面试讲解

本例面向“能部署、能验证、能解释”的单机实践。桌面安装包与容器镜像是两个交付目标：安装包提供本地窗口；本例把 Python 服务和已构建的 TypeScript 界面装进镜像，再由浏览器访问。现有新 UI 设计原型仍独立保存，不冒充已经接入 Agent 的生产界面。

从前端构建、Python 打包、Windows 安装包到 Jenkins 的完整学习顺序，见 [端到端交付教程](delivery-learning-guide.md)。

## 1. 企业通常怎样使用 Docker

| 常见用途 | 企业里的具体做法 | 为什么有价值 |
| --- | --- | --- |
| 开发环境 | 用 Compose 启动 API、数据库、缓存等服务 | 减少每个开发者手动配置环境的差异 |
| CI 测试与构建 | 在指定镜像中运行测试、构建产物 | 测试环境明确，减少宿主机依赖污染 |
| 测试 / 预发布 / 生产部署 | CI 构建带版本标签的镜像，推送企业镜像仓库；服务器拉取并启动 | 发布的是同一个已验证产物 |
| 后台任务 | 将任务消费者、定时作业或数据处理程序单独部署 | 能分别配置资源、升级和扩容 |
| 任务执行环境 | 把构建、代码检查等放入具有指定工具链的容器 | 便于控制依赖与可见目录，但仍需配置权限和资源限制 |

常见发布链路：

    Git 提交 → CI 测试 → 构建镜像 → 推送镜像仓库
                                          ↓
                           测试环境 → 预发布 → 生产宿主机
                                          ↓
                           日志、健康检查、监控与版本回滚

单台服务器可用 Compose 管理服务；需要跨机器调度、自动扩缩容等时，再结合 Kubernetes 等编排平台。使用 Docker 并不等于必须上 Kubernetes。学习阶段先理解镜像、容器、网络、存储和发布过程。

官方资料：[Docker 概览](https://docs.docker.com/get-started/docker-overview/)、[CI 中使用 Docker](https://docs.docker.com/build/ci/)、[Compose 生产部署](https://docs.docker.com/compose/how-tos/production/)。

## 2. 这个示例部署了什么

    Windows 电脑（Docker Desktop）
    ├─ 浏览器 → 127.0.0.1:18765
    │                 ↓ 端口映射
    ├─ Docker Linux 容器
    │    ├─ CodeRill Web / API：0.0.0.0:18765
    │    ├─ 已构建的 TS 页面
    │    ├─ Python AgentRuntime、Git、ripgrep、shell
    │    ├─ /workspace ────────────── bind mount
    │    └─ /home/coderill/.coderill ─ named volume
    ├─ examples/docker-workspace：你能直接看到的演示项目
    └─ coderill-demo_coderill-data：会话、附件、配置及子任务工作区

在原生 Linux 服务器上，Docker Engine 直接运行 Linux 容器。当前 Windows 使用 Docker Desktop 的 Linux 环境，存在额外一层虚拟化；容器与完整虚拟机的边界不同，不能描述为“每个容器一套独立内核”。

容器里的 0.0.0.0 用于接受映射进来的连接；宿主机端口只绑定 127.0.0.1，因此默认只有这台机器可以访问。两端均使用 18765，也与当前 Web 服务的 Host / Origin 检查一致。

这里的“宿主机”指运行 Docker 的机器。若部署到远程服务器，Agent 访问的是服务器上挂载的项目，并不会自动读到访问者笔记本上的文件。

## 3. 从零启动

前提：Docker Desktop 已启动并使用 Linux containers；在 CodeRill 项目根目录执行。

~~~powershell
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
~~~

打开 http://127.0.0.1:18765/ 。Compose 会构建镜像、创建服务与数据卷，并等待健康检查通过。

无需真实模型密钥即可练习部署。默认占位密钥只为让服务启动、创建会话和验证存储；它不能调用模型。不要用它测试 AI 回复。

若要真实使用 Agent，复制配置示例，在 .env.docker 中填写自己的 provider、模型、地址和密钥：

~~~powershell
Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker up -d --wait
~~~

编辑配置后再执行后面一条命令即可重建受配置影响的容器。不要把真实密钥写入 Dockerfile、镜像构建参数或 Git，也不要把 docker inspect / compose config 展开的环境变量截图公开。

## 4. 六个可以亲自验证的点

### 镜像与容器

~~~powershell
docker image ls coderill
docker compose ps
~~~

镜像是启动模板；容器是由镜像创建的运行实例。一个镜像能创建多个容器。镜像标签可以移动，生产发布应记录不可变版本或 digest；本例使用 local 标签方便开发练习。

### 日志与健康检查

~~~powershell
docker compose logs --tail 50 coderill
Invoke-RestMethod http://127.0.0.1:18765/healthz
~~~

健康检查只验证本地服务、页面资源和工作目录，不调用付费模型。日志写到 stdout / stderr，由 Docker 收集，并限制单文件大小及保留数量。

HEALTHCHECK 标记 unhealthy 不会自动触发 restart。restart: unless-stopped 主要处理容器主进程退出和服务恢复；业务不健康后的自动处理需要单独的监控 / 编排策略。

### 工作目录挂载

~~~powershell
docker compose exec coderill ls -la /workspace
docker compose exec coderill python /workspace/hello.py
~~~

/workspace 对应宿主机 examples/docker-workspace。这个 bind mount 为可写，容器写入会影响该目录。没有挂载整个用户主目录、系统盘或 Docker socket。

Linux 宿主机需保证演示目录对容器 UID 1000 可读写；挂载原有项目之前应按项目实际所有者设置权限，不要通过全局 chmod 777 解决。

### 会话持久化

~~~powershell
py scripts/check_docker.py --recreate
~~~

脚本验证页面、健康状态、端口、资源限制、非 root 用户、挂载文件可见性；创建一条标记会话，然后替换演示容器，检查新容器仍能读到这条会话。它不调用模型。

--recreate 会替换本演示服务，应在没有 Agent 正在执行任务时运行。检查创建的会话会留在演示数据卷里，便于在界面中观察；临时挂载文件由脚本清理。

~~~powershell
docker compose down
docker compose up -d --wait
~~~

普通 down 删除容器与网络，保留命名卷。不要在保留学习数据时加 --volumes / -v，该选项会删除本示例的会话卷。重建容器不代表清空会话，也不代表内存中的进行中任务能无缝继续；中断恢复仍由 CodeRill 的事件日志负责。

### 资源与权限

Compose 限制 1 CPU、1 GiB 内存和 256 个进程；以非 root 用户运行，取消额外 Linux capabilities，限制权限提升。该资源配额是演示默认值，并非压测后的生产容量；项目安装依赖、浏览器或大型测试可能需要更多。

~~~powershell
docker stats --no-stream
docker compose exec coderill id
~~~

容器里的程序与依赖来自镜像。这个运行镜像提供 Python、Git、ripgrep 和 shell；Node 只在前端构建阶段使用，最终镜像不包含 Node 工具链、浏览器或你宿主机安装的 MCP 外部程序。需要处理对应项目时，应构建包含所需工具的派生镜像。

## 5. Dockerfile 为什么分两阶段

第一阶段使用 Node 安装前端构建依赖并编译 TS；第二阶段使用 Python 运行服务，只复制前端编译产物，不带入前端 node_modules 和 Node 构建工具。Python 服务源码保留在 /app，安装为本地包，以复用现有静态资源寻址。

.dockerignore 使用构建上下文允许列表，只发送运行源码和前端构建输入。虚拟环境、.git、密钥文件、会话数据和设计截图不会发给构建器。

依赖清单先于源码 COPY，使只有业务代码变化时，前端 npm ci 层可使用缓存；Python 依赖当前按项目声明解析，没有转成完整锁文件。本例验证的是可重复执行的交付流程，不声称字节级可复现。生产发布还应固定依赖、基础镜像 digest，并运行漏洞扫描。

参考：[多阶段构建](https://docs.docker.com/build/building/multi-stage/)、[构建最佳实践](https://docs.docker.com/build/building/best-practices/)。

## 6. 如果企业要真正使用，还差什么

当前代码是本地、单用户服务，没有用户登录、租户隔离、访问审计平台，也不是经过多用户压力测试的服务框架。不能直接将本例端口开放到公网后称为“企业版”。

企业共享部署需要按实际需求补充身份认证、HTTPS / 反向代理、每个用户或任务的工作区与密钥隔离、备份与恢复、并发控制、监控告警和明确的发布回滚流程。反向代理只解决入口问题，不自动提供任务隔离。

命名卷保存当前会话，不等于已经有备份；把会话文件拷出后，还需要验证能恢复。文件可以回放不代表正在运行的 shell 可以精确执行一次，CodeRill 遇到未知工具副作用仍需检查真实状态。

普通容器也不是面向恶意代码的完整安全沙箱；可写挂载有真实副作用。桌面程序仍可默认用本机环境，Docker 作为另一种部署 / 执行选择。

## 7. 面试可以怎样讲

只在完成对应操作后使用下面的表述：

“我把 CodeRill 的 TypeScript 界面和 Python Agent 做了容器化。前端使用 Node 构建，运行镜像只保留构建产物与 Python 服务。用 Compose 管理端口、项目目录挂载、会话数据卷、健康检查、日志轮转和资源限制。我验证过容器写入能在宿主机演示目录看到，也用新容器读取了旧会话，确认持久化生效。这个版本是单机单用户实践，多用户部署还需要认证、隔离与运维能力。”

| 追问 | 结合本项目的回答 |
| --- | --- |
| Dockerfile / image / container / Compose 的区别？ | 构建说明 / 构建产物 / 运行实例 / 服务与运行参数声明 |
| COPY 和 volume 的区别？ | COPY 把构建输入写进镜像；挂载在运行时提供外部数据 |
| bind mount 和 named volume？ | 项目目录要与宿主机直接对应，用 bind；会话由 Docker 管理生命周期，用 named volume |
| 为什么容器监听 0.0.0.0？ | 容器 loopback 只接受容器内部连接，映射进来的连接需要服务监听容器接口 |
| 为什么改宿主机端口可能得到 403？ | 当前服务校验 Host / Origin；本例固定两端 18765，不能只随意改一端 |
| unhealthy 会自动重启吗？ | 不会；健康状态与进程退出重启策略是不同机制 |
| 为什么没有把密钥放进镜像？ | 镜像会分发并保留构建历史；配置在启动时提供，生产可用密钥管理系统 |
| 如何排查打不开？ | 看 compose ps → logs → healthz → 端口绑定 → Host / Origin → 前端是否构建 |
| 如何更新与回滚？ | 使用明确镜像版本启动新容器；回滚到旧镜像，前提是存储格式向后兼容 |
| 为什么没有直接上 Kubernetes？ | 当前是单机服务；先验证交付、网络和存储，按真实规模引入集群编排 |
| Docker 能访问访问者电脑里的代码吗？ | 只能看到容器文件与宿主机挂载；远端浏览器不会自动提供本地文件系统 |
| 实践里做过什么，没做过什么？ | 明确区分本地 Compose 部署与真实企业生产经验，不把示例包装成线上系统 |

## 8. 停止示例

~~~powershell
docker compose stop
~~~

这会停止本演示服务，保留容器和会话数据。需要继续时执行 docker compose start。不要停止、清理或删除本机其他项目的容器与卷。

## 9. 本次实测记录

2026-10-18，在 Windows Docker Desktop 的 Linux containers 环境中完成验证：

- Docker Engine 29.2.1、Compose 5.1.0；多阶段镜像构建成功。
- coderill-demo-coderill-1 状态为 running / healthy，端口为 127.0.0.1:18765。
- 镜像 ID：sha256:4ed204a5fe892d3f73097b93db85ccff7bc74d8fb077527d2a273cfea64ca570 。这是本次本机构建产物，尚未推送镜像仓库。
- Web 页面、健康端点、工作区元数据、非 root 身份和资源上限检查通过。
- 容器创建的临时文件可从宿主机演示目录读取，检查后已移除。
- 创建会话 20261018-122228-df103b 并记录标记消息；替换为不同容器后，同一会话和消息仍可读取。
- 容器内 coderill --version 输出 0.1.0。
- Web 相关测试 4 项通过，相关 Python 代码静态检查通过。

没有发送真实模型请求，也没有验证企业多人访问、真实服务器发布或桌面安装包。上述记录证明本次本地部署与存储行为，不等于生产容量或安全认证。
