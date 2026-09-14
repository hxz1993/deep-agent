# DeepAgent 上游更新与渠道身份调研

核验日期 2026-09-13；产品 0.10.0 / test@4fd0bd7。只读调研，未升级包、修改配置或调用真实企微凭据。

- [Pydantic AI / Harness / Monty 更新评估](runtime-assessment.md)：实际依赖、上游稳定版、直接收益、兼容适配、潜力及暂缓项。
- [企微渠道 User ID 获取流程](channel-user-identity.md)：原始发送者、平台会话用户、身份转换与姓名/部门补全。
- `pydantic-ai-releases.json`、`pydantic-ai-harness-releases.json`、`monty-releases.json`：本次从 GitHub/PyPI 获取的版本之后发布清单和依赖元数据。
- `monty-unreleased.json`：0.0.23 之后、本次核验主干的未发布提交，不能当作已安装能力。

本目录为上述日期和提交的历史调研，版本、行号及“尚未实现”均指当时基线，不作为当前能力清单。后续已实现可选企微应用成员资料补全及渠道统计，当前行为见[企微应用指南](../../guides/wecom-applications.md)和[会话诊断指南](../../guides/diagnostic-sessions.md)。联合运行时升级建议仍以专项兼容验证与正式发布记录为准。
