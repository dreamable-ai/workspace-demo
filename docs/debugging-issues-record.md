# Workspace Gateway 联调问题记录

> 记录日期：2026-08-20  
> 覆盖范围：MCP Client、Workspace、PAI Sandbox、后台进程、Preview Proxy 及管理控制台

## 1. 本次验证结论

真实链路已经验证过以下主流程：

```text
MCP Client
  → workspace_create
  → workspace_write_file
  → workspace_commit
  → workspace_read_file(version)
  → workspace_run
  → PAI Sandbox 中执行 Node.js
  → sandbox_start_process
  → Gateway Preview Proxy
```

验证时 MCP Server 发布 14 个工具：8 个 Workspace 工具和 6 个 Sandbox Runtime 工具。源码由 Workspace 保存并使用 Git commit 标识版本；Sandbox 只保存运行副本。

本次结束时已销毁调试过程中仍处于运行状态的两个云沙箱。Gateway 中的沙箱映射记录保留并标记为 `terminated`，Workspace 源码和 Git 历史没有删除。

## 2. 问题摘要

| 编号 | 问题 | 直接表现 | 状态 |
|---|---|---|---|
| P01 | MCP SDK 误解析 JSON 文本 | 写入 `package.json` 报字符串校验错误 | 已修复并增加回归测试 |
| P02 | Provider 已回收但本地状态仍是 running | 命令返回 404/502 或笼统 Provider 错误 | 已识别，需增加 Reconciler |
| P03 | PAI Preview Token 选择错误 | 预览地址返回 401 | 已修复 Token 回退逻辑 |
| P04 | 有 Preview URL 但端口未监听 | 浏览器访问返回 502 | 已明确健康检查要求 |
| P05 | 后台进程与 Sandbox 状态不是一回事 | Sandbox running，但应用已经退出 | 部分处理，需增加进程状态/日志 |
| P06 | `*.localhost` 被内置浏览器拦截 | `ERR_BLOCKED_BY_CLIENT` | 已改为同源路径代理 |
| P07 | Preview Token 出现在 URL | URL 可能进入历史和日志 | 已用一次性查询参数换 HttpOnly Cookie |
| P08 | Preview 会话仅保存在内存 | Gateway 重启后旧链接失效 | 当前已知限制 |
| P09 | 代理响应头与重定向处理 | 页面乱码、长度错误或跳出代理 | 已处理主要 HTTP Header 和 Location |
| P10 | WebSocket/绝对静态路径未完全覆盖 | Next.js HMR 或 `/_next/*` 可能失败 | 待实现 |
| P11 | Provider 错误过度脱敏 | 只看到 `The sandbox provider operation failed` | 待增加错误编号和内部诊断信息 |

## 3. 详细问题与修复

### P01：`workspace_write_file` 写 JSON 文件失败

现象：MCP Client 把 `package.json` 作为字符串传入 `text`，服务端却把 JSON 正文预解析成对象，随后 Pydantic 报“应为字符串”。普通 JavaScript 文本不触发，因此问题容易被误判为单个文件内容错误。

原因：当前 MCP Python SDK 会根据函数注解决定是否对参数做 JSON 预解析。`text: str | None` 没有被识别为纯字符串参数，合法 JSON 文本因此先变成 `dict`。

当前修复：MCP 工具层将参数注解保持为精确的 `str`，默认值仍允许 `None`，并在函数内检查 `text` 与 `content_base64` 必须且只能提供一个。测试必须覆盖 JSON、中文、空字符串和 Base64 二进制内容。

防复发检查：

- 用真实 Streamable HTTP MCP Client 写入 `package.json`；
- 不只调用 Python 函数本身，因为该问题发生在 MCP 协议参数解析层；
- 升级 MCP SDK 时重新运行端到端脚本。

### P02：云端实例过期，本地仍显示运行中

现象：数据库记录为 `running`，但连接 Provider 或执行命令时返回找不到实例，最终被 Gateway 转成 502。

原因：云沙箱有 TTL，会被 Provider 自动回收；本地状态主要在创建、查询和操作时更新，当前没有后台 Reconciler 持续同步 Provider 状态。

处理建议：

1. 每次执行命令、启动进程和创建预览前刷新 Provider 状态；
2. 对 Provider 的“实例不存在/已过期”错误统一映射为 `terminated`；
3. 后台周期同步 `running/paused` 实例和过期时间；
4. 控制台显示“最后同步时间”，避免把数据库缓存状态当作实时状态。

### P03：PAI Preview 鉴权 Token 不一致

现象：SDK 能生成端口域名，但访问仍返回 401。只注入 `traffic_access_token` 无法工作。

原因：本次 PAI `code-interpreter` 实例实际需要 SDK 连接对象中的 envd access token；`traffic_access_token` 为空或不是该预览入口使用的凭据。

当前修复：PAI/E2B-compatible Adapter 优先使用明确的 traffic token，没有时回退到已连接 Sandbox 对象的 envd token；Gateway 在服务端向 Provider 请求注入 `X-Access-Token`，浏览器和 Agent 永远拿不到 Provider Token。

注意：envd token 属于 SDK 内部属性，升级 SDK 时需要回归验证。长期应优先使用 Provider 正式暴露的公开访问凭据接口。

### P04：能生成 URL 不代表应用已经可访问

现象：`get_host(3000)` 或 Gateway 可以生成 URL，但访问返回 502。

原因：端口路由只说明“这个端口可以被映射”，不说明沙箱内有进程监听。Next.js/Node.js 进程可能尚未启动、启动失败、只监听 `127.0.0.1`，或者已经退出。

正确顺序：

```text
sandbox_start_process
  → 绑定 0.0.0.0:<port>
  → 在沙箱内检查端口/HTTP 健康
  → sandbox_get_preview
  → 创建浏览器访问会话
```

Node/Next.js 示例必须显式绑定 `0.0.0.0`，例如：

```bash
npm run dev -- --hostname 0.0.0.0 --port 3000
```

### P05：Sandbox running 不代表后台应用 running

现象：Sandbox 生命周期仍是 `running`，但之前启动的 Web 进程已经退出，访问地址随即变成 502。

原因：目前 `sandbox_start_process` 返回启动结果，但管理层没有独立保存和轮询进程状态、退出码及日志。

后续改进：增加 `process_id`、状态查询、最近日志、退出码和健康检查；管理页把“沙箱运行中”和“应用可访问”分成两个状态，不要仅因存在 URL 就显示应用健康。

### P06：子域名 Preview 在内置浏览器中被拦截

现象：命令行通过自定义 DNS 解析可以访问 `preview-<id>.localhost`，内置浏览器却返回 `ERR_BLOCKED_BY_CLIENT`。

原因：浏览器环境对 localhost 子域名、DNS 解析或安全策略的处理与命令行不同。

当前修复：不再依赖 `*.localhost`，改用 Gateway 同源路径：

```text
/v1/sandboxes/{gateway_id}/proxy/{port}/
```

这样不需要本地 DNS、Hosts 文件或通配符证书。

### P07：Preview URL 中的短期 Token

风险：查询参数可能进入浏览器历史、Referrer、代理访问日志或截图。

当前流程：

1. 已鉴权用户调用 `POST /v1/sandboxes/{id}/preview/{port}/access`；
2. Gateway 生成 15 分钟短期 token；
3. 首次访问携带 token；
4. Gateway 将其换成 `HttpOnly`、`SameSite=Lax`、限定代理路径的 Cookie；
5. 立即 302 到不含 token 的干净 URL。

Provider Token 从不进入返回 URL、Cookie 或前端 JavaScript。

### P08：Gateway 重启后 Preview 链接失效

原因：短期 Preview Session 当前保存在进程内存。重启会清空它，即使云沙箱仍然运行，旧链接也会返回过期。

当前行为可接受于本地 POC。生产环境可选：

- Redis 保存短期会话并设置 TTL；
- 使用服务端签名、包含 Sandbox/端口/过期时间的短期 Token；
- 支持主动撤销并记录访问审计。

### P09：HTTP 代理 Header 和重定向

问题：上游响应经 HTTP 客户端解压后，如果继续透传原始 `Content-Encoding` 或 `Content-Length`，浏览器会错误解析；上游绝对 `Location` 也可能把用户带到 Provider 地址并绕过 Gateway。

当前处理：过滤 hop-by-hop Header、`Content-Encoding` 和 `Content-Length`，并对重定向目标做 Gateway 路径改写。后续仍需补充缓存策略、超大响应流式转发和上传大小限制。

### P10：WebSocket 与绝对静态资源路径

当前 Preview Proxy 只完整覆盖普通 HTTP。Next.js 开发模式的 HMR WebSocket 尚未转发；页面若输出 `/_next/*` 一类站点根绝对路径，浏览器可能请求 Gateway 根路径而不是当前 Sandbox 代理前缀。

生产方案应二选一：

- 首选独立 Preview 域名/通配符域名，Gateway 做 Host-based 路由；
- 路径代理继续使用时，为应用配置 `basePath`/资源前缀并实现 WebSocket Upgrade 转发。

### P11：错误信息过于笼统

现象：对外只返回 `The sandbox provider operation failed`，能防止泄露 Token 和 Provider 内部细节，但无法区分认证失败、实例过期、额度不足、模板错误和端口未监听。

改进方向：返回稳定的公开错误码和 `request_id`，例如 `PROVIDER_AUTH_FAILED`、`SANDBOX_EXPIRED`、`TEMPLATE_NOT_FOUND`、`PREVIEW_NOT_READY`；完整 Provider 错误只进入脱敏后的服务端日志，管理页按 `request_id` 查看诊断信息。

## 4. 推荐回归流程

每次修改 MCP、Provider SDK 或 Preview Proxy 后，按以下顺序验证：

1. `GET /health`；
2. MCP `list_tools` 恰好包含允许的 14 个工具；
3. 创建 Workspace，写入包含合法 JSON 的 `package.json`；
4. 提交后按 commit 读取文件，确认内容一致；
5. `workspace_run` 执行 `node --version` 和项目命令；
6. 确认运行记录同时包含 `workspace_id`、commit 和 `sandbox_id`；
7. 后台启动监听 `0.0.0.0` 的 HTTP 服务；
8. 先在沙箱内探活，再创建 Preview Access；
9. 验证浏览器 URL 不含 Provider Token，首次跳转后也不含短期 token；
10. 销毁 Sandbox，确认 Provider 实例不可连接且 Gateway 状态为 `terminated`；
11. 再次读取 Workspace commit，确认源码仍然存在。

真实云端回归会产生费用，完成后必须执行第 10 步。

## 5. 后续优先级

1. Provider 状态 Reconciler 与 TTL 到期映射；
2. 后台进程状态、日志和 HTTP 健康检查；
3. Preview WebSocket 与 Host-based 生产路由；
4. Preview Session 外部存储或签名令牌；
5. 分级错误码、request ID 和脱敏诊断日志；
6. 多租户 Workspace/Sandbox 所有权校验和审计。
