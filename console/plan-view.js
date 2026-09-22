import { planPanelState } from "./assistant-presentation.js?v=20260912-21";
const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const icon = (name) => `<i data-lucide="${name}" aria-hidden="true"></i>`;

export function renderPlanItems(items) {
  return items.map((item) => {
    const status = ["pending", "in_progress", "completed"].includes(item.status) ? item.status : "pending";
    const display = status === "in_progress" && item.active_form ? item.active_form : item.content;
    const name = status === "completed" ? "circle-check" : status === "in_progress" ? "loader-circle" : "circle";
    return `<li class="composer-plan-item status-${status}"><span class="composer-plan-status">${icon(name)}</span><span>${escapeHtml(display)}</span></li>`;
  }).join("");
}

export function renderPlanSnapshot(snapshot) {
  const presentation = planPanelState(snapshot);
  if (!presentation) return "";
  return `<details class="composer-plan trajectory-plan" open><summary class="composer-plan-summary"><span class="composer-plan-chevron">${icon("chevron-right")}</span><span class="composer-plan-title">Plan</span><span class="composer-plan-progress">${presentation.label}</span></summary><ul class="composer-plan-items">${renderPlanItems(presentation.plan.items)}</ul></details>`;
}
