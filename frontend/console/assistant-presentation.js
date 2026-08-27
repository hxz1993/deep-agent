export function processDisclosureLabel({ pending = false, running = false } = {}) {
  if (pending) return "处理过程 · 等待操作";
  if (running) return "处理过程 · 处理中";
  return "处理过程";
}

export const PLANNING_TOOL_NAMES = new Set(["write_plan"]);

export function isPlanningToolEvent(event = {}) {
  return PLANNING_TOOL_NAMES.has(event.tool);
}

export function normalizePlanSnapshot(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.items)) return null;
  const items = snapshot.items
    .filter((item) => item && typeof item === "object")
    .map((item, index) => ({
      id: String(item.id || `plan-item-${index + 1}`),
      content: String(item.content || "未命名任务"),
      active_form: item.active_form ? String(item.active_form) : null,
      status: String(item.status || "pending"),
    }));
  if (!items.length) return null;
  const completed = items.filter((item) => item.status === "completed").length;
  return { items, total: items.length, completed };
}

export function planPanelState(snapshot) {
  const plan = normalizePlanSnapshot(snapshot);
  if (!plan) return null;
  return {
    plan,
    label: `${plan.completed}/${plan.total}`,
  };
}
