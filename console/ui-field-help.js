/** Small field explanations share a focusable question icon and a top-layer tooltip. */
export function initFieldHelp(root = document) {
  let active = null;
  const view = root.defaultView || window;
  const hide = () => {
    if (!active) return;
    const { tooltip } = active;
    if (tooltip.matches(":popover-open")) tooltip.hidePopover();
    tooltip.classList.remove("is-open");
    active = null;
  };
  const show = (trigger) => {
    if (!trigger || trigger.classList.contains("hidden")) return;
    if (active?.trigger === trigger) return;
    hide();
    const tooltip = trigger.querySelector(".field-help-tooltip");
    if (!tooltip) return;
    active = { trigger, tooltip };
    tooltip.setAttribute("popover", "manual");
    tooltip.classList.add("is-open");
    tooltip.showPopover?.();
    const bounds = trigger.getBoundingClientRect();
    const width = tooltip.offsetWidth;
    const height = tooltip.offsetHeight;
    const margin = 12;
    const left = Math.max(margin, Math.min(view.innerWidth - width - margin, bounds.left + bounds.width / 2 - width / 2));
    const above = bounds.top - height - 8;
    const top = above >= margin ? above : Math.min(view.innerHeight - height - margin, bounds.bottom + 8);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(margin, top)}px`;
  };
  const over = (event) => show(event.target.closest?.(".field-help"));
  const out = (event) => {
    if (!active || active.trigger.contains(event.relatedTarget)) return;
    if (active.trigger.contains(event.target)) hide();
  };
  const keydown = (event) => {
    if (event.key !== "Escape" || !active) return;
    event.preventDefault();
    event.stopPropagation();
    hide();
  };
  root.addEventListener("pointerover", over);
  root.addEventListener("pointerout", out);
  root.addEventListener("focusin", over);
  root.addEventListener("focusout", out);
  root.addEventListener("keydown", keydown, true);
  root.addEventListener("scroll", hide, true);
  root.addEventListener("close", hide, true);
  view.addEventListener("resize", hide);
  return () => {
    hide();
    root.removeEventListener("pointerover", over);
    root.removeEventListener("pointerout", out);
    root.removeEventListener("focusin", over);
    root.removeEventListener("focusout", out);
    root.removeEventListener("keydown", keydown, true);
    root.removeEventListener("scroll", hide, true);
    root.removeEventListener("close", hide, true);
    view.removeEventListener("resize", hide);
  };
}
