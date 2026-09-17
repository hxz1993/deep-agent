# DeepAgent 用户字段三方案

打开 `index.html`，切换 A/B/C、列表/卡片和列宽。悬停、点击或键盘聚焦用户可打开完整资料浮层，Esc 关闭。
仅使用虚构示例账号、`example.com` 邮箱和内嵌示例头像，不访问业务 API、不修改生产 Console。

## 字段核验

Atoms 源码：
- `design_UI/atoms/packages/api/src/types/common/user.ts`：`IUserListItemRes` 含 `userId/userName/nickName/avatar/email/color`，兼容 `username/nickname`。
- `design_UI/atoms/business/user-account/src/hooks/useUserCardHook.ts`：按 `showKeysMap` 展示“昵称 / 用户名”和“邮箱 / 手机号”；缺失字段用 `-`。
- 同目录 `view/UserInfoCard.tsx`、`.module.less`：圆形头像 16/24/32px，头像存在时背景图填充；无头像显示缩写（small 不显示文字），名称和联系字段单行省略。
- `utils.ts`：缩写优先由昵称、用户名、邮箱、手机号计算；中文取后两字、纯字母取前三字，颜色取配置色序号。

本次设计只取头像、昵称、用户名和邮箱，不展示手机号及企业/空间离开标记。

DeepAgent 当前 `security/diagnostic_users.py` 的归一化将 `userName/username` 与 `nickName/nickname`
折叠成 `display_name`（用户名优先）；`email` 和安全的 `avatar_url` 已单独保留。
后续实现需增加独立 `username/nickname`，沿用现有 `display_name` 兼容旧消费方；不要仅在前端猜拆昵称。
继续由后端一次批量补齐资料，不能每一行额外发一次用户名请求。
头像仍遵守后端现有 URL 安全校验；不得给外部头像 URL 附加平台 Token。

## 方案取舍

| 方案 | 常驻信息 | 场景 |
| --- | --- | --- |
| A 双行完整展示（推荐） | 32px 头像；昵称 / 用户名；邮箱 | 最接近 Atoms，适合现有双行会话列表 |
| B 单行 + 资料浮层 | 24px 头像与用户名；昵称、邮箱进浮层 | 列宽较窄、优先保持列表密度 |
| C 昵称突出展示 | 38px 头像；昵称、用户名、邮箱各一行 | 用户识别优先，适合卡片，列表行高相应增加 |

原型优化：缺少昵称时不保留多余分隔符；相同昵称和用户名去重；头像失败回退缩写；
缺邮箱显示“未设置邮箱”；未知用户继续保留 ID，不推断账号删除或离职。
所有字段截断只影响视觉，浮层保留完整文本。昵称为空时不会产生空白姓名行。

响应式：760px 以下方案选择纵排、卡片单列；列表保留横向滚动，不挤压用户字段。
列宽可在 200–360px 内预览，初始 286px。只有原型样式，没有新增生产断点。

## 方案 A 的局部确认（2026-09-17）

打开 `scenes.html`，同页确认会话列表、详情侧栏、左下角当前账号、概览单行署名、搜索过滤与渠道详情。
- 原始 User ID 保留；详情与渠道默认只显示展开图标，展开后在 ID 末尾显示复制按钮。详情用户字段保持左标签、右内容布局；账号弹层常驻 ID。
- 活跃用户只用用户名 / 昵称单行，不加头像、邮箱及 ID 操作图标。
- 最近提问沿用当前页面的 username 署名，不增加昵称或操作图标；无资料时仍以原始 ID 兜底。
- 资料缺失不推断删除；服务不可用也不显示为账号不存在。右上选择器可切换两种兜底状态。
- 搜索为离线样例交互，展示用户名、昵称、邮箱、User ID 候选；正式范围按接口契约确认，必须保留 UUID / 历史数字 ID。
- 姓名与邮箱为示例数据，不宣称与示例 ID 对应；缺失 UUID 使用用户指定值以方便验证。

只读核查：Test 会话 `4402a5e9b58a4fe184de5caceb12e930` 的归属为
`189faa0f-9f48-4731-a475-09c3f7580b98`。诊断用户搜索能命中，按 ID 过滤能查到 1 条会话。
System `/user/get-user-detail-list` 和 Space `/api/space/user-info/batch/query/by-user-ids`
用当前同一认证批量查询该 ID 与对照账号，都成功返回对照账号但没有目标 ID；不是本轮服务发现故障。
该会话渠道名称标注 cloud，System 的 `SysUserInfoCloud.getBasicUserInfoById` 又会附加部署 platformCode，
所以跨环境账号是待核实方向；未取得 Cloud 用户资料，不能断言账号删除或确认跨环境。
