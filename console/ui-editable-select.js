/* A single input for suggested values, arbitrary codes and clearing to an empty value. */
(function () {
  let nextId = 0;
  window.createEditableSelect = function createEditableSelect(input, { options = [] } = {}) {
    const doc = input.ownerDocument;
    const root = doc.createElement("div");
    root.className = "da-editable-select";
    input.before(root);
    root.append(input);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    const menu = doc.createElement("div");
    menu.className = "da-select-menu da-editable-menu";
    menu.id = `editable-select-${++nextId}`;
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", input.getAttribute("aria-label") || "可选项");
    menu.setAttribute("popover", "manual");
    menu.hidden = true;
    input.setAttribute("aria-controls", menu.id);
    const clear = doc.createElement("button");
    clear.type = "button";
    clear.className = "da-editable-clear";
    clear.setAttribute("aria-label", "清空选择");
    clear.innerHTML = '<i data-lucide="x" aria-hidden="true"></i>';
    const toggle = doc.createElement("button");
    toggle.type = "button";
    toggle.className = "da-editable-toggle";
    toggle.setAttribute("aria-label", "展开选项");
    toggle.setAttribute("aria-controls", menu.id);
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = '<i data-lucide="chevron-down" aria-hidden="true"></i>';
    root.append(clear, toggle, menu);
    let opened = false;
    let disposed = false;
    let committing = false;
    let entries = [];
    let active = -1;
    const listeners = [];
    function on(target, type, handler, capture = false) {
      target.addEventListener(type, handler, capture);
      listeners.push(() => target.removeEventListener(type, handler, capture));
    }
    function locked() { return input.readOnly || input.disabled; }
    function refresh() {
      clear.hidden = !input.value || locked();
      toggle.disabled = locked();
      root.classList.toggle("readonly", locked());
      if (locked()) close();
    }
    function place() {
      const rect = root.getBoundingClientRect();
      const viewport = doc.documentElement;
      const below = viewport.clientHeight - rect.bottom - 8;
      const above = rect.top - 8;
      const height = Math.min(240, Math.max(below, above));
      menu.style.width = `${Math.min(rect.width, viewport.clientWidth - 16)}px`;
      menu.style.maxHeight = `${height}px`;
      menu.style.left = `${Math.max(8, Math.min(rect.left, viewport.clientWidth - rect.width - 8))}px`;
      menu.style.top = `${below >= Math.min(240, menu.scrollHeight) || below >= above ? rect.bottom + 4 : Math.max(8, rect.top - Math.min(menu.scrollHeight, height) - 4)}px`;
    }
    function close() {
      if (!opened) return;
      opened = false;
      if (menu.matches(":popover-open")) menu.hidePopover();
      menu.hidden = true;
      input.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }
    function highlight(index) {
      active = index;
      Array.from(menu.children).forEach((row, i) => row.classList.toggle("active", i === active));
      const row = menu.children[active];
      if (row) {
        input.setAttribute("aria-activedescendant", row.id);
        row.scrollIntoView({ block: "nearest" });
      } else input.removeAttribute("aria-activedescendant");
    }
    function choose(value) {
      if (locked()) return;
      input.value = value;
      close();
      refresh();
      committing = true;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      committing = false;
      input.focus();
      close();
    }
    function render(filter = "") {
      const query = filter.toLocaleLowerCase().trim();
      entries = options.filter((item) =>
        !query || `${item.label} ${item.value}`.toLocaleLowerCase().includes(query));
      menu.replaceChildren();
      entries.forEach((item, index) => {
        const row = doc.createElement("div");
        row.id = `${menu.id}-${index}`;
        row.className = "da-select-option";
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(input.value === item.value));
        row.dataset.index = String(index);
        const label = doc.createElement("span");
        label.textContent = item.label;
        row.append(label);
        if (item.value) {
          const code = doc.createElement("code");
          code.textContent = item.value;
          row.append(code);
        }
        menu.append(row);
      });
      active = -1;
      input.removeAttribute("aria-activedescendant");
    }
    function open(filter = "") {
      if (disposed || locked()) return;
      render(filter);
      menu.hidden = false;
      if (!opened) {
        opened = true;
        menu.showPopover();
      }
      input.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-expanded", "true");
      place();
    }
    on(input, "click", () => { if (!opened) open(); });
    on(input, "input", () => { refresh(); if (!committing) open(input.value); });
    on(toggle, "click", () => { if (opened) close(); else open(); input.focus(); });
    on(clear, "click", () => choose(""));
    on(menu, "pointerdown", (event) => event.preventDefault());
    on(menu, "click", (event) => {
      // Prevent an enclosing label from forwarding another click to the input.
      event.preventDefault();
      const row = event.target.closest("[data-index]");
      if (row) choose(entries[Number(row.dataset.index)].value);
    });
    on(root, "keydown", (event) => {
      if (locked() || event.isComposing) return;
      if (event.key === "Escape" && opened) {
        event.preventDefault(); event.stopPropagation(); close();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!opened) open();
        if (!entries.length) return;
        highlight(active < 0
          ? (event.key === "ArrowUp" ? entries.length - 1 : 0)
          : (active + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % entries.length);
      } else if (event.key === "Enter" && opened) {
        event.preventDefault();
        if (active >= 0) choose(entries[active].value);
        else close();
      } else if (event.key === "Tab") close();
    });
    on(doc, "pointerdown", (event) => { if (!root.contains(event.target)) close(); }, true);
    on(root, "focusout", (event) => { if (!root.contains(event.relatedTarget)) close(); });
    on(doc, "scroll", (event) => { if (opened && !menu.contains(event.target)) close(); }, true);
    on(window, "resize", close);
    const dialog = input.closest("dialog");
    if (dialog) on(dialog, "close", close);
    on(window, "pagehide", (event) => {
      // Back/forward cache resumes this controller without reinitializing it.
      if (event.persisted) close();
      else destroy();
    });
    function destroy() {
      if (disposed) return;
      close();
      disposed = true;
      listeners.forEach((remove) => remove());
      root.before(input);
      root.remove();
      for (const attribute of ["role", "aria-autocomplete", "aria-expanded", "aria-controls", "aria-activedescendant"]) input.removeAttribute(attribute);
    }
    window.lucide?.createIcons({ root });
    refresh();
    return { refresh, close, destroy };
  };
})();
