# 业务 Skill、Python 与文件

## 业务Skill如何组织

```text
business-review/
├── SKILL.md
├── manifest.yaml
├── references/api.md
├── assets/report-template.html
└── scripts/
    ├── __init__.py
    ├── report.py
    └── helpers.py
```

SKILL.md Frontmatter至少包含name、description；正文写适用任务、执行顺序、输入输出、失败处理与何时读参考文档。大段规则放references，重复且确定的逻辑放Python脚本。一个业务ZIP只包含一个Skill，准确code用于替换与关联。

安装使用Console的Skill上传或管理员 `POST /api/v1/skills`；同code替换走 `PUT /api/v1/skills/{code}/package`。保存后核对manifest、资源、版本和Hash，再给Agent显式关联。上传Skill、关联Agent和测试运行是三个独立步骤。

Agent初始只看到目录信息，匹配时调用load_capability加载正文，再按需list_skill_resources/read_file读取脚本及参考。源码可读不等于可执行；执行仍需要Python能力。文本脚本如Python/JS/SQL可供读取，业务脚本执行目前只支持Python。

## 两种Python输入形式

```text
execute_python(code, inputs?, files?, include_context=False)
execute_skill_script(skill_code, script_path, arguments?, inputs?, resource_paths?, files?, include_context=False)
```

普通Python直接执行代码并提供`inputs`变量；Skill入口保留原路径，JSON输入从工作目录`inputs.json`读取，正式结果写`outputs/result.json`。这两种输入形式保持不同，不能套错。

```python
import json
from pathlib import Path
from deepagent_files import input_path, publish_path

values = json.loads(Path("inputs.json").read_text(encoding="utf-8"))
source = input_path(values["file_id"])
target = publish_path("review-report.html", mime="text/html")
Path(target).write_text("<h1>核验报告</h1>", encoding="utf-8")
Path("outputs/result.json").write_text(
    json.dumps({"status": "ready", "source_name": Path(source).name}, ensure_ascii=False),
    encoding="utf-8",
)
```

`files=[{"file_id":"<授权File ID>","path":"inputs/source.pdf"}]`让宿主下载已授权文件。input_path不读取任意宿主路径。publish_path只登记产物，宿主在执行成功后上传，工具顶层files返回真实File ID、名称、MIME、大小与摘要。没有上传成功不能声称有File ID；文件上传也不等于登记下载中心。

Skill执行目录为独立临时根，含skill/副本、inputs.json、inputs/和outputs/。工作目录不是Skill根；通过`__file__`定位资源。入口不改名，不要求main函数，同Skill Python模块自动携带，支持包内相对导入。非代码资源通过resource_paths明确选择，不开放跨Skill或宿主缓存。

## 用户资料：按需开启

只有脚本需要当前平台用户或App/Space时，提示模型调用执行工具时传`include_context=true`。默认false不查询也不注入。原生和PTC执行共享同一Run缓存。

```python
import json, os
context = json.loads(os.environ["DEEP_AGENT_CONTEXT"])
username = context["user"]["username"]
if not username:
    raise RuntimeError("需要当前用户名，请检查include_context和用户资料解析")
```

固定结构：

```json
{
  "user": {"id": "user-demo", "username": "demo", "nickname": null, "display_name": "demo", "email": null, "avatar_url": null},
  "app": "demo001", "space": "demo", "language": "zh-cn",
  "resolution": "resolved", "error": null
}
```

查询失败仍可执行：仅保留user.id，其他资料字段为空，resolution=unavailable，error为安全错误信息。同Run不反复请求失败服务。此变量没有Cookie、Token或API Key，也不授予平台内部接口调用权限。

固定服务密钥可由脚本自行读取随包.env；非代码资源需显式携带，不自动加载。真实.env随原包导出，不能把含密钥的包公开分享。调试固定账号应使用明确开关，关闭后只使用运行身份，缺必要字段则报错，不能静默冒充他人。

## PTC与限制

PTC可调用这两个已授权执行工具，工具返回result/files后汇总可信产物。PTC不能直接访问宿主环境或Skill目录；view_image不进入PTC，先生成/上传File ID，回外层查看。Skill限额、运行超时和预装依赖以目标版本工具说明为准，不能靠拆脚本绕过。

外部HTTP请求应有明确超时、有界轮询和业务状态检查。错误返回分类说明认证失败、网络失败、任务失败、返回结构不符或缺页；不造数、不把部分成功包装成完整成功。
