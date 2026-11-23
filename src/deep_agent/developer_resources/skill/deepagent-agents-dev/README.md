# 安装 DeepAgent 开发 Skill

本包供 Coding Agent 配置、开发和排查 DeepAgent 业务助手。它安装在 Coding 工具中，不是上传到 DeepAgent 业务 Skill 列表的安装包。

## 安装与连接

1. 解压 ZIP，保留 `deepagent-agents-dev/` 目录结构。通过当前 Coding 工具的本地 Skill 导入功能安装；若该工具使用文件目录，放入它实际支持的项目级或个人 Skill 目录。不要覆盖其他 Skill，同名更新先核对已有版本与本次更新范围。
2. 让 Coding Agent 读取 [SKILL.md](SKILL.md)，再按当前任务读取对应资料，不一次加载全部参考。安装不会执行包内脚本，也不会自动修改 DeepAgent 配置。
3. 使用用户提供的服务根地址（包含网关前缀），读取 `/info`、`/openapi.json` 和 `/api/v1/admin/access`，确认版本、接口与权限。资料基线为 **{{DEEP_AGENT_VERSION}} / 数据库 016**，目标环境以实际返回为准。
4. 沿用 Coding 工具私有配置中的平台授权。需要补充凭据时，让用户在私有配置入口填写；不要将 Cookie、Token 或 Key 放进对话、提示词、Skill 或源码。401/403 先处理认证/权限；具体方式见[连接与授权](references/01-connect-and-permissions.md)。本机 trusted local-dev 已允许访问时无需额外造 Token；云端执行器不能把 `localhost` 当作用户电脑。

## 完成任务

按用户指定范围工作：配置通过正式 API 读取、最小修改并回读；排障默认只读。涉及源码时才读源码开发章节，保留无关改动和原有 Agent、会话及附件。

验证应覆盖实际结果：配置看回读，运行看最终状态与轨迹，文件实际下载，业务保存另行回读。汇报目标版本、变更、可查看入口及未验证项，不将“连接成功”当作业务验收通过。临时证据放私有目录并标记 `TMP to delete`。

## 包内资料

- `SKILL.md`：业务任务与源码开发的入口。
- `references/` 下的编号指南覆盖配置、接口、Skill、结构化流程、电子表格、会话诊断及发布与远程 Agent，无需取得框架源码。
- `references/source/`：有对应源码时使用的后端、Console 和业务客户端专题参考。
- `scripts/`：按诊断任务选用的运行环境与 SSE 汇总脚本。

详细内容按需读取。离线资料不自动更新；产品升级后从目标环境重新下载配套包。
