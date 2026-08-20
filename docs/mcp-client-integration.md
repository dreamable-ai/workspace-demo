# MCP Client 接入 Workspace Gateway

## 1. 接入结论

Agent Backend 通过 Streamable HTTP 连接 Workspace Gateway：

```text
MCP URL: http://127.0.0.1:8080/mcp
Transport: Streamable HTTP
Authorization: Bearer <GATEWAY_API_KEY>
```

没有配置 `GATEWAY_API_KEY` 且 Gateway 明确允许不安全的本地访问时，可以省略 Authorization。公网环境必须使用 HTTPS、身份认证和租户级授权。

核心边界：MCP Client 把源码写入持久化 Workspace；只有构建、测试和启动时，`workspace_run` 才把确定的 Git commit 同步到临时 Sandbox。

```text
Agent
  ├─ Read / Write / Commit ──→ Workspace（长期代码资产）
  └─ Run / Process / Preview → Sandbox（临时运行副本）
```

## 2. 当前发布的工具

### Workspace：源码主接口

| 工具 | 作用 |
|---|---|
| `workspace_create` | 创建 Git-backed Workspace |
| `workspace_get` | 查看版本、dirty 状态和文件数 |
| `workspace_list_files` | 列出项目文件 |
| `workspace_read_file` | 读取工作树或指定 commit 文件 |
| `workspace_write_file` | 创建或覆盖源码文件 |
| `workspace_commit` | 提交并取得不可变 commit hash |
| `workspace_history` | 查看提交历史 |
| `workspace_run` | 同步版本到 Sandbox 并执行命令 |

### Sandbox Runtime：运行辅助接口

| 工具 | 作用 |
|---|---|
| `sandbox_get` | 查询一个已知 Gateway Sandbox |
| `sandbox_run_command` | 在已同步环境中执行额外命令 |
| `sandbox_start_process` | 启动 Web 服务等后台进程 |
| `sandbox_get_preview` | 取得端口的上游信息和 Gateway 代理路径 |
| `sandbox_pause` | 暂停实例 |
| `sandbox_kill` | 永久销毁实例，必须 `confirm=true` |

MCP 不发布 Provider 配置、模板查询、Sandbox 列表、直接创建 Sandbox、直接读写 Sandbox 文件等能力。默认 Provider 和模板由管理员预先在管理后台配置。

## 3. Python Client 依赖

项目已经包含依赖：

```toml
mcp = ">=2.0,<3"
httpx = ">=0.27,<1"
python-dotenv = ">=1.0,<2"
```

当前端到端脚本使用 MCP SDK 附带的 `httpx2` 兼容客户端。可直接运行项目中的：

```bash
python tests/manual/mcp_workspace_flow.py
```

该脚本会创建真实 Workspace 和云沙箱，可能产生费用。执行结束后应调用 `sandbox_kill(confirm=true)`，或在管理后台销毁测试沙箱。

## 4. 最小连接代码

```python
import asyncio
import os

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    url = os.getenv("WORKSPACE_GATEWAY_MCP_URL", "http://127.0.0.1:8080/mcp")
    api_key = os.getenv("GATEWAY_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with httpx2.AsyncClient(headers=headers) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(
            transport,
            mode="legacy",
            read_timeout_seconds=600,
            cache=None,
        ) as client:
            tools = await client.list_tools()
            print([tool.name for tool in tools.tools])


asyncio.run(main())
```

`mode="legacy"` 是当前服务端实际验证通过的 SDK 模式。升级 MCP SDK 或服务端协议实现时，应重新验证协商模式。

## 5. 标准 Workspace → Sandbox 调用流程

### 5.1 处理工具结果

```python
from typing import Any


def structured(result: Any) -> Any:
    if result.is_error:
        messages = [getattr(item, "text", str(item)) for item in result.content]
        raise RuntimeError("; ".join(messages))
    if result.structured_content is None:
        raise RuntimeError("MCP tool returned no structured content")
    return result.structured_content
```

### 5.2 创建 Workspace 并写代码

```python
workspace = structured(
    await client.call_tool(
        "workspace_create",
        {"name": "Node Demo", "description": "MCP client example"},
    )
)
workspace_id = workspace["id"]

await client.call_tool(
    "workspace_write_file",
    {
        "workspace_id": workspace_id,
        "path": "index.js",
        "text": "console.log('hello from sandbox')\n",
    },
)

await client.call_tool(
    "workspace_write_file",
    {
        "workspace_id": workspace_id,
        "path": "package.json",
        "text": """{
  "name": "node-demo",
  "private": true,
  "scripts": {"start": "node index.js"}
}""",
    },
)
```

`workspace_write_file` 必须且只能传 `text` 或 `content_base64` 之一。二进制文件使用 Base64；路径必须是 Workspace 内的相对路径。

### 5.3 提交并运行准确版本

```python
commit = structured(
    await client.call_tool(
        "workspace_commit",
        {"workspace_id": workspace_id, "message": "Add Node demo"},
    )
)
version = commit["version"]["version"]

run = structured(
    await client.call_tool(
        "workspace_run",
        {
            "workspace_id": workspace_id,
            "version": version,
            "command": "npm start",
            "auto_commit": False,
            "timeout_seconds": 300,
        },
    )
)

if run["result"]["exit_code"] != 0:
    raise RuntimeError(run["result"]["stderr"])

sandbox_id = run["sandbox"]["id"]
print(run["result"]["stdout"])
```

`workspace_run` 没有传 `sandbox_id` 时，Gateway 使用后台默认模板创建 Sandbox；传入先前返回的 Gateway Sandbox ID 时，会复用该运行环境并重新同步所选版本。

Client 应持久保存：

- `workspace_id`：后续会话继续编辑同一项目；
- `version`：运行、审计和复现所需的 Git commit；
- `sandbox_id`：仅用于当前临时运行环境。

### 5.4 启动 Web 服务和获取预览

```python
process = structured(
    await client.call_tool(
        "sandbox_start_process",
        {
            "gateway_id": sandbox_id,
            "command": "npm run dev -- --hostname 0.0.0.0 --port 3000",
            "cwd": "/workspace",
        },
    )
)

preview = structured(
    await client.call_tool(
        "sandbox_get_preview",
        {"gateway_id": sandbox_id, "port": 3000},
    )
)
print(preview["gateway_proxy_path"])
```

`sandbox_get_preview` 返回的是运行时预览元数据。给浏览器生成可点击的短期链接时，由已登录的业务后端调用：

```http
POST /v1/sandboxes/{sandbox_id}/preview/3000/access
Authorization: Bearer <GATEWAY_API_KEY>
```

返回的 `url` 经过 Gateway 代理；Provider Token 不会暴露给 Agent 或浏览器。应用必须实际监听 `0.0.0.0:3000`，仅生成 URL 不代表服务健康。

### 5.5 结束后销毁 Sandbox

```python
terminated = structured(
    await client.call_tool(
        "sandbox_kill",
        {"gateway_id": sandbox_id, "confirm": True},
    )
)
```

销毁是不可逆的，沙箱内未持久化的依赖、日志和运行产物会丢失；Workspace 源码与 Git 历史不受影响。业务系统应把清理放进 `finally`、任务回收器或 TTL 策略中。

## 6. Agent Backend 的建议调用策略

```text
收到用户编程请求
  → 找到或创建 workspace_id
  → list/read Workspace
  → write Workspace（多次）
  → commit
  → workspace_run(commit)
  → 根据 stdout/stderr 继续修改 Workspace
  → 新 commit，再次 workspace_run
  → 需要网页时启动后台进程和 Preview
  → 会话结束或超时后 kill Sandbox
```

不要把 `sandbox_run_command` 中通过 Shell 修改的文件当作项目源码，因为这些修改不会自动回写 Workspace。需要保留的代码必须由 Agent 再写入 Workspace 并提交。

## 7. 配置示意

不同 Agent 产品的 MCP 配置格式不同，核心字段保持一致：

```json
{
  "mcpServers": {
    "workspace-gateway": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:8080/mcp",
      "headers": {
        "Authorization": "Bearer ${GATEWAY_API_KEY}"
      }
    }
  }
}
```

这是通用结构示意，不应直接假定每个 Agent Client 都接受完全相同的字段名。部署时按该 Client 的配置格式映射 URL、Transport 和 Header。

## 8. 常见错误

| 现象 | 可能原因 | 排查 |
|---|---|---|
| MCP 连接返回 401 | Gateway Key 缺失或错误 | 检查 Bearer Header，不要传 Provider Token |
| `workspace_run` 提示没有默认模板 | 管理后台未设置 MCP 默认模板 | 配置 `code-interpreter` 等模板并设为默认 |
| `The sandbox provider operation failed` | Token、Domain、模板、TTL 或 Provider 实例状态异常 | 查看 Gateway 脱敏日志并刷新实例状态 |
| 写 `package.json` 报字符串校验错误 | MCP SDK 参数解析回归 | 确认服务端保留纯 `str` 注解并运行真实 MCP 测试 |
| 命令超时 | 安装依赖或构建超过默认 60 秒 | 为 `workspace_run` 设置合理 `timeout_seconds` |
| Preview 返回 502 | 端口没有监听、进程退出或只绑定 localhost | 在沙箱内探活并绑定 `0.0.0.0` |
| Preview 链接重启后失效 | 短期会话当前保存在 Gateway 内存 | 重新生成链接；生产环境接入 Redis/签名 Token |

## 9. 生产安全要求

- Gateway 对外使用 HTTPS；
- 使用 OAuth/OIDC 或短期 JWT 替代全局共享 Key；
- 服务端校验每个 `workspace_id`、`sandbox_id` 的 tenant/user 所有权；
- Provider Token 只保存在 Gateway 的密钥服务；
- 限制命令时长、输出、并发、网络、CPU、内存和沙箱 TTL；
- 保存 commit、运行记录、预览和销毁审计；
- 对 Workspace Git 数据做持久卷、备份和恢复演练；
- 在任务结束、空闲超时和费用阈值触发时自动销毁 Sandbox。

## 10. 参考实现

- [真实 MCP Workspace-to-Sandbox 验证脚本](../tests/manual/mcp_workspace_flow.py)
- [MCP 工具实现](../src/workspace_gateway/mcp_server.py)
- [Workspace-first MCP 技术方案](mcp-integration.md)
- [联调问题记录](debugging-issues-record.md)

