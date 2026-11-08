const LABELS = {
  default: "默认", minimal: "极低", low: "低", medium: "中",
  high: "高", xhigh: "超高", max: "最高", ultra: "极高", none: "关闭",
};

export function effortLabel(value) {
  return Object.hasOwn(LABELS, value) ? LABELS[value] : value || "默认";
}

export function modelWithEffort(name, effort) {
  return effort ? `${name} · ${effortLabel(effort)}` : name;
}

export function effortValues(options) {
  return [...new Set((options || []).filter(item => item.type === "effort").flatMap(item => item.values || []))];
}

export function parseEffortValues(input) {
  const value = input.trim();
  let parsed;
  try { parsed = value.startsWith("[") ? JSON.parse(value) : value.split(",").map(item => item.trim()).filter(Boolean); }
  catch { throw new Error("请输入 JSON 字符串数组，或用逗号分隔档位"); }
  if (!Array.isArray(parsed) || parsed.length > 32 || parsed.some(item => typeof item !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(item) || item.toLowerCase() === "default")) {
    throw new Error("最多 32 个原生档位，不能包含默认值");
  }
  return [...new Set(parsed)];
}

export function historyEffort(items, turnId) {
  if (!turnId) return null;
  return items.find(item => item.kind === "user_message" && item.turn_id === turnId)?.reasoning_effort || null;
}

export function eventEffort(events, runId) {
  if (!runId) return null;
  const run = events.find(event => event.run_id === runId && event.turn_id);
  if (!run) return null;
  return events.find(event => event.turn_id === run.turn_id && event.event_type === "user/message")
    ?.payload?.message?.metadata?.reasoning_effort || null;
}

export function appendThinkingMenu(menu, choices, selected, onSelect) {
  if (!selected) selected = "default";
  const doc = menu.ownerDocument;
  const row = doc.createElement("div");
  row.className = "reasoning-picker-row";
  const trigger = doc.createElement(choices?.length ? "button" : "span");
  trigger.className = "reasoning-picker-trigger";
  const label = doc.createElement("span");
  label.textContent = "Thinking";
  const current = doc.createElement("span");
  current.className = "reasoning-picker-current";
  const value = doc.createElement("span");
  value.textContent = effortLabel(selected);
  current.append(value);
  trigger.append(label, current);
  row.append(trigger);
  menu.append(row);
  if (!choices?.length) return;
  trigger.type = "button";
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-haspopup", "menu");
  const chevron = doc.createElement("i");
  chevron.setAttribute("data-lucide", "chevron-right");
  chevron.setAttribute("aria-hidden", "true");
  current.append(chevron);
  const panel = doc.createElement("div");
  panel.className = "da-select-menu reasoning-picker-options";
  panel.setAttribute("popover", "manual");
  panel.setAttribute("role", "menu");
  panel.setAttribute("aria-label", "思考档位");
  panel.hidden = true;
  row.append(panel);
  function close(focus = false) {
    panel.hidePopover?.();
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (focus && trigger.isConnected) trigger.focus();
  }
  panel.closeReasoningMenu = close;
  for (const effort of ["default", ...choices]) {
    const button = doc.createElement("button");
    button.type = "button";
    button.className = "da-select-option";
    button.setAttribute("role", "menuitemradio");
    button.setAttribute("aria-checked", String(effort === selected));
    const optionLabel = doc.createElement("span");
    optionLabel.className = "reasoning-picker-option-label";
    const localized = effortLabel(effort);
    optionLabel.textContent = localized;
    if (localized !== effort) {
      const nativeLabel = doc.createElement(effort === "default" ? "span" : "code");
      nativeLabel.className = "reasoning-picker-option-code";
      nativeLabel.textContent = effort === "default" ? "Default" : effort;
      optionLabel.append(" ", nativeLabel);
    }
    button.append(optionLabel);
    if (effort === selected) {
      const check = doc.createElement("i");
      check.setAttribute("data-lucide", "check");
      check.setAttribute("aria-hidden", "true");
      button.append(check);
    }
    button.onclick = () => { close(); onSelect(effort); };
    panel.append(button);
  }
  panel.addEventListener("keydown", event => {
    if (event.key === "Escape" || event.key === "ArrowLeft") {
      event.preventDefault(); event.stopPropagation(); close(true);
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const buttons = [...panel.children];
      const current = buttons.indexOf(doc.activeElement);
      const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
        : (current + buttons.length + (event.key === "ArrowDown" ? 1 : -1)) % buttons.length;
      buttons[index]?.focus();
    }
  });
  trigger.onkeydown = event => {
    if (event.key === "ArrowRight" && panel.hidden) {
      event.preventDefault();
      trigger.click();
    }
  };
  trigger.onclick = () => {
    if (!panel.hidden) { close(true); return; }
    const rect = menu.getBoundingClientRect();
    const viewport = doc.documentElement;
    const margin = 8, gap = 6;
    panel.hidden = false;
    panel.showPopover?.();
    const width = panel.offsetWidth, height = panel.offsetHeight;
    let left = rect.right + gap, top = rect.bottom - height;
    if (left + width > viewport.clientWidth - margin) {
      left = rect.left - width - gap;
      if (left < margin) {
        left = rect.right - width;
        top = rect.bottom + gap + height <= viewport.clientHeight - margin
          ? rect.bottom + gap : row.getBoundingClientRect().top - height - gap;
      }
    }
    panel.style.left = `${Math.max(margin, Math.min(left, viewport.clientWidth - width - margin))}px`;
    panel.style.top = `${Math.max(margin, Math.min(top, viewport.clientHeight - height - margin))}px`;
    trigger.setAttribute("aria-expanded", "true");
    (panel.querySelector('[aria-checked="true"]') || panel.firstElementChild)?.focus();
  };
}

if (typeof window !== "undefined") {
  window.DeepAgentReasoning = { effortLabel, modelWithEffort, effortValues, parseEffortValues, appendThinkingMenu };
}
