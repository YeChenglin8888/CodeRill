# 从源码到安装包、Docker 和 Jenkins：CodeRill 实践教程

以当前 CodeRill 为例，按“运行 → 构建 → 产物 → 部署 → 验证 → 自动化”的顺序学习。

状态说明：前端构建、Docker 构建 / 启动 / 存储验证已经实测。Windows 桌面壳、安装包和 Jenkins 尚未搭建；本教程不会把示例命令或未运行的流水线说成已完成。

## 1. 先认识六个东西

| 名词 | 含义 | CodeRill 的例子 |
| --- | --- | --- |
| 源码 | 人可以编辑的程序与配置 | Python、TS、CSS、Dockerfile |
| 构建 | 把源码处理为可交付形式 | TS 编译、Python 冻结、构建镜像 |
| 构建产物 | 构建后真正交付的文件 | web/dist、exe、setup.exe、镜像 |
| 部署 | 将产物放到目标环境并配置启动 | 安装桌面程序、在服务器启动容器 |
| 运行 | 操作系统实际执行程序 | Python 服务进程、桌面窗口 |
| CI/CD | 自动执行检查、构建、发布等步骤 | Jenkins Pipeline |

封装是统称：决定把代码、资源和依赖按什么边界组织起来。它可以指 Python 包、桌面程序，也可以指 Docker 镜像，并不是只有 Docker 这一种形式。

整体有两条交付线：

    源码 ─→ TS 构建 + Python 打包 + 桌面壳 ─→ Windows 安装包 ─→ 用户电脑
      └──→ 前端构建 + Python 运行环境 ──────→ Docker 镜像 ──→ Docker 主机
                      ↑
             Jenkins 可以自动执行这些构建

## 2. 第一步：弄清楚源码怎样运行

进入项目根目录：

~~~powershell
cd 'C:\Users\YCL\Desktop\智能agent\CodeRill'
~~~

现在 CLI 调用 Python AgentRuntime；功能客户端使用 TS 界面，调用同一个 Python 运行时。界面设计稿是独立的评审原型，尚未完全替换功能客户端。

- TS / HTML / CSS 决定界面的样子和交互。
- Python 提供会话、模型调用、审批、工具执行和恢复。
- 模型通常是外部 API，不会因为打包应用就把 Claude 等模型权重一起装进来。
- API key、工作区路径和用户数据属于运行配置 / 数据，不能固定进安装包或镜像。

验收标准：你能说明“页面由谁显示，模型由谁调用，命令在哪里执行，数据存在哪里”。

## 3. 第二步：构建前端

~~~powershell
cd web
npm ci
npm run build
cd ..
~~~

npm ci 按 package-lock.json 安装依赖；build 完成类型检查和 Vite 构建。输出在 web/dist，主要是 index.html、JavaScript 和 CSS。

npm run dev 用于开发时修改后实时更新；dist 是交付时使用的静态资源。最终用户无需为了看页面安装 Node 或运行 Vite 开发服务。

验收标准：构建成功，并知道后续安装包或镜像要携带 web/dist，而不是让用户运行源码开发服务器。

## 4. 第三步：理解 Python 可执行程序

Python 平常需要解释器和 site-packages。PyInstaller 收集 Python 运行时、导入模块及指定资源，生成针对当前平台的程序；它通常不把 Python 转成一套完全独立的原生业务代码。

先用现有的无依赖示例练习，不直接拿复杂服务猜配置。下面命令需要安装 PyInstaller，尚未在本轮执行：

~~~powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --onedir --name coderill-demo --distpath dist/packaging-demo --workpath build/packaging-demo --specpath build examples/docker-workspace/hello.py
.\dist\packaging-demo\coderill-demo\coderill-demo.exe
~~~

--onedir 输出一个目录，exe 和依赖一起交付，便于检查缺少什么；--onefile 把内容组织为一个文件，启动时还可能解包。单个 exe 与完整安装向导是两件事。

正式封装 CodeRill 还要处理：

1. 使用专门的打包入口，调用 coderill.app.web.main；不要直接冻结带包内相对导入的文件后假定能启动。
2. 收集 web/dist、必要的 Python 模块、运行资源和动态导入。
3. 修改当前依赖源码目录的静态资源寻址，使安装后的目录同样可用。
4. 配置、会话写进用户数据目录，不能写进 Program Files。
5. 管理服务启动、就绪检查、退出和异常日志。
6. 在没有开发虚拟环境和源码目录的干净 Windows 环境验证。

Python 被打包不表示 Git、Node、浏览器和 MCP 外部程序也自动打包；实际工具依赖应检测、说明或按需提供。

官方参考：[PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)、[使用说明](https://pyinstaller.org/en/stable/usage.html)。

## 5. 第四步：把它做成桌面安装包

采用先前讨论的 Tauri 路线：

    Tauri 桌面窗口
    ├─ 展示 TypeScript 构建后的界面
    ├─ 启动随包携带的 coderill-server.exe
    ├─ 等待本地服务就绪，再交互
    └─ 管理文件夹选择、窗口关闭和后台进程退出

Tauri 将 Python 程序作为 sidecar 携带；Python 业务代码仍使用现有运行时。需要 Windows 构建环境、Rust、Visual Studio C++ 构建工具与 WebView2 相关配置。

实现顺序：

1. 完成上一步的 Python 服务打包并单独启动验证。
2. 初始化 Tauri 工程，接入现有前端构建流程。
3. 配置 sidecar 及平台 / 架构名称，加入必要运行资源。
4. 实现启动握手、可用端口、同源通信或受控 IPC，以及进程退出处理。
5. 配置应用名、版本、图标、安装范围和 NSIS / MSI 输出。
6. 在已初始化并配置好的桌面工程执行 Tauri build。
7. 在干净环境验证安装、运行、升级、卸载与用户数据保留；公开分发再处理代码签名。

完成工程后，构建命令可以是 npm exec tauri build，输出安装包通常位于 src-tauri/target/release/bundle 下；具体路径取决于工程目录。本项目当前没有 src-tauri，所以此命令目前不是可直接运行的项目入口。

安装包安装的是客户端及内部运行时，模型账户、API key 和被操作项目的依赖仍由实际使用场景决定。安装包、镜像可从同一份业务源码构建，但两者不能互相替代。

官方参考：[Tauri 前置条件](https://v2.tauri.app/start/prerequisites/)、[Python sidecar](https://v2.tauri.app/develop/sidecar/)、[Windows 安装包](https://v2.tauri.app/distribute/windows-installer/)。

## 6. 第五步：用 Docker 封装服务器运行环境

这一部分已经在当前电脑验证。

~~~powershell
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail 30 coderill
~~~

打开 http://127.0.0.1:18765/ 。默认占位密钥适用于部署练习，不能调用模型。

逐项对应：

| 文件 / 指令 | 负责什么 |
| --- | --- |
| Dockerfile | 说明怎样构建镜像 |
| FROM Node 构建阶段 | 安装 npm 依赖，编译前端 |
| FROM Python 运行阶段 | 准备 Python、Git、服务和前端产物 |
| .dockerignore | 决定哪些文件不进入构建上下文 |
| image: coderill:local | 给构建结果命名 |
| compose.yaml | 说明镜像如何启动、配置、映射端口和挂载 |
| 127.0.0.1:18765:18765 | 宿主机 loopback 端口 → 容器端口 |
| bind mount | 指定宿主机目录映射到 /workspace |
| named volume | 保存会话，替换容器后还能读取 |
| HEALTHCHECK | 检查服务就绪，不调用模型 |

~~~powershell
py scripts/check_docker.py --recreate
~~~

这个练习会替换本演示容器，并验证同一会话仍能读取。不要在演示 Agent 正执行任务时运行。

停止演示：

~~~powershell
docker compose stop
~~~

容器不会自动包含宿主机软件。当前 Linux 容器的命令和路径按 Linux 环境解释。远程 Docker 主机也不会自动访问浏览器使用者的本机代码。

更详细的存储、端口、权限及面试追问见 [Docker 实践](docker-deployment.md)。

## 7. 第六步：把镜像交付给另一台主机

本地 Compose build 只能证明你在当前机器构建并运行过。企业常用镜像仓库把构建结果交给其他机器；Harbor、企业云镜像仓库等都可承担这个角色。

下面用 registry.example.com 表示尚未提供的镜像仓库，不能原样运行：

    docker tag coderill:local registry.example.com/team/coderill:build-12
    docker push registry.example.com/team/coderill:build-12

目标服务器的部署 Compose 使用这个明确版本的 image，不再每次拉源码重新 build；服务器登录仓库后 pull，再 up。服务器提供自己的密钥、项目目录和会话数据卷。

安装包可以上传制品库或发布页供用户下载；镜像则推到镜像仓库供 Docker 主机拉取。对于升级，尽量推广同一份已验证产物。回滚切回上一个镜像版本，仍需确认数据格式兼容。

验收标准：你能区分“在本机启动成功”和“通过仓库把同一产物部署到另一台服务器”。

## 8. 第七步：启动 Jenkins，理解控制节点与构建节点

Jenkins 控制节点负责页面、任务调度、流水线记录和凭据管理；构建节点负责执行 shell / bat 等构建命令。这里的 Jenkins agent 是执行任务的机器，不是 AI 智能体。

可以用 Docker 启动学习用的 Jenkins 控制节点。下面命令会下载镜像并创建新的本地服务，本轮没有执行：

~~~powershell
docker run -d --name coderill-jenkins --restart unless-stopped -p 127.0.0.1:18080:8080 -v coderill-jenkins-home:/var/jenkins_home jenkins/jenkins:lts-jdk21
~~~

打开 http://127.0.0.1:18080/ 。获取首次设置的临时管理员密码：

~~~powershell
docker exec coderill-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
~~~

由你在本地查看密码、完成设置向导并创建管理员账户。选择建议插件；示例流水线至少需要 Pipeline、Git、JUnit 和相关 Pipeline 步骤。Jenkins 数据卷用于保存配置与任务历史。

官方 Jenkins 镜像本身不包含 Docker CLI，也不自动具有你 Windows 上的 Python、Node 与 Rust。把 Jenkins 容器启动起来，只完成了控制节点的部署。

为了接着使用本机现有工具，可以创建专用 Windows 构建节点：

1. 在具备 Java、Git、Python、Node 和 Docker CLI 的 Windows 机器准备构建目录，例如 C:\jenkins-agent。
2. 在 Jenkins 的节点管理中创建一个 Permanent Agent，名称自定，标签设为 windows-coderill，先用 1 个执行器。
3. 使用控制节点提供的入站连接说明启动 agent；本机通过 WebSocket 连接时可复用 HTTP 入口，具体命令与密钥以 Jenkins 节点页面生成的内容为准。
4. 在节点上检查 Java 版本符合该 Jenkins 版本要求，确认 py、npm、git、docker 命令可用，Docker Engine 可访问。
5. 节点显示 online 后，流水线才会真正开始执行。

给构建节点 Docker 访问权意味着能操作对应 Docker 主机；学习时使用专用节点和可信代码，不需要为了入门将宿主机 Docker socket 挂进 Jenkins 控制容器。

官方参考：[Docker 安装 Jenkins](https://www.jenkins.io/doc/book/installing/docker/)、[使用构建节点](https://www.jenkins.io/doc/book/using/using-agents/)。

## 9. 第八步：配置第一条 Jenkins 流水线

仓库提供 [Windows 学习示例](examples/Jenkinsfile.windows)，包含：

    Checkout → Web API checks → Frontend build → Container image
                   ↓                ↓                 ↓
               JUnit 报告       web/dist 归档       ci-构建号 镜像

这个文件是待运行的示例，不代表已经在 Jenkins 验证。它使用 windows-coderill 标签，创建 Python 虚拟环境，安装项目测试依赖，运行 Web API 测试，构建前端，并生成带当前构建号的本地镜像。

设置步骤：

1. 将准备使用的源码与流水线文件提交到你自己的 Git 仓库。当前工作区未提交的文件不会自动出现在远端构建里；不应把 .env、会话卷或虚拟环境提交进去。
2. Jenkins 新建一个 Pipeline 任务。
3. Definition 选择 Pipeline script from SCM；SCM 选 Git。
4. 填写实际仓库地址和实际分支；私有仓库使用 Jenkins Credentials 中的凭据。
5. Script Path 填 docs/examples/Jenkinsfile.windows。
6. 保存并点击 Build Now。
7. 查看阶段结果、Console Output、测试报告和归档产物；在构建节点查看 coderill:ci-构建号 镜像。

先手动触发，跑通后再在代码托管服务配置 webhook；触发地址应按实际插件和网络环境配置。不要把轮询、webhook 与构建步骤混为一谈。

示例没有自动推送镜像或更新当前正在运行的学习服务。后续有真实镜像仓库与测试服务器后，再追加发布阶段：

    测试通过 → 构建版本镜像 → 登录仓库并 push → 测试环境 pull / up
                                                ↓
                                  healthz 验证 → 人工确认 → 生产发布

仓库密码、SSH key 和模型 key 应存在对应的凭据 / 密钥管理位置；流水线引用凭据 ID，不能把明文写进 Jenkinsfile。

桌面安装包可在具备 Rust、C++ 构建工具和打包脚本的 Windows 节点使用另一条任务构建、签名并归档。当前示例只覆盖前端与 Docker CI，不会生成安装包。

官方参考：[Jenkinsfile](https://www.jenkins.io/doc/book/pipeline/jenkinsfile/)、[Jenkins 与 Docker](https://www.jenkins.io/doc/book/pipeline/docker/)。

## 10. 学完后按这六个问题复述

1. 应用运行需要什么？——代码、运行时、依赖、配置、数据及外部服务。
2. 构建产生什么？——前端静态文件、可执行程序、安装包或镜像，取决于目标。
3. Docker 做了什么？——定义并分发容器运行环境，通过挂载和配置连接外部数据。
4. Jenkins 做了什么？——在构建节点自动执行原本能手工完成的测试、构建和发布流程。
5. 为什么替换容器后数据还在？——数据放在容器生命周期之外的命名卷或挂载中。
6. 你亲自验证到哪一步？——区分手动本地部署、自动构建、镜像仓库发布、真实服务器部署和 Windows 安装测试。

建议练习顺序：先运行源码与前端构建，再重做现有 Docker 检查；接着跑第一条 Jenkins CI；最后完善桌面工程和安装包发布。每一步都有独立验收，避免同时排查四层问题。
