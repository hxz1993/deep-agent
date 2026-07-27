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

function globalSettingsGroupHtml(group) {
  const items = Array.isArray(group?.items) ? group.items : [];
  return `<section class="global-settings-group">
    <h3>${escapeHtml(group?.label || "未命名分组")}</h3>
    <div class="table-wrap">
      <table class="data-table global-settings-table">
        <thead>
          <tr>
            <th>配置项</th>
            <th>当前值</th>
            <th>环境变量</th>
            <th>来源</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map(
              (item) => `<tr>
                <td><strong>${escapeHtml(item.label)}</strong></td>
                <td class="global-setting-value">${escapeHtml(globalSettingValue(item))}</td>
                <td><code>${escapeHtml(item.env_name)}</code></td>
                <td><span class="global-setting-source source-${escapeHtml(item.source)}">${escapeHtml(globalSettingSourceLabel(item.source))}</span></td>
                <td class="global-setting-description">${escapeHtml(item.description)}</td>
              </tr>`,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  </section>`;
}

function renderGlobalSettings(groups) {
  const target = document.getElementById("globalSettingsGroups");
  if (!target) return;
  if (!Array.isArray(groups) || groups.length === 0) {
    target.innerHTML = `<div class="global-settings-state">暂无可展示的全局配置。</div>`;
    return;
  }
  target.innerHTML = groups.map(globalSettingsGroupHtml).join("");
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
  async refresh() {
    const target = document.getElementById("globalSettingsGroups");
    if (!target) return;
    target.innerHTML = `<div class="global-settings-state">正在加载全局配置…</div>`;
    try {
      const response = await apiFetch("/api/v1/global-settings");
      renderGlobalSettings(response?.groups);
    } catch (error) {
      renderGlobalSettingsError(error);
    }
  },
};

window.globalSettingsAdmin = globalSettingsAdmin;
window.__GlobalSettingsUI = {
  sourceLabel: globalSettingSourceLabel,
  settingValue: globalSettingValue,
  groupHtml: globalSettingsGroupHtml,
};

document.addEventListener("DOMContentLoaded", () => {
  document
    .getElementById("refreshGlobalSettingsBtn")
    ?.addEventListener("click", () => globalSettingsAdmin.refresh());
  document
    .getElementById("globalSettingsGroups")
    ?.addEventListener("click", (event) => {
      if (event.target.closest("[data-retry-global-settings]")) {
        globalSettingsAdmin.refresh();
      }
    });
  globalSettingsAdmin.refresh();
});
