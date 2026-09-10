/**
 * Browser-only synthetic API responses for visual review.
 * This file is never imported by Console or the server. No credentials,
 * production identities, external pictures, or real conversation text are used.
 */
export const FIXTURE_START = "2026-09-02T16:00:00.000Z";
export const FIXTURE_END = "2026-09-09T16:00:00.000Z";
export const FIXTURE_NOW = "2026-09-09T16:00:00.000Z";
export const LOG_START = "2026-09-08T16:00:00.000Z";
export const TIMEZONE = "Asia/Shanghai";

export const agents = [
  { id: "visual-agent-chat", name: "企微助手" },
  { id: "visual-agent-report", name: "报表分析" },
  { id: "visual-agent-knowledge", name: "知识库助手" },
];
const names = ["chenliang", "wangwei", "lin.yu", "zhaoliu", "chenqi"];
export const users = Object.fromEntries(Array.from({ length: 24 }, (_, index) => {
  const id = `visual-user-${String(index + 1).padStart(2, "0")}`;
  return [id, { user_id: id, display_name: names[index] || `review.user${index + 1}`, email: `fixture${index + 1}@invalid.example`, avatar_url: "https://invalid.example/avatar.png", resolution: "resolved" }];
}));
const userIds = Object.keys(users);
const titles = [
  "帮助我分析本月费用与预算的差异", "查询华东区域第三季度收入", "这份报表中的毛利率如何计算？",
  "整理最近一周的销售数据", "生成产品功能对比表", "分析用户反馈的主要问题", "制定下季度的市场推广计划",
  "查询合同管理相关流程", "需要一份竞品分析的摘要", "汇总本月项目回款进度", "核对费用报销的审批规则", "查看最新产品使用说明",
];
const summaries = [
  "本月各部门的费用支出是多少？有哪些超支项目？", "华东区域的收入趋势如何？同比增长了多少？", "毛利率的计算公式是什么？请用示例说明。",
  "请汇总上周的销售额并按地区分类。", "请对比旗舰版与标准版的功能差异。", "用户反馈中出现频率最高的问题有哪些？",
  "请基于历史数据给出下季度的推广建议。", "合同从创建到归档的流程是怎样的？", "请总结主要竞品的核心优势与劣势。",
  "请按项目汇总本月的回款金额。", "报销金额超过预算时需要谁审批？", "请整理产品的常见操作步骤。",
];
const statuses = ["completed", "running", "completed", "error", "completed", "waiting", "running", "completed", "completed", "stopped", "unknown", "not_started"];
export const sessions = Array.from({ length: 128 }, (_, index) => {
  // First 102 sessions have four questions, the remainder three. This produces
  // a meaningful 286 / 124 / 76 distribution while retaining real filter math.
  const agentIndex = index < 12 ? [1, 0, 2, 1, 2, 0][index % 6]
    : index < 102 ? (index < 69 ? 0 : index < 93 ? 1 : 2)
      : (index < 116 ? 0 : index < 120 ? 1 : 2);
  const agent = agents[agentIndex];
  return {
    session_id: `visual-session-${String(index + 1).padStart(3, "0")}-0000000000000000`,
    title: titles[index % titles.length], agent_id: agent.id, agent_name: agent.name,
    user_id: userIds[index % userIds.length], source: index % 3 === 1 ? "channel" : "direct",
    latest_status: statuses[index % statuses.length], created_at: FIXTURE_START,
    updated_at: new Date(Date.parse(FIXTURE_END) - (index + 1) * 13 * 60_000).toISOString(),
    run_id: `visual-run-${String(index + 1).padStart(3, "0")}`,
  };
});

const dailyCounts = [35, 47, 64, 50, 88, 77, 125];
export const questions = dailyCounts.flatMap((count, day) => Array.from({ length: count }, (_, index) => {
  const ordinal = dailyCounts.slice(0, day).reduce((sum, n) => sum + n, 0) + index;
  const session = sessions[(485 - ordinal) % sessions.length];
  const created_at = new Date(Date.parse(FIXTURE_START) + day * 86400_000 + 8 * 3600_000 + index * 30_000).toISOString();
  return { ...session, summary: titles[(485 - ordinal) % titles.length], created_at, event_id: `visual-question-${ordinal + 1}` };
}));
for (const session of sessions) {
  const latest = questions.filter(item => item.session_id === session.session_id).at(-1);
  session.recent_question = latest;
  session.last_activity_at = latest.created_at;
  session.updated_at = latest.created_at;
}
const errors = sessions.filter(item => item.latest_status === "error").slice(0, 6).map((session, index) => ({
  ...session, event_id: `visual-error-${index + 1}`, created_at: session.last_activity_at,
  message: index % 2 ? "工具执行失败，返回结果为空" : "模型请求超时", code: "fixture_execution_error",
}));
const inRange = (time, params) => (!params.get("start") || Date.parse(time) >= Date.parse(params.get("start"))) && (!params.get("end") || Date.parse(time) < Date.parse(params.get("end")));
const matches = (item, params) => ["agent_id", "user_id", "source"].every(key => !params.get(key) || item[key] === params.get(key));
const range = params => ({ start: params.get("start") || FIXTURE_START, end: params.get("end") || FIXTURE_END, timezone: params.get("timezone") || TIMEZONE });
const recent = (items, key = "created_at") => [...items].sort((a, b) => Date.parse(b[key]) - Date.parse(a[key]) || a.session_id.localeCompare(b.session_id));
const userMap = items => Object.fromEntries([...new Set(items.map(item => item.user_id))].map(id => [id, users[id]]));
const aggregate = (items, key) => [...items.reduce((map, item) => map.set(item[key], (map.get(item[key]) || 0) + 1), new Map())].sort((a, b) => b[1] - a[1]);

function overview(params) {
  const scoped = questions.filter(item => matches(item, params) && inRange(item.created_at, params));
  const scopedErrors = errors.filter(item => matches(item, params) && inRange(item.created_at, params));
  const activeSessions = sessions.filter(item => matches(item, params) && (scoped.some(q => q.session_id === item.session_id) || inRange(item.created_at, params)));
  const start = Date.parse(params.get("start") || FIXTURE_START), end = Date.parse(params.get("end") || FIXTURE_END);
  const dayStart = Math.floor((start + 8 * 3600_000) / 86400_000) * 86400_000 - 8 * 3600_000;
  const trend = [];
  for (let time = dayStart; time < end && trend.length < 32; time += 86400_000) {
    const from = Math.max(start, time), to = Math.min(end, time + 86400_000);
    trend.push({ date: new Date(time + 8 * 3600_000).toISOString().slice(0, 10), start: new Date(from).toISOString(), end: new Date(to).toISOString(), question_count: scoped.filter(item => Date.parse(item.created_at) >= from && Date.parse(item.created_at) < to).length });
  }
  return {
    range: range(params), generated_at: FIXTURE_NOW,
    metrics: { session_count: activeSessions.length, question_count: scoped.length, active_user_count: new Set(scoped.map(item => item.user_id)).size, error_session_count: new Set(scopedErrors.map(item => item.session_id)).size },
    trend, agents: aggregate(scoped, "agent_id").map(([agent_id, question_count]) => ({ agent_id, agent_name: agents.find(agent => agent.id === agent_id).name, question_count })),
    agent_options: agents.map(agent => ({ agent_id: agent.id, agent_name: agent.name })),
    recent_questions: recent(scoped).slice(0, 10), recent_errors: recent(scopedErrors).slice(0, 5),
    active_users: aggregate(scoped, "user_id").slice(0, 20).map(([user_id, question_count]) => ({ user_id, question_count })),
    users: userMap(scoped), users_status: "resolved",
  };
}
function listing(params) {
  const query = (params.get("query") || "").toLowerCase();
  const selected = sessions.filter(session => {
    const qs = questions.filter(item => item.session_id === session.session_id && inRange(item.created_at, params));
    const errs = errors.filter(item => item.session_id === session.session_id && inRange(item.created_at, params));
    return matches(session, params) && (qs.length || errs.length || inRange(session.created_at, params))
      && (!query || session.title.toLowerCase().includes(query) || session.session_id.toLowerCase() === query)
      && (params.get("has_questions") !== "true" || qs.length > 0)
      && (params.get("has_execution_error") !== "true" || errs.length > 0)
      && (!params.get("latest_status") || session.latest_status === params.get("latest_status"));
  });
  const limit = Math.min(200, Math.max(1, Number(params.get("limit") || 50))), offset = Math.max(0, Number(params.get("offset") || 0));
  const items = recent(selected, "last_activity_at").slice(offset, offset + limit);
  return { items, total: selected.length, limit, offset, range: range(params), users: userMap(items), agent_options: agents.map(agent => ({ agent_id: agent.id, agent_name: agent.name })) };
}

export const selectedLog = {
  timestamp: "2026-09-09T06:32:20.000Z", level: "ERROR", source: "api", event: "model.request_failed", summary: "模型请求超时",
  record_id: "visual-log-11", boot_id: "visual-boot-0001", instance_id: "visual-local", process_id: 9001,
  session_id: sessions[0].session_id, run_id: sessions[0].run_id, model_name: "deepseek-v4.1-flash", duration_ms: 30000, attempt: 2, reason: "请求超时，请稍后重试。",
};
export const logRows = Array.from({ length: 240 }, (_, index) => index === 10 ? selectedLog : {
  timestamp: new Date(Date.parse("2026-09-09T06:22:11.000Z") + index * 79_000).toISOString(),
  level: index === 5 ? "WARNING" : index === 18 ? "ERROR" : "INFO", source: index % 3 === 2 ? "gateway" : "api",
  event: index % 3 === 0 ? "request.received" : index % 3 === 1 ? "model.request_completed" : "tool.completed",
  summary: index === 5 ? "工具执行完成，但返回结果为空" : index === 18 ? "模型请求超时" : ["收到用户消息，开始处理", "模型请求已完成", "工具执行完成"][index % 3],
  record_id: `visual-log-${index + 1}`, boot_id: "visual-boot-0001", instance_id: "visual-local", process_id: 9001,
  session_id: sessions[Math.floor(index / 3) % sessions.length].session_id, run_id: sessions[Math.floor(index / 3) % sessions.length].run_id,
});
function logs(params) {
  const filtered = logRows.filter(item => inRange(item.timestamp, params) && ["source", "level", "session_id", "run_id"].every(key => !params.get(key) || item[key] === params.get(key)) && (!params.get("q") || `${item.summary} ${item.event}`.includes(params.get("q"))));
  const offset = Number(params.get("cursor") || 0), limit = Number(params.get("limit") || 200);
  return { instance_id: "visual-local", items: filtered.slice(offset, offset + limit), next_cursor: offset + limit < filtered.length ? String(offset + limit) : null, partial: false, range: { start: LOG_START, end: FIXTURE_END } };
}
function detail(session) {
  return { ...session, users: { [session.user_id]: users[session.user_id] }, app_id: "visualapp001", space_id: "visualapp", conversation_id: `visual-conversation-${session.session_id}`, turn_count: 1, message_count: 2, attachment_count: 0, model_name: "synthetic-review-model", runs: [{ run_id: session.run_id, status: "completed", model_name: "synthetic-review-model", usage: { input_tokens: 123, output_tokens: 56, total_tokens: 179 } }], context_error: "视觉夹具不进行上下文计算" };
}
function history(session) {
  const question = session.recent_question;
  return { schema_version: 1, session_id: session.session_id, event_head_sequence: 2, items: [
    { item_id: `message:${question.event_id}`, kind: "user_message", text: question.summary, created_at: question.created_at, order: { sequence: 1, part_offset: 0 }, source_event_ids: [question.event_id], attachments: [], context_refs: [] },
    { item_id: `assistant:${session.session_id}`, kind: "assistant_message", phase: "final", status: "completed", text: "这是一条仅用于浏览器视觉验收的合成回复，不对应真实业务数据。\n\n已保留现有正文、轨迹与右侧信息栏的只读布局。", created_at: question.created_at, updated_at: question.created_at, order: { sequence: 2, part_offset: 0 }, source_event_ids: [`visual-answer-${session.session_id}`] },
  ] };
}
function events(session) {
  return { session_id: session.session_id, items: [{ event_id: session.recent_question.event_id, session_id: session.session_id, run_id: session.run_id, sequence: 1, event_type: "user/message", created_at: session.recent_question.created_at, status: "completed", payload: { message: { kind: "request", parts: [{ part_kind: "user-prompt", content: session.recent_question.summary }] }, attachments: [], context_refs: [] } }], next_after_sequence: null };
}

/** Return undefined only for non-API/static resources. Unknown API GETs stay mocked. */
export function fixtureResponse(url) {
  const { pathname: path, searchParams: params } = new URL(url);
  if (path === "/info") return { version: "0.8.6" };
  if (path === "/health") return { status: "ok" };
  if (!path.startsWith("/api/")) return undefined;
  if (path === "/api/v1/request-context") return { user: "visual-review-admin", app: null, space: null, language: "zh-cn" };
  if (path === "/api/v1/current-user") return { user_id: "visual-review-admin", username: "视觉验收" };
  if (path === "/api/v1/admin/access") return { allowed: true, roles: ["enterprise_admin"] };
  if (path === "/api/v1/diagnostics/logs/status") return { enabled: true, available: true, instance_id: "visual-local", retention_days: 7, sources: ["api", "gateway"], issues: [] };
  if (path === "/api/v1/diagnostics/logs") return logs(params);
  if (path === "/api/v1/diagnostics/sessions/overview") return overview(params);
  if (path === "/api/v1/diagnostics/sessions") return listing(params);
  const sessionPath = path.match(/^\/api\/v1\/diagnostics\/sessions\/([^/]+)(?:\/(history|events))?$/);
  if (sessionPath) {
    const session = sessions.find(item => item.session_id === decodeURIComponent(sessionPath[1]));
    if (!session) return { __httpStatus: 404, detail: "合成会话不存在" };
    return sessionPath[2] === "history" ? history(session) : sessionPath[2] === "events" ? events(session) : detail(session);
  }
  if (path === "/api/v1/agents") return agents.map(agent => ({ ...agent, capabilities: [], skills: [], model_id: null }));
  if (path === "/api/v1/sessions") return [];
  if (path === "/api/v1/uploads/capabilities") return { kinds: [], allowed_mimes: [], max_count: 10, max_size_mb: 20 };
  if (path === "/api/v1/database-info") return { configured: true, engine: "sqlite", revision: "002", expected_revision: "002", ready: true };
  if (path === "/api/v1/channel-gateway/status") return { running: false, enabled: false, accounts: [] };
  if (path === "/api/v1/global-settings") return {};
  return [];
}
