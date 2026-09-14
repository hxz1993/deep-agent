import { escapeHtml as e, icon, icons } from "./diagnostics-ui.js?v=20260915-release-audit";

export function uniqueChannelPeers(items) {
  const peers = new Map();
  for (const item of items) {
    const key = `${item.kind}:${item.peer_id}`;
    if (!peers.has(key) || (!peers.get(key).name && item.name)) peers.set(key, item);
  }
  return [...peers.values()];
}

export function peerPickerHtml(kind, selected) {
  const label = kind === "group" ? "群聊" : "用户";
  return `<details class="channel-peer-picker" data-picker-kind="${kind}"><summary class="console-select-trigger" aria-label="筛选${label}">${icon(kind === "group" ? "users-round" : "user-round")}<span>${e(selected?.name || selected?.peer_id || `全部${label}`)}</span>${icon("chevron-down")}</summary><div class="channel-peer-popover"><input type="search" role="combobox" aria-label="搜索${label}名称或 ID" aria-controls="channel-${kind}-options" aria-autocomplete="list" aria-expanded="false" placeholder="搜索${label}名称或 ID" maxlength="200" autocomplete="off"/><div id="channel-${kind}-options" role="listbox" aria-label="${label}搜索结果"></div></div></details>`;
}

export function bindPeerPicker(root, { kind, selectedId, fetchPage, onSelect, initialQuery = "", onQuery = () => {} }) {
  const host = root.querySelector(`[data-picker-kind="${kind}"]`);
  if (!host) return () => {};
  const input = host.querySelector("input"), list = host.querySelector('[role="listbox"]');
  let abort, timer, generation = 0, items = [], total = 0, offset = 0, query = "", busy = false, disposed = false;
  const label = kind === "group" ? "群聊" : "用户";
  function draw(message = "") {
    list.innerHTML = `<button type="button" role="option" aria-selected="${!selectedId}" data-peer-choice="">全部${label}</button>${items.map(item => `<button type="button" role="option" aria-selected="${selectedId === item.peer_id}" data-peer-choice="${e(item.peer_id)}" title="${e(item.name ? `${item.name} · ${item.peer_id}` : item.peer_id)}"><span>${e(item.name || item.peer_id)}</span>${item.name && item.name !== item.peer_id ? `<small>${e(item.peer_id)}</small>` : ""}</button>`).join("")}${message ? `<p role="status">${e(message)}</p>` : ""}${offset < total ? `<button type="button" data-peer-more ${busy ? "disabled" : ""}>加载更多</button>` : ""}`;
  }
  async function load(more = false) {
    abort?.abort(); abort = new AbortController(); const current = ++generation;
    if (!more) { items = []; offset = 0; total = 0; }
    busy = true; draw("正在搜索…");
    try {
      const data = await fetchPage(query, offset, abort.signal);
      if (disposed || current !== generation || !host.open) return;
      items = uniqueChannelPeers([...items, ...data.items]); offset += data.items.length; total = data.total;
      busy = false; draw(items.length ? "" : "没有匹配结果");
    } catch (error) {
      if (disposed || current !== generation || abort.signal.aborted) return;
      busy = false; draw(error.message || "搜索失败，请重新输入重试");
    }
  }
  const stop = () => { clearTimeout(timer); abort?.abort(); generation++; busy = false; };
  const toggle = () => {
    input.setAttribute("aria-expanded", String(host.open));
    if (host.open) { input.value = initialQuery; query = initialQuery; input.focus(); void load(); } else stop();
  };
  const change = () => { stop(); query = input.value.trim(); initialQuery = query; onQuery(query); items = []; total = 0; draw("正在搜索…"); timer = setTimeout(() => load(), 250); };
  const click = event => {
    const option = event.target.closest("[data-peer-choice]");
    if (option) { const item = items.find(p => p.peer_id === option.dataset.peerChoice) || null; host.open = false; stop(); onSelect(item); }
    else if (event.target.closest("[data-peer-more]") && !busy) void load(true);
  };
  const key = event => {
    if (event.key === "Escape") { host.open = false; host.querySelector("summary").focus(); }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault(); const buttons = [...list.querySelectorAll("button:not(:disabled)")];
    const index = buttons.indexOf(event.target), step = event.key === "ArrowDown" ? 1 : -1;
    buttons[index < 0 ? (step > 0 ? 0 : buttons.length - 1) : (index + step + buttons.length) % buttons.length]?.focus();
  };
  const outside = event => { if (!host.contains(event.target)) host.open = false; };
  host.addEventListener("toggle", toggle); input.addEventListener("input", change); list.addEventListener("click", click); host.addEventListener("keydown", key);
  root.ownerDocument.addEventListener("pointerdown", outside); icons();
  return () => { disposed = true; stop(); host.removeEventListener("toggle", toggle); input.removeEventListener("input", change); list.removeEventListener("click", click); host.removeEventListener("keydown", key); root.ownerDocument.removeEventListener("pointerdown", outside); };
}
