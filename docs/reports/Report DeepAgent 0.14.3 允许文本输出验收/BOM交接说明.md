# DeepAgent 0.14.3：BOM开发会话知悉说明

本说明供提交、推送和Tag完成后交接。**AlphaPG尚未发布，当前只理解变更并评估，不修改BOM代码、Agent配置、Schema、Prompt或Skill。用户发布后再另行安排。**

本次对既有严格JSON客户端的默认行为改动很小：不勾选新选项即可保持原模式。请结合BOM当前流程判断是否需要允许普通文字结束，以及若勾选，现有提示词或Skill中“必须输出JSON”等指令是否需要协调；先给出建议及依据，不实施修改、不提前启用。Schema约束适合继续留在Schema，业务校验、人审和保存职责不因该选项改变。

## 平台变化

结构化输出总开关仍默认false。开启后新增二级“允许文本输出”（allow_text_output），默认false：

| 配置 | 结束方式 |
| --- | --- |
| 结构化关闭 | 原普通文本／工具／Deferred |
| 结构化开启，允许文本false | 原生JSON Output Tool／Deferred，仍严格 |
| 结构化开启，允许文本true | 普通文本或原生JSON Output Tool／Deferred |

字段“必须”仍只维护JSON Schema required，跟是否允许普通文本无关。BOM默认继续false；本次不改bom_result业务包、基线／hash／revision、来源校验、人审和业务保存逻辑。

## 配置接口与存储

POST /api/v1/agents、PATCH /api/v1/agents/{id}继续一次原子提交structured_output_enabled、allow_text_output、output_config。output_config仍为name／description／json_schema；allow_text_output对外仍是顶层boolean。PATCH省略保持，null422。管理员GET /api/v1/agents/{id}/output-config返回两开关及完整定义；普通详情只返回bool，不返回管理Schema。

数据库013→012：总开关在agents；agent_output_configs按agent_id逻辑1:1，name、description、allow_text_output为独立列，字段完整定义仍放json_schema JSON。不新增结果表或正常Run表。关闭父开关保留草稿；无配置记录按false；删除定义同时清除策略，不能没有定义单独保存true。012历史迁移保持不变。

## SSE／HTTP／History对接

JSON分支不变：output事件accepted:true及最终output_info指向同一output_id，HTTP／final.output为原生JSON。History kind:structured_output的item_id等于output_id；Live原样包装。按output_id去重，不能把output和final重复采用。

允许的文本结束没有output_info、成功output事件或结构化接受事实，沿用text／final及assistant_message。必须消费完整事件流，不能见首个text就提前停止，后面仍可能有工具／Deferred或JSON调用。Schema可以返回字符串，不能按typeof output或正文像不像JSON判断；用output_info／History kind识别真实分支。

同响应文字伴随合法输出调用时，文字为过程说明，JSON为唯一接受结果。混合模式文字在完整模型响应检查并保存后再发布既有text事件，以隔离稍后被拒的候选；关闭结构化路径实时增量不变。失败候选仅在轨迹，不作为业务结果。BOM客户端即使收到普通文字也不能将其当作业务数据采用。

Schema和文本策略随Turn冻结，历史缺新字段按false；暂停后改配置不会改变在途Turn，恢复不重置纠错及请求预算。JSON仍完整Schema校验，每Turn最多两次格式修正；普通/MCP/UI工具混调或多个输出调用在宿主分发前拒绝。

## 安装与发布

包仍整数schema_version:1、一个正式导出入口。关闭结构化输出时省略所有新增输出键及关闭草稿，因此可安装到0.14前旧版；开启包旧版拒绝零写入。开启包只在允许文本时携带allow_text_output:true，缺省false；旧包新建或覆盖新版时两开关重置false。策略变化会使旧预检摘要失效。配置备份按同Revision恢复并保留草稿。

0.14.3需要数据库013与服务源码／Console一起发布；无生产依赖变更。不代表AlphaPG已上线。完整证据见同目录验收报告，最终提交SHA及Tag以平台交付消息为准。
