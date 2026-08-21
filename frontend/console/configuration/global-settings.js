function globalSettingSourceLabel(source) {
  return source === "environment" ? "环境配置" : "代码默认值";
}

function globalSettingValue(item) {
  const display = item?.display_value == null ? "—" : String(item.display_value);
  if (
    item?.value === null ||
    item.value === "" ||
    (Array.isArray(item.value) && item.value.length === 0)
  ) {
    return display;
  }
  return item?.unit ? `${display} ${item.unit}` : display;
}

function globalSettingMatches(group, item, query) {
  const normalized = String(query || "").trim().toLocaleLowerCase();
  if (!normalized) return true;
  return [
    group?.label,
    item?.label,
    item?.env_name,
    item?.description,
    item?.display_value,
    globalSettingSourceLabel(item?.source),
  ].some((value) => String(value || "").toLocaleLowerCase().includes(normalized));
}

function globalSettingsTableHtml(groups, query = "") {
  const rows = (Array.isArray(groups) ? groups : []).flatMap((group) => {
    const items = (Array.isArray(group?.items) ? group.items : []).filter((item) =>
      globalSettingMatches(group, item, query),
    );
    if (!items.length) return [];
    return [
      `<tr class="global-settings-group-row"><th colspan="5">${escapeHtml(group?.label || "未命名分组")}</th></tr>`,
      ...items.map(
        (item) => `<tr>
          <td><strong>${escapeHtml(item.label)}</strong></td>
          <td class="global-setting-value">${escapeHtml(globalSettingValue(item))}</td>
          <td><code>${escapeHtml(item.env_name)}</code></td>
          <td><span class="global-setting-source source-${escapeHtml(item.source)}">${escapeHtml(globalSettingSourceLabel(item.source))}</span></td>
          <td class="global-setting-description">${escapeHtml(item.description)}</td>
        </tr>`,
      ),
    ];
  });
  if (!rows.length) return "";
  return `<div class="table-wrap">
    <table class="data-table global-settings-table">
      <thead><tr><th>配置项</th><th>当前值</th><th>环境变量</th><th>来源</th><th>说明</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>
  </div>`;
}

function renderGlobalSettings(groups, query = "") {
  const target = document.getElementById("globalSettingsGroups");
  if (!target) return;
  if (!Array.isArray(groups) || groups.length === 0) {
    target.innerHTML = `<div class="global-settings-state">暂无可展示的全局配置。</div>`;
    return;
  }
  target.innerHTML = globalSettingsTableHtml(groups, query) || `<div class="global-settings-state">没有匹配的配置项。</div>`;
}

function renderGlobalSettingsError(error) {
  const target = document.getElementById("globalSettingsGroups");
  if (!target) return;
  const message = error instanceof Error ? error.message : String(error);
  target.innerHTML = `<div class="global-settings-state error">
    <span>${escapeHtml(message || "全局配置加载失败")}</span>
    <button class="btn" type="button" data-retry-global-settings>重新加载</button>
  </div>`;
}

const globalSettingsAdmin = {
  groups: [],
  query: "",
  async refresh() {
    const target = document.getElementById("globalSettingsGroups");
    if (!target) return;
    target.innerHTML = `<div class="global-settings-state">正在加载全局配置…</div>`;
    try {
      const response = await apiFetch("/api/v1/global-settings");
      this.groups = Array.isArray(response?.groups) ? response.groups : [];
      renderGlobalSettings(this.groups, this.query);
    } catch (error) {
      renderGlobalSettingsError(error);
    }
  },
  search(query) {
    this.query = String(query || "");
    renderGlobalSettings(this.groups, this.query);
  },
};

window.globalSettingsAdmin = globalSettingsAdmin;
window.__GlobalSettingsUI = {
  sourceLabel: globalSettingSourceLabel,
  settingValue: globalSettingValue,
  tableHtml: globalSettingsTableHtml,
  matches: globalSettingMatches,
};

document.addEventListener("DOMContentLoaded", () => {
  document
    .getElementById("globalSettingsSearch")
    ?.addEventListener("input", (event) => globalSettingsAdmin.search(event.target.value));
  document
    .getElementById("globalSettingsGroups")
    ?.addEventListener("click", (event) => {
      if (event.target.closest("[data-retry-global-settings]")) {
        globalSettingsAdmin.refresh();
      }
    });
});
