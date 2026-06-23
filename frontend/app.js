"use strict";

/* ------------------------------------------------------------------ *
 * deep-agent 前端 —— 适配 FastAPI /api/v1/chat/stream (SSE over POST)
 * 协议详见 docs/sse-protocol.md
 *
 * 后端 SSE 事件 (event: <type>, data: <json>):
 *   session      -> { session_id, agent_id }
 *   text         -> { type:"text", delta }
 *   thinking     -> { type:"thinking", delta }
 *   tool_call    -> { type:"tool_call", tool, args, tool_call_id }  // 同 id 可能多次，需 upsert
 *   tool_result  -> { type:"tool_result", tool, result, tool_call_id }
 *   final        -> { type:"final", status, output, usage, deferred? }
 *   error        -> { type:"error", message }
 * ------------------------------------------------------------------ */

function defaultBaseUrl() {
  if (location.protocol === "file:") return "http://localhost:8000";
  const root = window.__DEEP_AGENT_APP_ROOT__ || window.__DEEP_AGENT_BASE__ || "/";
  return `${location.origin}${root.replace(/\/$/, "")}`;
}

const DEFAULT_BASE_URL = defaultBaseUrl();

const UI_TOOLS = new Set(["confirm_action", "ask_user_choice"]);

const state = {
  baseUrl: localStorage.getItem("da_base_url") || DEFAULT_BASE_URL,
  agentId: localStorage.getItem("da_agent_id") || "",
  agents: [],
  sessionId: null,
  sessions: JSON.parse(localStorage.getItem("da_sessions") || "[]"), // [{id, agentId, agentName, title}]
  streaming: false,
  awaitingHitl: false,
  abortController: null,
};

/* ----------------------------- DOM ----------------------------- */
const $ = (id) => document.getElementById(id);
const el = {
  sidebar: $("sidebar"),
  messages: $("messages"),
  emptyState: $("emptyState"),
  input: $("input"),
  sendBtn: $("sendBtn"),
  stopBtn: $("stopBtn"),
  sessionList: $("sessionList"),
  capList: $("capList"),
  baseUrlInput: $("baseUrlInput"),
  agentSelect: $("agentSelect"),
  connStatus: $("connStatus"),
  sessionBadge: $("sessionBadge"),
  chatTitle: $("chatTitle"),
  usageInfo: $("usageInfo"),
};

/* --------------------------- 初始化 --------------------------- */
function updateAdminLink() {
  const link = $("adminLink");
  if (link) link.href = `${state.baseUrl}/admin/`;
}

/** Normalize local / API session records to display id + agentId. */
function normalizeSession(session) {
  let id = session.id;
  let agentId = session.agentId || "";
  const colon = id.indexOf(":");
  if (colon > 0) {
    const prefix = id.slice(0, colon);
    const suffix = id.slice(colon + 1);
    if (!agentId || agentId === prefix) {
      agentId = prefix;
      id = suffix;
    }
  }
  return { ...session, id, agentId };
}

function sessionApiUrl(session, suffix = "") {
  const s = normalizeSession(session);
  const params = new URLSearchParams();
  if (s.agentId) params.set("agent_id", s.agentId);
  const qs = params.toString();
  return `${state.baseUrl}/api/v1/sessions/${encodeURIComponent(s.id)}${suffix}${qs ? `?${qs}` : ""}`;
}

function sessionLookupKey(session) {
  const s = normalizeSession(session);
  return `${s.agentId || ""}:${s.id}`;
}

function sessionSortTime(session) {
  return Date.parse(session.updatedAt || session.createdAt || 0) || 0;
}

function sortSessions() {
  state.sessions.sort((a, b) => sessionSortTime(b) - sessionSortTime(a));
}

function agentNameById(agentId) {
  const found = state.agents.find((a) => a.id === agentId);
  return found ? found.name : agentId;
}

function defaultAgentId() {
  if (!state.agents.length) return "";
  const named = state.agents.find((a) => a.name === "default");
  return named ? named.id : state.agents[0].id;
}

function ensureAgentSelected() {
  const valid = state.agentId && state.agents.some((a) => a.id === state.agentId);
  if (!valid && state.agents.length) {
    state.agentId = defaultAgentId();
    localStorage.setItem("da_agent_id", state.agentId);
  }
  if (el.agentSelect && state.agentId) el.agentSelect.value = state.agentId;
  return !!state.agentId;
}

function generateSessionId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return Math.random().toString(16).slice(2) + Date.now().toString(16);
}

function init() {
  el.baseUrlInput.value = state.baseUrl;
  updateAdminLink();

  const agentFromUrl = new URLSearchParams(location.search).get("agent_id");
  if (agentFromUrl) state.agentId = agentFromUrl;

  el.baseUrlInput.addEventListener("change", () => {
    state.baseUrl = el.baseUrlInput.value.trim().replace(/\/$/, "") || DEFAULT_BASE_URL;
    localStorage.setItem("da_base_url", state.baseUrl);
    updateAdminLink();
    checkHealth();
    loadCapabilities();
    loadAgents();
  });
  el.agentSelect?.addEventListener("change", () => {
    state.agentId = el.agentSelect.value;
    localStorage.setItem("da_agent_id", state.agentId);
    updateChatHeader();
  });

  $("newChatBtn").addEventListener("click", newChat);
  $("newChatBtnWide").addEventListener("click", newChat);
  $("toggleSidebar").addEventListener("click", () => el.sidebar.classList.toggle("collapsed"));
  $("refreshCapsBtn").addEventListener("click", loadCapabilities);
  el.sendBtn.addEventListener("click", onSend);
  el.stopBtn.addEventListener("click", stopStream);

  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  });
  el.input.addEventListener("input", autoGrow);

  document.querySelectorAll(".suggestion").forEach((b) =>
    b.addEventListener("click", () => {
      el.input.value = b.textContent;
      autoGrow();
      onSend();
    })
  );

  initMarkdown();
  state.sessions = state.sessions.map((s) => normalizeSession(s));
  sortSessions();
  persistSessions();
  renderSessions();
  syncSessionsFromBackend();
  checkHealth();
  loadCapabilities();
  loadAgents().then(() => {
    if (agentFromUrl) newChat(agentFromUrl);
    else updateAgentSelectState();
  });
}

async function syncSessionsFromBackend() {
  try {
    const r = await fetch(`${state.baseUrl}/api/v1/sessions?limit=50`);
    if (!r.ok) return;
    const payload = await r.json();
    const rows = Array.isArray(payload?.items) ? payload.items : [];
    if (rows.length === 0) return;
    const byKey = new Map(state.sessions.map((s) => [sessionLookupKey(s), s]));
    for (const row of rows) {
      const agentId = row.agent_id || "";
      const key = `${agentId}:${row.session_id}`;
      const title = (row.user_prompt || row.session_id.slice(0, 8)).slice(0, 40);
      const createdAt = row.created_at || null;
      const updatedAt = row.updated_at || createdAt || null;
      const existing = byKey.get(key);
      if (existing) {
        Object.assign(existing, normalizeSession({ ...existing, id: row.session_id, agentId }));
        existing.agentName = row.agent_name || existing.agentName;
        existing.title = title;
        if (createdAt) existing.createdAt = createdAt;
        if (updatedAt) existing.updatedAt = updatedAt;
      } else {
        state.sessions.push(
          normalizeSession({
            id: row.session_id,
            agentId,
            agentName: row.agent_name,
            title,
            createdAt,
            updatedAt,
          })
        );
      }
    }
    sortSessions();
    persistSessions();
    renderSessions();
  } catch (_) {
    /* 后端不可用时保留 localStorage 列表 */
  }
}

function autoGrow() {
  el.input.style.height = "auto";
  el.input.style.height = Math.min(el.input.scrollHeight, 200) + "px";
}

/* --------------------------- 会话管理 --------------------------- */
function newChat(agentId) {
  if (state.streaming) stopStream();
  if (agentId) {
    state.agentId = agentId;
    localStorage.setItem("da_agent_id", state.agentId);
    if (el.agentSelect) el.agentSelect.value = state.agentId;
  } else {
    ensureAgentSelected();
  }
  state.sessionId = null;
  el.messages.innerHTML = "";
  el.messages.appendChild(el.emptyState);
  el.emptyState.classList.remove("hidden");
  el.sessionBadge.textContent = "";
  el.chatTitle.textContent = "新对话";
  el.usageInfo.textContent = "";
  updateChatHeader();
  updateAgentSelectState();
  renderSessions();
  el.input.focus();
}

function persistSessions() {
  localStorage.setItem("da_sessions", JSON.stringify(state.sessions));
}

function upsertSession(id, title, agentId, agentName) {
  const normalized = normalizeSession({ id, agentId: agentId || state.agentId });
  const key = `${normalized.agentId || ""}:${normalized.id}`;
  const now = new Date().toISOString();
  const existing = state.sessions.find((s) => sessionLookupKey(s) === key);
  if (existing) {
    Object.assign(existing, normalized);
    if (title) existing.title = title.slice(0, 40);
    if (agentName) existing.agentName = agentName;
    existing.updatedAt = now;
  } else {
    state.sessions.push({
      ...normalized,
      agentName: agentName || agentNameById(normalized.agentId),
      title: (title || normalized.id.slice(0, 8)).slice(0, 40),
      createdAt: now,
      updatedAt: now,
    });
  }
  sortSessions();
  persistSessions();
  renderSessions();
}

function renderSessions() {
  el.sessionList.innerHTML = "";
  if (state.sessions.length === 0) {
    el.sessionList.innerHTML = '<li class="session-empty">暂无会话</li>';
    return;
  }
  for (const s of state.sessions) {
    const li = document.createElement("li");
    li.className =
      "session-item" +
      (s.id === state.sessionId && (s.agentId || "") === (state.agentId || "") ? " active" : "");
    const agentLabel = s.agentName
      ? `<span class="session-agent">${escapeHtml(s.agentName)}</span>`
      : s.agentId
        ? `<span class="session-agent">${escapeHtml(s.agentId.slice(0, 8))}</span>`
        : "";
    li.innerHTML = `<span class="sid" title="${escapeHtml(s.id)}"><span class="session-title">${escapeHtml(s.title)}</span>${agentLabel}</span><span class="del" title="删除">✕</span>`;
    li.querySelector(".sid").addEventListener("click", () => switchSession(s));
    li.querySelector(".del").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s);
    });
    el.sessionList.appendChild(li);
  }
}

async function switchSession(s) {
  if (state.streaming) stopStream();
  const session = normalizeSession(s);
  state.sessionId = session.id;
  if (session.agentId) {
    state.agentId = session.agentId;
    localStorage.setItem("da_agent_id", state.agentId);
    if (el.agentSelect) el.agentSelect.value = state.agentId;
  }
  el.messages.innerHTML = "";
  el.emptyState.classList.add("hidden");
  el.sessionBadge.textContent = session.id.slice(0, 8);
  el.chatTitle.textContent = session.title || "对话";
  updateChatHeader();
  updateAgentSelectState();
  renderSessions();

  try {
    const r = await fetch(sessionApiUrl(session, "/history"));
    if (r.status === 404) {
      addSystemNote("会话不存在或尚无历史记录。");
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (!data.messages || data.messages.length === 0) {
      el.emptyState.classList.remove("hidden");
      el.messages.appendChild(el.emptyState);
      return;
    }
    renderHistory(data.messages);
  } catch (e) {
    addSystemNote(`加载历史失败：${e.message}`);
  }
}

function renderHistory(messages) {
  let lastDayKey = null;
  for (const msg of messages) {
    const ts = msg.timestamp || null;
    if (ts) {
      const dayKey = localDayKey(ts);
      if (dayKey !== lastDayKey) {
        addDateSeparator(ts);
        lastDayKey = dayKey;
      }
    }
    if (msg.role === "user" && msg.content) {
      addMessage("user", msg.content, ts);
      continue;
    }
    if (msg.role === "assistant" && msg.parts && msg.parts.length) {
      const assistant = createAssistantMessage(ts);
      const completedToolIds = collectCompletedToolIds(msg.parts);
      for (const part of msg.parts) {
        if (part.type === "thinking" && part.content) {
          assistant.addThinkingBlock(part.content);
        } else if (part.type === "text" && part.content) {
          assistant.appendText(part.content);
        } else if (part.type === "tool_call") {
          assistant.addToolCall({
            tool: part.tool,
            args: part.args,
            tool_call_id: part.tool_call_id,
          });
        } else if (part.type === "tool_result") {
          assistant.addToolResult({
            tool: part.tool,
            result: part.result,
            tool_call_id: part.tool_call_id,
          });
        }
      }
      for (const part of msg.parts) {
        if (isPendingUiToolCall(part, completedToolIds)) {
          assistant.attachHitlWidget({
            tool: part.tool,
            args: part.args,
            tool_call_id: part.tool_call_id,
          });
        }
      }
      assistant.finalizeThinking();
      assistant.finalizeText();
    }
  }
  state.awaitingHitl = findResumablePendingUiCall(messages) !== null;
  updateHitlInputState();
  scrollToBottom();
}

function collectCompletedToolIds(parts) {
  const ids = new Set();
  for (const part of parts) {
    if (part.type === "tool_result" && part.tool_call_id) {
      ids.add(part.tool_call_id);
    }
  }
  return ids;
}

function isPendingUiToolCall(part, completedToolIds) {
  return (
    part.type === "tool_call" &&
    part.tool_call_id &&
    UI_TOOLS.has(part.tool) &&
    !completedToolIds.has(part.tool_call_id)
  );
}

/** Pending UI tool call at the end of history (safe to resume). */
function findResumablePendingUiCall(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") return null;
    if (msg.role !== "assistant" || !msg.parts?.length) continue;
    const completedToolIds = collectCompletedToolIds(msg.parts);
    for (const part of msg.parts) {
      if (isPendingUiToolCall(part, completedToolIds)) {
        return part;
      }
    }
    return null;
  }
  return null;
}

async function deleteSession(session) {
  const normalized = normalizeSession(session);
  const key = sessionLookupKey(normalized);
  state.sessions = state.sessions.filter((s) => sessionLookupKey(s) !== key);
  persistSessions();
  try {
    await fetch(sessionApiUrl(normalized), { method: "DELETE" });
  } catch (_) { /* 忽略 */ }
  if (normalized.id === state.sessionId && (normalized.agentId || "") === (state.agentId || "")) newChat();
  else renderSessions();
}

/* --------------------------- 健康检查 / 能力 --------------------------- */
async function checkHealth() {
  setConn("pending", "检测中…");
  try {
    const r = await fetch(`${state.baseUrl}/health`, { method: "GET" });
    if (r.ok) setConn("ok", "已连接");
    else setConn("err", `HTTP ${r.status}`);
  } catch (_) {
    setConn("err", "未连接");
  }
}

function setConn(kind, text) {
  el.connStatus.className = "conn-status" + (kind === "ok" ? " ok" : kind === "err" ? " err" : "");
  el.connStatus.querySelector(".conn-text").textContent = text;
}

async function loadAgents() {
  if (!el.agentSelect) return;
  el.agentSelect.innerHTML = '<option value="">加载中…</option>';
  try {
    const r = await fetch(`${state.baseUrl}/api/v1/agents`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.agents = await r.json();
    el.agentSelect.innerHTML = "";
    if (!state.agents.length) {
      el.agentSelect.innerHTML = '<option value="">无可用 Agent</option>';
      return;
    }
    for (const a of state.agents) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.name;
      el.agentSelect.appendChild(opt);
    }
    ensureAgentSelected();
    updateChatHeader();
  } catch (e) {
    el.agentSelect.innerHTML = `<option value="">加载失败</option>`;
    console.warn("[deep-agent] 加载 Agent 失败", e);
  }
}

function updateChatHeader() {
  if (state.agentId) {
    el.sessionBadge.textContent = state.sessionId
      ? state.sessionId.slice(0, 8)
      : agentNameById(state.agentId);
  }
}

function updateAgentSelectState() {
  if (el.agentSelect) el.agentSelect.disabled = state.streaming || !!state.sessionId;
}

async function loadCapabilities() {
  el.capList.innerHTML = '<li class="cap-empty">加载中…</li>';
  try {
    const r = await fetch(`${state.baseUrl}/api/v1/tools`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const caps = await r.json();
    el.capList.innerHTML = "";
    if (!caps.length) {
      el.capList.innerHTML = '<li class="cap-empty">无可用能力</li>';
      return;
    }
    for (const c of caps) {
      const li = document.createElement("li");
      li.className = "cap-item";
      const tools = (c.tools || []).map((t) => t.name).join(", ");
      li.innerHTML = `
        <div class="cap-name">${escapeHtml(c.name)} <span class="cap-kind">${escapeHtml(c.kind)}</span></div>
        ${tools ? `<div class="cap-tools">${escapeHtml(tools)}</div>` : ""}`;
      el.capList.appendChild(li);
    }
  } catch (e) {
    el.capList.innerHTML = `<li class="cap-empty">加载失败：${escapeHtml(e.message)}</li>`;
  }
}

/* --------------------------- 发送 / 流式 --------------------------- */
async function onSend() {
  const text = el.input.value.trim();
  if (!text || state.streaming) return;
  if (state.awaitingHitl) {
    addSystemNote("请先完成上方待处理的交互。");
    return;
  }
  if (!ensureAgentSelected()) {
    addSystemNote("请先选择 Agent。");
    return;
  }

  el.emptyState.classList.add("hidden");
  el.input.value = "";
  autoGrow();

  addMessage("user", text);

  const assistant = createAssistantMessage();
  setStreaming(true);
  updateAgentSelectState();

  if (!state.sessionId) state.sessionId = generateSessionId();

  const payload = { agent_id: state.agentId, message: text, session_id: state.sessionId };
  await runChatStream(payload, assistant);
}

async function resumeDeferred(deferredResults) {
  if (state.streaming || !state.sessionId) return;
  state.awaitingHitl = false;
  updateHitlInputState();

  const assistant = createAssistantMessage();
  setStreaming(true);
  updateAgentSelectState();

  const payload = {
    agent_id: state.agentId,
    session_id: state.sessionId,
    deferred_tool_results: deferredResults,
  };
  await runChatStream(payload, assistant);
}

async function runChatStream(payload, assistant) {
  state.abortController = new AbortController();
  try {
    const resp = await fetch(`${state.baseUrl}/api/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(payload),
      signal: state.abortController.signal,
    });

    if (!resp.ok || !resp.body) {
      const detail = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status} ${detail}`.trim());
    }
    setConn("ok", "已连接");

    await readSse(resp.body, (evt) => handleEvent(evt, assistant));
  } catch (e) {
    if (e.name === "AbortError") {
      assistant.finalizeThinking();
      assistant.finalizeText();
      addErrorTo(assistant, "（已停止生成）");
    } else {
      setConn("err", "请求失败");
      addErrorTo(assistant, `请求失败：${e.message}`);
    }
  } finally {
    assistant.finalizeThinking();
    assistant.finalizeText();
    setStreaming(false);
    state.abortController = null;
  }
}

function updateHitlInputState() {
  if (el.input) {
    el.input.placeholder = state.awaitingHitl ? "请先完成上方交互…" : "输入消息…";
    el.input.disabled = state.awaitingHitl;
  }
  if (el.sendBtn) el.sendBtn.disabled = state.awaitingHitl;
}

function stopStream() {
  if (state.abortController) state.abortController.abort();
}

function setStreaming(v) {
  state.streaming = v;
  el.sendBtn.classList.toggle("hidden", v);
  el.stopBtn.classList.toggle("hidden", !v);
  el.input.disabled = false;
  updateAgentSelectState();
}

/**
 * 解析 SSE 流（fetch ReadableStream）。
 * 事件块以空行分隔，每块包含 event: / data: 行；以 ":" 开头的是注释(心跳)。
 */
async function readSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // sse_starlette 默认以 \r\n 分行，事件块之间是 \r\n\r\n；
    // 同时兼容 \n\n，并取最早出现的边界。
    let m;
    while ((m = buffer.match(/\r\n\r\n|\n\n/))) {
      const idx = m.index;
      const rawBlock = buffer.slice(0, idx);
      buffer = buffer.slice(idx + m[0].length);
      const evt = parseSseBlock(rawBlock);
      if (evt) onEvent(evt);
    }
  }
  // flush 残留
  const evt = parseSseBlock(buffer);
  if (evt) onEvent(evt);
}

function parseSseBlock(block) {
  const lines = block.split(/\r?\n/);
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue; // 空行或心跳注释
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!dataLines.length) return null;
  let data;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch (_) {
    data = { raw: dataLines.join("\n") };
  }
  return { event, data };
}

function handleEvent({ event, data }, assistant) {
  const type = event || data.type;
  switch (type) {
    case "session": {
      const id = data.session_id;
      if (id) {
        state.sessionId = id;
        if (data.agent_id) {
          state.agentId = data.agent_id;
          localStorage.setItem("da_agent_id", state.agentId);
          if (el.agentSelect) el.agentSelect.value = state.agentId;
        }
        updateChatHeader();
        const title = lastUserText() || id.slice(0, 8);
        upsertSession(id, title, state.agentId, agentNameById(state.agentId));
        if (el.chatTitle.textContent === "新对话") el.chatTitle.textContent = title.slice(0, 40);
        updateAgentSelectState();
      }
      break;
    }
    case "thinking":
      assistant.appendThinking(data.delta || "");
      break;
    case "text":
      assistant.finalizeThinking();
      assistant.appendText(data.delta || "");
      break;
    case "tool_call":
      assistant.addToolCall(data);
      break;
    case "tool_result":
      assistant.addToolResult(data);
      break;
    case "final":
      assistant.finalizeThinking();
      assistant.finalizeText();
      if (data.usage) renderUsage(data.usage);
      if (data.status === "pending" && data.deferred) {
        state.awaitingHitl = true;
        const pending = [...(data.deferred.calls || []), ...(data.deferred.approvals || [])];
        for (const call of pending) {
          assistant.attachHitlWidget(call);
        }
      } else {
        state.awaitingHitl = false;
      }
      updateHitlInputState();
      break;
    case "error":
      addErrorTo(assistant, data.message || "未知错误");
      break;
    default:
      break;
  }
  scrollToBottom();
}

function renderUsage(usage) {
  const parts = [];
  if (usage.requests != null) parts.push(`${usage.requests} 次请求`);
  if (usage.input_tokens != null) parts.push(`in ${usage.input_tokens}`);
  if (usage.output_tokens != null) parts.push(`out ${usage.output_tokens}`);
  if (usage.total_tokens != null) parts.push(`共 ${usage.total_tokens} tokens`);
  el.usageInfo.textContent = parts.join(" · ");
}

/* --------------------------- 消息渲染 --------------------------- */
function addMessage(role, text, timestamp) {
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;
  row.innerHTML = `
    <div class="avatar ${role}">${role === "user" ? "你" : "🤖"}</div>
    <div class="bubble-wrap"><div class="bubble"></div></div>`;
  const wrap = row.querySelector(".bubble-wrap");
  if (timestamp) prependMsgTime(wrap, timestamp);
  wrap.querySelector(".bubble").textContent = text;
  el.messages.appendChild(row);
  scrollToBottom();
  return row;
}

function addDateSeparator(iso) {
  const sep = document.createElement("div");
  sep.className = "date-separator";
  sep.textContent = formatDateSeparator(iso);
  el.messages.appendChild(sep);
}

function prependMsgTime(wrap, iso) {
  const timeEl = document.createElement("div");
  timeEl.className = "msg-time";
  timeEl.textContent = formatMessageTime(iso);
  wrap.insertBefore(timeEl, wrap.firstChild);
}

function addSystemNote(text) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.innerHTML = `<div class="avatar assistant">ℹ️</div><div class="bubble-wrap"><div class="bubble" style="opacity:.7;font-size:13px"></div></div>`;
  row.querySelector(".bubble").textContent = text;
  el.messages.appendChild(row);
  scrollToBottom();
}

function createAssistantMessage(timestamp) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.innerHTML = `
    <div class="avatar assistant">🤖</div>
    <div class="bubble-wrap"></div>`;
  const wrap = row.querySelector(".bubble-wrap");
  if (timestamp) prependMsgTime(wrap, timestamp);
  el.messages.appendChild(row);
  scrollToBottom();

  let textBuffer = "";
  let textBubble = null;
  let thinkingBlock = null;
  let thinkingBuffer = "";
  const toolBlocks = new Map(); // tool_call_id -> element

  function ensureThinkingBlock() {
    if (!thinkingBlock) {
      thinkingBlock = document.createElement("div");
      thinkingBlock.className = "thinking-block";
      thinkingBlock.innerHTML = `
        <div class="thinking-head">
          <span class="chevron">▶</span>
          <span class="thinking-icon">💭</span>
          <span class="thinking-title">思考中…</span>
          <span class="thinking-status"><span class="spinner"></span></span>
        </div>
        <div class="thinking-body"><pre class="thinking-content"></pre></div>`;
      thinkingBlock.querySelector(".thinking-head").addEventListener("click", () => {
        thinkingBlock.classList.toggle("open");
      });
      wrap.appendChild(thinkingBlock);
    }
    return thinkingBlock;
  }

  function renderThinkingContent() {
    const pre = ensureThinkingBlock().querySelector(".thinking-content");
    pre.textContent = thinkingBuffer;
  }

  function finalizeThinkingBlock() {
    if (!thinkingBlock) return;
    thinkingBlock.querySelector(".thinking-title").textContent = "思考过程";
    thinkingBlock.querySelector(".thinking-status").innerHTML = "";
    thinkingBlock.classList.remove("open");
  }

  function ensureTextBubble() {
    if (!textBubble) {
      textBubble = document.createElement("div");
      textBubble.className = "bubble markdown streaming";
      wrap.appendChild(textBubble);
    }
    return textBubble;
  }

  return {
    row,
    appendThinking(delta) {
      thinkingBuffer += delta;
      renderThinkingContent();
      scrollToBottom();
    },
    finalizeThinking() {
      finalizeThinkingBlock();
    },
    addThinkingBlock(content) {
      thinkingBuffer = content;
      renderThinkingContent();
      finalizeThinkingBlock();
    },
    appendText(delta) {
      textBuffer += delta;
      ensureTextBubble().innerHTML = renderMarkdownBody(textBuffer);
    },
    finalizeText() {
      if (textBubble) {
        textBubble.classList.remove("streaming");
        textBubble.innerHTML = renderMarkdownBody(textBuffer);
      }
    },
    addToolCall(data) {
      finalizeThinkingBlock();
      const key = data.tool_call_id || `${data.tool}-${toolBlocks.size}`;
      const hasArgs = hasNonEmptyArgs(data.args);
      const existing = toolBlocks.get(key);

      if (existing) {
        if (hasArgs) {
          const argsEl = existing.querySelector(".args");
          if (argsEl) argsEl.textContent = pretty(data.args);
        }
        scrollToBottom();
        return;
      }

      // 工具调用开始后，文本若在流式中先固定，避免后续文本拼接错乱
      const block = document.createElement("div");
      block.className = "tool-block open";
      block.dataset.toolCallId = key;
      const argsHtml = hasArgs
        ? `<pre class="args">${escapeHtml(pretty(data.args))}</pre>`
        : `<pre class="args args-pending">参数接收中…</pre>`;
      block.innerHTML = `
        <div class="tool-head">
          <span class="chevron">▶</span>
          <span class="tool-icon">🛠️</span>
          <span class="tool-name">${escapeHtml(data.tool || "tool")}</span>
          <span class="tool-status"><span class="spinner"></span></span>
        </div>
        <div class="tool-body">
          <div class="label">参数 args</div>
          ${argsHtml}
        </div>`;
      block.querySelector(".tool-head").addEventListener("click", () => block.classList.toggle("open"));
      wrap.appendChild(block);
      toolBlocks.set(key, block);
      // 新文本将进入一个新气泡
      textBubble = null;
      textBuffer = "";
      scrollToBottom();
    },
    addToolResult(data) {
      const key = data.tool_call_id;
      let block = key && toolBlocks.get(key);
      if (!block) {
        block = [...toolBlocks.values()].reverse().find((b) => !b.dataset.done);
      }
      if (!block) return;
      block.dataset.done = "1";
      block.classList.remove("hitl-pending");
      block.querySelector(".tool-status").innerHTML = '<span style="color:#3fb950">✓ 完成</span>';
      const body = block.querySelector(".tool-body");
      const existingWidget = body.querySelector(".hitl-widget");
      if (existingWidget) existingWidget.remove();
      const res = document.createElement("div");
      res.innerHTML = `<div class="label" style="margin-top:8px">结果 result</div>${renderToolResult(data.result)}`;
      body.appendChild(res);
    },
    attachHitlWidget(call) {
      const key = call.tool_call_id;
      let block = key && toolBlocks.get(key);
      if (!block) {
        this.addToolCall(call);
        block = toolBlocks.get(key);
      }
      if (!block || !UI_TOOLS.has(call.tool)) return;
      renderHitlWidget(block, call, resumeDeferred);
    },
  };
}

function renderHitlWidget(block, call, onSubmit) {
  const { tool, args = {}, tool_call_id: toolCallId } = call;
  const body = block.querySelector(".tool-body");
  if (!body || body.querySelector(".hitl-widget")) return;

  block.classList.add("open", "hitl-pending");
  block.querySelector(".tool-status").innerHTML = '<span style="color:#d29922">等待操作</span>';

  const widget = document.createElement("div");
  widget.className = "hitl-widget";

  if (tool === "confirm_action") {
    const message = args.message || "确认执行？";
    widget.innerHTML = `
      <p class="hitl-prompt">${escapeHtml(message)}</p>
      <div class="hitl-actions">
        <button type="button" class="hitl-btn hitl-confirm">${escapeHtml(args.confirm_label || "确认")}</button>
        <button type="button" class="hitl-btn hitl-cancel">${escapeHtml(args.cancel_label || "取消")}</button>
      </div>`;
    widget.querySelector(".hitl-confirm").addEventListener("click", () => {
      onSubmit([{ tool_call_id: toolCallId, result: { confirmed: true } }]);
    });
    widget.querySelector(".hitl-cancel").addEventListener("click", () => {
      onSubmit([{ tool_call_id: toolCallId, result: { confirmed: false } }]);
    });
  } else if (tool === "ask_user_choice") {
    const options = Array.isArray(args.options) ? args.options : [];
    const allowMultiple = !!args.allow_multiple;
    widget.innerHTML = `
      <p class="hitl-prompt">${escapeHtml(args.prompt || "请选择：")}</p>
      <div class="hitl-choices"></div>
      <button type="button" class="hitl-btn hitl-submit">提交</button>`;
    const choicesEl = widget.querySelector(".hitl-choices");
    for (const opt of options) {
      const label = document.createElement("label");
      label.className = "hitl-choice";
      const input = document.createElement("input");
      input.type = allowMultiple ? "checkbox" : "radio";
      input.name = `choice-${toolCallId}`;
      input.value = opt;
      label.appendChild(input);
      label.appendChild(document.createTextNode(` ${opt}`));
      choicesEl.appendChild(label);
    }
    widget.querySelector(".hitl-submit").addEventListener("click", () => {
      const selected = [...choicesEl.querySelectorAll("input:checked")].map((i) => i.value);
      const result = allowMultiple ? { selected } : { selected: selected[0] ?? null };
      onSubmit([{ tool_call_id: toolCallId, result }]);
    });
  } else {
    return;
  }

  body.appendChild(widget);
}

function addErrorTo(assistant, message) {
  const div = document.createElement("div");
  div.className = "error-bubble";
  div.textContent = "⚠ " + message;
  assistant.row.querySelector(".bubble-wrap").appendChild(div);
  scrollToBottom();
}

/* --------------------------- 工具函数 --------------------------- */
function localDayKey(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function formatDateSeparator(iso) {
  const d = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((today - msgDay) / 86400000);
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function formatMessageTime(iso) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(
    new Date(iso)
  );
}

function lastUserText() {
  const rows = el.messages.querySelectorAll(".msg-row.user .bubble");
  return rows.length ? rows[rows.length - 1].textContent : "";
}

function scrollToBottom() {
  el.messages.scrollTop = el.messages.scrollHeight;
}

function hasNonEmptyArgs(args) {
  if (args == null) return false;
  if (typeof args === "object" && !Array.isArray(args)) return Object.keys(args).length > 0;
  return true;
}

function pretty(v) {
  if (v == null) return "null";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
}

function renderToolResult(result) {
  if (typeof result === "string") {
    return `<div class="md-content tool-result-md markdown-body">${renderMarkdown(result)}</div>`;
  }
  return `<pre>${escapeHtml(pretty(result))}</pre>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* --------------------------- Markdown (marked + DOMPurify + hljs) --------------------------- */
let markdownReady = false;

function initMarkdown() {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    console.warn("[deep-agent] Markdown 库未加载，将回退为纯文本");
    return;
  }

  marked.use({
    gfm: true,
    breaks: false,
    renderer: {
      code(codeText, lang) {
        const text = String(codeText ?? "");
        let highlighted = escapeHtml(text);
        if (typeof hljs !== "undefined") {
          if (lang && hljs.getLanguage(lang)) {
            try {
              highlighted = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
            } catch (_) { /* keep escaped fallback */ }
          } else {
            try {
              highlighted = hljs.highlightAuto(text).value;
            } catch (_) { /* keep escaped fallback */ }
          }
        }
        const langAttr = lang ? ` class="hljs language-${escapeHtml(lang)}"` : ' class="hljs"';
        return `<pre><code${langAttr}>${highlighted}</code></pre>\n`;
      },
    },
  });

  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });

  markdownReady = true;
}

function renderMarkdown(src) {
  const text = String(src ?? "");
  if (!markdownReady) return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;

  const raw = marked.parse(text);
  return DOMPurify.sanitize(raw, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["target", "rel", "class", "data-lang"],
  });
}

function renderMarkdownBody(src) {
  return `<div class="markdown-body">${renderMarkdown(src)}</div>`;
}

init();
