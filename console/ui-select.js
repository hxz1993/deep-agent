// Shared visual adapter. The select remains the source of truth for forms and consumers.
let nextId = 0;
const instances = new WeakMap();

export function nextSelectIndex(options, current, key) {
  const enabled = options.map((option, index) => ({ option, index }))
    .filter(({ option }) => !option.disabled && !option.hidden && !option.parentElement?.disabled)
    .map(({ index }) => index);
  if (!enabled.length) return -1;
  if (key === "Home") return enabled[0];
  if (key === "End") return enabled.at(-1);
  const position = enabled.indexOf(current);
  if (position < 0) return key === "ArrowUp" ? enabled.at(-1) : enabled[0];
  return enabled[Math.max(0, Math.min(enabled.length - 1, position + (key === "ArrowUp" ? -1 : 1)))];
}

export function selectMenuPosition(rect, viewport, height = 280) {
  const margin = 8, gap = 5;
  const width = Math.min(Math.max(rect.width, 160), viewport.width - margin * 2);
  const below = viewport.height - rect.bottom - gap - margin;
  const above = rect.top - gap - margin;
  const up = below < Math.min(height, 160) && above > below;
  const maxHeight = Math.max(0, Math.min(height, up ? above : below));
  return { left: Math.max(margin, Math.min(rect.left, viewport.width - width - margin)),
    top: up ? rect.top - gap - maxHeight : rect.bottom + gap, width, maxHeight };
}

export function observeSelectProperty(target, name, changed) {
  const own = Object.getOwnPropertyDescriptor(target, name);
  if (own && !own.configurable) return () => {};
  let prototype = target, descriptor = own;
  while (!descriptor && (prototype = Object.getPrototypeOf(prototype))) descriptor = Object.getOwnPropertyDescriptor(prototype, name);
  if (!descriptor?.get || !descriptor?.set) return () => {};
  Object.defineProperty(target, name, { configurable: true, get() { return descriptor.get.call(this); },
    set(value) { descriptor.set.call(this, value); changed(); } });
  return () => { if (own) Object.defineProperty(target, name, own); else delete target[name]; };
}

export function selectLabelText(node) {
  if (node.nodeType === 3) return node.textContent || "";
  if (node.nodeType !== 1 || node.matches("select, input, textarea, button, .da-select, .console-select, .console-combobox, small, svg, .field-help, .field-hint, .field-subtitle, [role=tooltip], [role=listbox], .hidden, [hidden], [aria-hidden=true]")) return "";
  return Array.from(node.childNodes).map(selectLabelText).filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
}

export function enhanceSelect(select) {
  if (instances.has(select)) return instances.get(select);
  if (select.multiple || select.size > 1 || select.hidden || select.classList.contains("hidden")
      || select.getAttribute("aria-hidden") === "true" || select.dataset.nativeSelect === "true") return null;
  const document = select.ownerDocument, view = document.defaultView;
  const abort = new view.AbortController();
  const on = (target, type, handler, options = {}) => target.addEventListener(type, handler, { ...options, signal: abort.signal });
  const host = document.createElement("span");
  host.className = "da-select";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "da-select-trigger";
  trigger.setAttribute("role", "combobox");
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const text = document.createElement("span");
  trigger.append(text);
  const chevron = document.createElement("i");
  chevron.setAttribute("data-lucide", "chevron-down");
  trigger.append(chevron);
  const menu = document.createElement("div");
  menu.id = `da-select-menu-${++nextId}`;
  menu.className = "da-select-menu";
  menu.setAttribute("role", "listbox");
  menu.setAttribute("popover", "manual");
  menu.hidden = true;
  trigger.setAttribute("aria-controls", menu.id);
  const originalTabIndex = select.getAttribute("tabindex");
  select.before(host);
  host.append(select, trigger, menu);
  select.classList.add("da-select-native");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  let opened = false, active = -1, disposed = false, search = "", searchAt = 0;
  const optionRestores = new Map();
  const restores = [];
  let resizeObserver;
  const options = () => Array.from(select.options);
  const position = () => {
    if (!opened) return;
    if (!trigger.getClientRects().length || !select.isConnected || select.closest("dialog:not([open])")) { close(); return; }
    const rect = trigger.getBoundingClientRect();
    const viewport = { width: view.innerWidth, height: view.innerHeight };
    // Measure at the final width. scrollHeight includes padding, but excludes borders.
    const initial = selectMenuPosition(rect, viewport);
    menu.style.width = `${initial.width}px`;
    menu.style.maxHeight = "none";
    const style = view.getComputedStyle(menu);
    const naturalHeight = Math.ceil(menu.scrollHeight + parseFloat(style.borderTopWidth || 0) + parseFloat(style.borderBottomWidth || 0));
    const bounds = selectMenuPosition(rect, viewport, Math.min(280, naturalHeight || 280));
    Object.assign(menu.style, { left: `${bounds.left}px`, top: `${bounds.top}px`, width: `${bounds.width}px`, maxHeight: `${bounds.maxHeight}px` });
  };
  function close({ focus = false } = {}) {
    if (!opened) return;
    opened = false;
    if (menu.hidePopover && menu.matches(":popover-open")) menu.hidePopover();
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    trigger.removeAttribute("aria-activedescendant");
    if (focus && select.isConnected) trigger.focus();
  }
  function markActive() {
    menu.querySelectorAll("[role=option]").forEach(node => node.classList.toggle("active", Number(node.dataset.index) === active));
    const node = menu.querySelector(`[data-index="${active}"]`);
    if (node) { trigger.setAttribute("aria-activedescendant", node.id); node.scrollIntoView?.({ block: "nearest" }); }
    else trigger.removeAttribute("aria-activedescendant");
  }
  function renderMenu() {
    menu.replaceChildren();
    let previousGroup = null;
    options().forEach((option, index) => {
      if (option.hidden) return;
      const group = option.parentElement?.tagName === "OPTGROUP" ? option.parentElement : null;
      if (group && group !== previousGroup) {
        const heading = document.createElement("div"); heading.className = "da-select-group";
        if (group.dataset.iconSrc) {
          const icon = document.createElement("img"); icon.src = group.dataset.iconSrc; icon.alt = ""; heading.append(icon);
        }
        heading.append(document.createTextNode(group.label)); menu.append(heading);
      }
      previousGroup = group;
      if (option.dataset.separatorBefore === "true" && menu.children.length) {
        const separator = document.createElement("div");
        separator.className = "da-select-separator";
        separator.setAttribute("role", "separator");
        menu.append(separator);
      }
      const row = document.createElement("div");
      row.className = "da-select-option";
      row.classList.toggle("is-grouped", Boolean(group));
      row.id = `${menu.id}-${index}`;
      row.dataset.index = String(index);
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(option.selected));
      row.setAttribute("aria-disabled", String(option.disabled || !!group?.disabled));
      row.textContent = option.label;
      if (option.dataset.badge) {
        row.textContent = "";
        const name = document.createElement("span"); name.textContent = option.label;
        const badge = document.createElement("span"); badge.className = "da-select-badge"; badge.textContent = option.dataset.badge;
        row.append(name, badge);
        row.classList.add("has-badge");
      }
      if (/^[a-z][a-z0-9-]*$/.test(option.dataset.icon || "")) {
        const icon = document.createElement("i");
        icon.setAttribute("data-lucide", option.dataset.icon);
        icon.setAttribute("aria-hidden", "true");
        row.prepend(icon);
        row.classList.add("has-icon");
      }
      row.title = option.title || option.label;
      menu.append(row);
    });
    if (!menu.children.length) { const empty = document.createElement("div"); empty.className = "da-select-empty"; empty.textContent = "暂无选项"; menu.append(empty); }
    view.lucide?.createIcons({ root: menu });
    markActive();
  }
  let selectedIconName = "";
  function refresh() {
    if (disposed) return;
    const current = options();
    for (const [option, restore] of optionRestores) if (!current.includes(option)) { restore(); optionRestores.delete(option); }
    current.forEach(option => { if (!optionRestores.has(option)) optionRestores.set(option, observeSelectProperty(option, "selected", refresh)); });
    text.textContent = current.filter(option => option.selected).map(option => option.label).join("、") || (select.dataset.placeholder ?? "—");
    const selectedOption = current.find(option => option.selected);
    const iconName = /^[a-z][a-z0-9-]*$/.test(selectedOption?.dataset.icon || "") ? selectedOption.dataset.icon : "";
    if (selectedIconName !== iconName) {
      trigger.querySelector(".da-select-value-icon")?.remove();
      selectedIconName = iconName;
      if (iconName) {
        const icon = document.createElement("i");
        icon.className = "da-select-value-icon";
        icon.setAttribute("data-lucide", iconName);
        icon.setAttribute("aria-hidden", "true");
        trigger.insertBefore(icon, text);
        view.lucide?.createIcons({ root: trigger });
      }
    }
    text.classList.toggle("da-select-placeholder", select.selectedIndex < 0
      || Boolean(selectedOption && selectedOption.value === "" && selectedOption.hidden));
    trigger.disabled = select.disabled || select.matches(":disabled");
    trigger.title = select.title || text.textContent;
    trigger.setAttribute("aria-required", String(select.required));
    const description = select.getAttribute("aria-describedby");
    if (description) trigger.setAttribute("aria-describedby", description);
    else trigger.removeAttribute("aria-describedby");
    const labelledBy = select.getAttribute("aria-labelledby");
    const label = select.getAttribute("aria-label") || Array.from(select.labels || []).map(selectLabelText).filter(Boolean).join(" ") || select.name || select.id;
    if (labelledBy) trigger.setAttribute("aria-labelledby", labelledBy);
    else { trigger.removeAttribute("aria-labelledby"); trigger.setAttribute("aria-label", label); }
    if (trigger.disabled) close();
    if (opened) { renderMenu(); position(); }
  }
  function open(key) {
    refresh();
    if (trigger.disabled) return;
    // Close every other enhanced control without sharing controller state across documents.
    document.dispatchEvent(new view.CustomEvent("console-select-open", { detail: select }));
    active = select.selectedIndex;
    if (key === "Home" || key === "End" || active < 0) active = nextSelectIndex(options(), active, key || "Home");
    renderMenu();
    menu.hidden = false;
    opened = true;
    try { menu.showPopover?.(); } catch { /* Fixed-position fallback for older embedded browsers. */ }
    trigger.setAttribute("aria-expanded", "true");
    position();
    markActive();
  }
  function choose(index) {
    const option = options()[index];
    if (!option || option.hidden || option.disabled || option.parentElement?.disabled) return;
    const changed = select.selectedIndex !== index;
    select.selectedIndex = index;
    close({ focus: true });
    if (changed) { select.dispatchEvent(new view.Event("input", { bubbles: true })); select.dispatchEvent(new view.Event("change", { bubbles: true })); }
  }
  on(trigger, "click", () => opened ? close() : open());
  on(trigger, "keydown", event => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!opened) open(event.key);
      else { active = nextSelectIndex(options(), active, event.key); markActive(); }
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault(); opened ? choose(active) : open();
    } else if (event.key === "Escape" && opened) { event.preventDefault(); event.stopPropagation(); close({ focus: true }); }
    else if (event.key === "Tab") close();
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now(); search = now - searchAt > 700 ? event.key : search + event.key; searchAt = now;
      if (!opened) open();
      const index = options().findIndex(option => !option.disabled && !option.hidden && !option.parentElement?.disabled && option.label.toLocaleLowerCase().startsWith(search.toLocaleLowerCase()));
      if (index >= 0) { active = index; markActive(); }
    }
  });
  on(menu, "pointerdown", event => event.preventDefault());
  on(menu, "click", event => { const row = event.target.closest("[data-index]"); if (row) { event.preventDefault(); choose(Number(row.dataset.index)); } });
  on(document, "pointerdown", event => { if (!host.contains(event.target)) close(); }, { capture: true });
  on(document, "console-select-open", event => { if (event.detail !== select) close(); });
  on(document, "scroll", event => { if (!menu.contains(event.target)) position(); }, { capture: true });
  on(view, "resize", position);
  on(select, "focus", () => trigger.focus());
  on(select, "change", refresh);
  on(select, "input", refresh);
  on(select, "invalid", event => { event.preventDefault(); trigger.focus(); trigger.setAttribute("aria-invalid", "true"); });
  on(select, "change", () => trigger.removeAttribute("aria-invalid"));
  if (select.form) on(select.form, "reset", () => view.queueMicrotask(refresh));
  if (select.closest("dialog")) on(select.closest("dialog"), "close", () => close());
  restores.push(observeSelectProperty(select, "value", refresh), observeSelectProperty(select, "selectedIndex", refresh));
  const observer = new view.MutationObserver(refresh);
  observer.observe(select, { subtree: true, childList: true, characterData: true, attributes: true });
  if (view.ResizeObserver) { resizeObserver = new view.ResizeObserver(position); resizeObserver.observe(trigger); }
  const instance = { select, trigger, menu, refresh, open, close, destroy() {
    if (disposed) return;
    close(); disposed = true; abort.abort(); observer.disconnect(); resizeObserver?.disconnect();
    restores.forEach(restore => restore()); optionRestores.forEach(restore => restore()); optionRestores.clear();
    select.classList.remove("da-select-native"); select.removeAttribute("aria-hidden");
    if (originalTabIndex === null) select.removeAttribute("tabindex"); else select.setAttribute("tabindex", originalTabIndex);
    if (host.contains(select)) host.before(select);
    host.remove(); instances.delete(select);
  } };
  instances.set(select, instance);
  refresh();
  view.lucide?.createIcons({ root: host });
  return instance;
}

export function initConsoleSelects(root = document) {
  const document = root.ownerDocument || root, view = document.defaultView;
  const active = new Set();
  function refresh() {
    for (const instance of active) if (!root.contains(instance.select)) { instance.destroy(); active.delete(instance); }
    root.querySelectorAll("select").forEach(select => { const instance = enhanceSelect(select); if (instance) { active.add(instance); instance.refresh(); } });
  }
  const observer = new view.MutationObserver(records => {
    // Ignore adapter UI mutations; only source selects or newly inserted subtrees matter.
    if (records.some(record => record.target.closest?.("select") || Array.from(record.addedNodes).some(node => node.nodeType === 1 && (node.matches("select") || node.querySelector("select")))
      || Array.from(record.removedNodes).some(node => node.nodeType === 1 && (node.matches("select, .da-select") || node.querySelector("select"))))) refresh();
  });
  refresh();
  observer.observe(root, { subtree: true, childList: true });
  const onPageHide = event => {
    if (event.persisted) active.forEach(instance => instance.close());
    else destroy();
  };
  const destroy = () => { observer.disconnect(); active.forEach(instance => instance.destroy()); active.clear(); view.removeEventListener("pagehide", onPageHide); };
  view.addEventListener("pagehide", onPageHide);
  return { refresh, destroy };
}
