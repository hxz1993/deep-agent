# 测试去重与执行入口审计（2026-11-05）

> 发布状态：阶段性开发与验证记录随 v0.15.10 一并交付；当前发布门禁及结果以 [0.15.10 发布审计](2026-11-05-release-0.15.10-audit.md) 为准。下文的“未提交／版本不变”描述的是当时状态。

## 结果与边界

产品保持 0.15.9、数据库 013，依赖变更：无。只整理测试、执行脚本和指南；原有 Console 未提交改动保留，不提交、不推送。没有改生产 Python、迁移、现有本地数据库、会话或附件。

Python 从 3549 次收集变成 **3470 项**：消除 87 次重复执行，合并 2 项完全相同的用例，新增 10 项执行入口回归，净减 79 次。有效行为覆盖以逐 ID 清单核对，不按数量裁剪。

| 去重来源 | 减少 | 处理 |
| --- | ---: | --- |
| 渠道账号测试类重复导入 | 54 | 初始化移入非 TestCase helper；保留不同初始化下继承的行为 |
| 渠道网关测试类重复导入 | 25 | 抽出 fixture 与 Fake 类，消费者不再导入测试类 |
| 会话列表测试类重复导入 | 8 | 共用准备放入 fixture |
| session_resolve 的两个完全相同场景 | 1 | 保留 test_resolve_storage_ref_directly |
| sandbox 与 registry 重复的无 backend 断言 | 1 | 保留独立 registry 用例 |

三个 fixture 的 9 个方法、网关 5 个 Fake 类与原实现 AST 相同。渠道账号原 18 项行为继续在飞书、企微各自初始化下继承；两类注册场景共 48 项保留。函数式 load_tests 的 21 项保留。最终收集无重复 ID、无导入错误；新入口遇到重复 ID 报错，不静默丢弃。

## 执行入口与选测

默认 `.venv/bin/python scripts/run_tests.py` 完整运行 Python 与 Console。日常可以用 `--suite affected --base HEAD`；`--suite quick` 为显式纯逻辑组加 Console 全量，不能替代完整验收。`--list` 只显示选择原因；默认展示最慢 20 项，`--durations 0` 可关闭。

生产模块到测试组采用显式映射及 JSON 清单，多领域取并集；测试 helper 沿本地 import 反向传播。共享装配、数据库、依赖、测试入口、包初始化、删除／无法确定使用者的辅助模块、未知代码或契约资产回退全量。Console 改动仍跑全部 Console 测试。新入口正在修改的本轮工作区，因此真实 affected 会正确回退全量；下表单领域测时是固定模拟改动输入，不能误称本轮工作区只需这些测试。

审计修复了领域清单遗漏、包隐式初始化依赖和文档判断过宽的问题；内置 Skill 正文按生产资源处理，docs 下 Schema、JSON、流水线等未知资产不作为普通说明跳过。新增静态契约校验现有直接生产 import 的领域覆盖。完整选测不是形式化依赖证明，新增／移动相关测试仍需维护清单；无法确定时运行 all。

报告区分收集、执行、子测试、fixture 错误和 skip；JSON 不保存断言／异常或日志正文，默认不生成文件，显式报告限定在 Git 忽略的 output/TMP to delete... 目录。测试主进程及其子进程统一 src／仓库根导入路径，Sandbox Worker 仍由自己的受控环境构造，不因此开放宿主路径。标准入口与旧 unittest 命令均保留，后者统一 -t .。

## 实测结果

| 场景 | 数量 | 耗时 | 结果 |
| --- | ---: | ---: | --- |
| 改造前完整 Python | 3549 | 821.51s（含收集） | 1 failure、1 error、66 skip；两项均为退役 general 的陈旧测试 |
| 改造后完整 Python | 3470 | 737.35s（含收集） | 1 failure、0 error、66 skip；新入口未给测试子进程传入源码路径，随后已修 |
| 同轮 Console 全量 | 1085 | 3.96s | 全部通过 |
| 最后受影响定向回归 | 33 | 4.16s | 全部通过，含入口、Web 真实子进程和 Header 状态清理 |
| 新入口测试移入 integration 后 | 10 | 3.96s | 全部通过，含真实 spawn、Git 及子进程导入 |
| Console 改动选测 | 1085／次，2 次 | 中位数 3.55s | 全部通过 |
| Web 领域选测 | 413，1 次 | 51.47s | 全部通过；固定模拟 src/deep_agent/web/settings.py 改动 |
| 快速组 | Python 82 + Console 1085／次，2 次 | 中位数 5.95s | 全部通过 |

Console／领域／快速组的短场景按外层进程总耗时记录，包含解释器启动。

两项陈旧测试保留验证目的：输出工具重名使用仍可配置的 builtin:ptc，模块归属改为运行时 environment 入口。Header 用例补齐 OPTION 状态清理。新执行入口补齐 PYTHONPATH 的子进程传递及结束恢复，并用真实子进程回归；没有放宽异常解析的 10 秒上限。

修复后采用上述定向、领域和快速回归收尾，没有再次重复完整后端，也没有继续执行多轮全量／Web 测时。**不能将这次记录表述为最终版本全量零失败**；发布前仍使用 all 完整回归。全量前后计时各一次，且含上述修复差异，只作运行规模参考，不宣称去重本身带来固定百分比提速。主要提效来自按影响面运行较小测试组。

66 项 skip 包含未提供的真实 MySQL／PostgreSQL、外部发现服务／认证环境及 macOS 不支持的 Linux 限制，未算作实际验证通过。不增加依赖或扩大数据库验证范围。

## 使用与证据

```bash
.venv/bin/python scripts/run_tests.py --suite quick
.venv/bin/python scripts/run_tests.py --suite affected --base HEAD --list
.venv/bin/python scripts/run_tests.py --suite affected --base HEAD
.venv/bin/python scripts/run_tests.py --suite all
```

测试说明见 tests/README.md。逐 ID 基线、差异和耗时 JSON 留在忽略的 output/TMP-to-delete-test-efficiency/，均为可删除的本地验证依据；中间脚本和原始日志清理。没有引入并行执行、共享可变数据库或大范围目录搬迁。新入口含真实 Git／spawn 验证，归入 integration。
