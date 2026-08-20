# Workspace Gateway

面向 Coding Agent 的持久化代码工作区与隔离运行控制面。Agent 首先面对 Workspace，只有运行、构建和预览时才进入 Sandbox。当前覆盖：

- 阿里云 PAI-Sandbox：通过其 E2B-compatible SDK Domain 接入；
- E2B Cloud：通过 E2B Python SDK 默认服务接入；
- 火山引擎 Sandbox：提供 E2B-compatible 模式和隔离的 REST Bridge 模式；
- 统一的 Workspace 创建、文件读写、Git 版本和运行 API；
- 统一的 Sandbox 创建、状态、命令、后台进程、预览、暂停和销毁能力；
- 使用 Gateway ID 隐藏不同厂商的实例 ID；
- PostgreSQL 保存 Workspace、运行记录及 Gateway 与厂商实例的映射；SQLite 仅用于本地测试；
- 内置管理控制台，查看运行实例、Provider 配置和生命周期详情。
- 内置 Workspace-first MCP Server，Agent 在 Workspace 中编辑代码，需要运行时才使用 Sandbox。
- Git-backed Workspace 持久化项目代码、提交历史和运行版本；Sandbox 只作为临时执行层。

详细设计见 [技术方案](docs/technical-solution.md)。
MCP 的工具、安全边界和演进方案见 [MCP 接入技术方案](docs/mcp-integration.md)。
Workspace 的代码保存、版本控制与运行同步见 [Workspace 技术方案](docs/workspace-architecture.md)。
MCP Client 的连接、工具调用和错误处理见 [MCP Client 接入指南](docs/mcp-client-integration.md)。
火山引擎 E2B、原生 veFaaS 和可选企业 Bridge Contract 见 [火山引擎云沙箱接入技术方案](docs/volcengine-sandbox-integration-technical-solution.md)。
联调中发现的问题、原因和防复发检查见 [调试问题记录](docs/debugging-issues-record.md)。

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

先配置数据库连接并启动：

```bash
workspace-gateway
```

在 `.env` 配置 PostgreSQL `GATEWAY_DATABASE_URL`，首次运行前创建数据库：

```bash
python scripts/init_database.py
```

服务监听端口、日志目录和 Workspace 代码目录统一在 `.env` 配置：

```dotenv
GATEWAY_PORT=8080
GATEWAY_LOG_DIR=./logs
GATEWAY_WORKSPACE_STORAGE_PATH=./data/workspaces
GATEWAY_SANDBOX_CODE_DIR=/workspace
```

运行日志写入 `GATEWAY_LOG_DIR/workspace-gateway.log`，单文件上限 10 MiB，保留
5 个轮转文件。`GATEWAY_WORKSPACE_STORAGE_PATH` 保存 Workspace 的项目代码和 Git
历史；`GATEWAY_SANDBOX_CODE_DIR` 则是代码同步进 Sandbox 后使用的远端执行路径。

后台启动和管理服务：

```bash
./scripts/start.sh
./scripts/start.sh restart
```

不传参数时默认执行 `start`。脚本只负责后台启动和重启，不解析 `.env`；端口、
日志目录、Workspace 目录和数据库连接均由应用启动后自行加载。进程 PID 保存到
项目根目录的 `.workspace-gateway.pid`，该文件已加入 `.gitignore`。

本地接口文档：

```text
http://127.0.0.1:8080/docs
```

管理控制台：

```text
http://127.0.0.1:8080/console
```

MCP Server：

```text
http://127.0.0.1:8080/mcp
```

支持 Streamable HTTP 的 Agent 或 MCP Client 可以直接连接该地址。设置了 `GATEWAY_API_KEY` 时，MCP Client 使用：

```text
Authorization: Bearer <gateway-api-key>
```

控制台提供：

- 使用中、运行中、暂停和 Provider 配置统计；
- 按 Gateway ID、Provider ID、模板和 Workspace 搜索；
- Provider 与状态筛选；
- 模板、超时、metadata、环境变量名称和生命周期详情；
- 创建、刷新状态、暂停和销毁操作；
- 展示沙箱应用访问地址，并通过 Gateway 的短期会话代理安全打开；
- 在 Provider 卡片中配置 PAI、E2B 或火山引擎，保存后立即生效；
- 独立的沙箱模板目录，维护 Provider、厂商 Template ID、默认超时和 MCP 默认模板；
- 15 秒自动更新；
- 移动端布局、系统深色模式和键盘焦点支持。
- Workspace 项目目录、文件查看编辑、Git 提交历史和一键同步运行。

## Workspace 项目流程

项目源码必须写入持久化 Workspace，不能把可销毁的 Sandbox 当作 Agent 工作区：

```text
创建 Workspace → 写入/查看文件 → 提交版本 → 同步到 Sandbox → 运行命令
```

Workspace API 使用真实 Git commit hash 标识版本。`POST /v1/workspaces/{id}/runs` 默认自动提交未保存修改、使用后台默认模板创建 Sandbox、同步源码并执行命令。Sandbox 销毁后，Workspace 代码和版本历史仍然保留。

本地代码仓库默认位于 `./data/workspaces`，可通过 `GATEWAY_WORKSPACE_STORAGE_PATH` 修改。生产环境必须将该目录放在持久卷或独立 Workspace/Git 服务中并配置备份。

点击任意 Provider 卡片中的“配置”即可填写接入参数。PAI 只需要 E2B-compatible Domain、API Token 和默认超时；Template ID 不属于 Provider 配置。配置写入 PostgreSQL 的 `provider_configurations` 表，并立即更新当前进程中的 Provider Adapter，无需重启。服务重启时会从数据库恢复配置。

Template ID 在独立的“沙箱模板”菜单维护。管理员可以将其中一个条目设为 MCP 默认模板；首次新增 `code-interpreter` 时会自动设为默认。Agent 调用 `workspace_run` 时不传 Provider 或 Template ID，Gateway 自动使用默认模板创建运行环境。

为了防止泄密，控制台和 Provider 查询接口不会展示或回传 Provider Token，也不会展示沙箱环境变量值。生产环境应限制数据库访问、启用磁盘/数据库加密，并最终使用 KMS 或 Secret Manager 封装数据库中的凭据。设置了 `GATEWAY_API_KEY` 时，可以在控制台左下角的连接设置中输入；它只保存在当前浏览器标签页的 `sessionStorage`。生产环境应同时启用 Gateway API Key 和 HTTPS。

## 最小调用流程

创建 Workspace：

```bash
curl -X POST http://127.0.0.1:8080/v1/workspaces \
  -H 'Content-Type: application/json' \
  -d '{"name":"Next.js Demo","description":"Agent project"}'
```

将响应中的 `ws_...` 作为 Workspace ID，后续所有源码读写都使用它：

```bash
curl -X PUT http://127.0.0.1:8080/v1/workspaces/<workspace-id>/file \
  -H 'Content-Type: application/json' \
  -d '{"path":"app.js","text":"console.log(42)"}'

curl -X POST http://127.0.0.1:8080/v1/workspaces/<workspace-id>/commits \
  -H 'Content-Type: application/json' \
  -d '{"message":"Add app"}'
```

运行时由 Gateway 使用后台默认模板创建 Sandbox，并同步准确的 Workspace 版本：

```bash
curl -X POST http://127.0.0.1:8080/v1/workspaces/<workspace-id>/runs \
  -H 'Content-Type: application/json' \
  -d '{"command":"node app.js","auto_commit":true}'
```

如果设置了 `GATEWAY_API_KEY`，所有 `/v1/*` 请求还需要：

```text
X-Gateway-API-Key: <gateway-api-key>
```

同一个密钥也用于 `/mcp`，但 MCP 推荐放在标准 `Authorization: Bearer` Header。Provider Token 始终只保存在 Gateway 内，不会下发给 Agent。

## 当前边界

- 命令接口是同步响应；流式日志和取消任务放在下一阶段。
- Preview 已支持带短期访问会话的 HTTP 反向代理，并在 Gateway 内注入 Provider Token；WebSocket 转发尚未实现。
- 正式运行使用 PostgreSQL；未设置 `GATEWAY_DATABASE_URL` 时仍可回退到 SQLite，主要用于测试。
- 火山引擎 REST 模式是明确的内部 Bridge Contract，不假设未核实的官方 API 路径。
- Provider 连接配置从数据库加载，Token 永远不通过读取接口返回；`.env` 只保存 Gateway 部署配置。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试使用 Fake Provider，不会创建云端资源。

真实 MCP 端到端验证会通过 `/mcp` 创建持久化 Workspace，并使用后台默认模板创建云端 Sandbox、同步 Git commit、执行 Node.js 代码：

```bash
python tests/manual/mcp_workspace_flow.py
```

该脚本从 `.env` 读取 Gateway 连接配置，Provider 配置由运行中的 Gateway 从数据库加载。脚本会创建真实云端资源，不属于默认自动化测试。
