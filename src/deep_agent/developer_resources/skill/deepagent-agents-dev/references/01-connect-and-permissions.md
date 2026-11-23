# 连接、授权与权限

## 先连接正确的服务

服务根地址包含实际网关前缀，例如：

```text
https://agent.example.com/deepfos-server/deep-agent-server1-0
```

所有地址都相对此根拼接。先读取 `/info`、`/openapi.json`，记录实际产品版本和接口；`/health` 正常只证明该健康检查通过，不证明模型、MCP或文件服务都可用。示例域名不是真实服务。

本机已运行的回环 local-dev 可能已注入开发身份。针对 `http://127.0.0.1:8000`，先直接读取 `/info` 和 `/api/v1/admin/access`；若已允许，不另造 Token、不伪造 Header。`localhost` 必须是 Coding 工具实际执行命令的本机，云端执行器不能直接访问用户电脑回环地址。

## 配置一次授权，按同一身份调用

优先复用人在目标平台已登录、已授权的账号凭据。通过平台认可的登录/授权流程取得凭据，并配置在 Coding 工具支持的私有环境、Secret 或仓库外请求配置中。若需要从浏览器取得请求配置，应由人在目标页面已有请求中核对实际认证方式，不把 Cookie/Token 截图或粘贴到聊天。

不同OP网关可能使用 Cookie、Token Header、Authorization 或其他既有认证机制。**没有一个可保证所有部署通用的Header名称**。照目标环境实际协议配置，不能把任何 Key 都改写为 `Bearer`，也不要用模型供应商 Key 访问平台管理接口。

若部署方已提供专用API Key或服务账号，可按其网关协议使用。当前 DeepAgent 没有一个通用的“开发Skill Key签发”界面；不要引导用户寻找不存在的按钮。专用账号也必须具备对应权限。

私有请求配置的示意格式（由 Coding 工具或项目自行读取，不是新增 DeepAgent 产品配置）：

```json
{
  "base_url": "https://agent.example.com/deepfos-server/deep-agent-server1-0",
  "headers": {"<目标网关要求的认证Header>": "<在私有配置中填写>"}
}
```

第一次连通后再做其他任务，后续沿用同一配置；失效或换环境时重新取得授权。不要将完整Headers返回模型，不把凭据写入Skill ZIP、Markdown、日志或提交文件。

## 普通用户与管理员

业务Chat、本人会话、附件等使用目标账号的业务权限。Agent/模型/MCP/Skill管理及跨用户诊断需要管理员权限。已登录不等于管理员；跨用户诊断的 `user_id` 只是筛选条件，不能据此换成被查用户身份。

```text
GET /api/v1/admin/access
```

先按目标OpenAPI验证接口存在，再查看是否允许管理。401表示缺失/失效认证，403表示权限不足；平台权限服务异常可能返回503，应保留错误，不冒充“没有会话”。可信身份应由网关处理，不手工伪造 `user`、用户ID或管理员标记。

App/Space是业务范围，不能替代认证。浏览器客户端沿用宿主有效范围与现有请求层；Console的调试App用于联调，和会话列表的App过滤不是同一个操作。

## 只读探针示例

下面使用Python标准库读取私有配置。先替换配置路径；示例不打印Headers，也不建立新的授权机制：

```python
import json
from pathlib import Path
from urllib.request import Request, urlopen

settings = json.loads(Path("<仓库外私有请求配置.json>").read_text())
url = settings["base_url"].rstrip("/") + "/info"
request = Request(url, headers=settings["headers"], method="GET")
with urlopen(request, timeout=15) as response:
    info = json.load(response)
print({"name": info.get("name"), "version": info.get("version")})
```

后续示例中的 `BASE` 指这个服务根。不要把凭据拼进URL查询参数。重定向跨主机时不自动转发凭据；工具的HTTP重定向策略也要核对。
