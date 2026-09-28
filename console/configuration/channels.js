const channelAdminState = {
  accounts: [],
  agents: [],
  runtime: null,
  editingId: null,
  channelType: "wecom",
  registrationMode: "scan",
  view: "accounts",
  applications: [],
  applicationLoadGeneration: 0,
  saving: false,
  apiKeyDraft: "",
  clearApiKey: false,
};

const channelPlatforms = {
  feishu: { name: "飞书", logo: "feishu.ico", consoleUrl: "https://open.feishu.cn/app", consoleLabel: "飞书开发者后台", credential: "App ID", secret: "App Secret", subtitle: "飞书应用机器人" },
  wecom: { name: "企微", logo: "wecom.png", consoleUrl: "https://work.weixin.qq.com/", consoleLabel: "企业微信管理后台", credential: "Bot ID", secret: "Secret", subtitle: "企微智能机器人" },
  dingtalk: { name: "钉钉", logo: "dingtalk.svg", consoleUrl: "https://open-dev.dingtalk.com/", consoleLabel: "钉钉开发者后台", credential: "Client ID", secret: "Client Secret", subtitle: "钉钉应用机器人" },
};
let channelRegistration = null;
function renderChannelRegistration(data) {
  const status = document.getElementById("channelRegistrationScanStatus");
  const qr = document.getElementById("channelRegistrationQr");
  const link = document.getElementById("channelRegistrationAuthLink");
  const button = document.getElementById("channelRegistrationStartScan");
  const retry = document.getElementById("channelRegistrationRetryScan");
  const authorized = data.status === "authorized";
  document.getElementById("channelRegistrationAuthorized").hidden = !authorized;
  document.getElementById("channelRegistrationSaveReminder").hidden = !authorized;
  const retryable = ["failed", "expired", "denied", "cancelled"].includes(data.status);
  retry.hidden = !(retryable || ["waiting", "scanned", "selecting", "creating"].includes(data.status));
  const feishu = channelAdminState.channelType === "feishu";
  const dingtalk = channelAdminState.channelType === "dingtalk";
  const platform = channelPlatforms[channelAdminState.channelType];
  const labels = { scanned: "扫码成功，请在钉钉完成机器人创建并确认连接。", selecting: "扫码成功，请在钉钉选择机器人并点击「立即连接」。", creating: "扫码已确认，钉钉正在创建应用，请稍候，无需重复扫码。", starting: "正在准备二维码…", waiting: dingtalk ? "使用钉钉扫码，完成应用创建和授权，然后保存渠道。" : feishu ? "使用飞书扫码，完成应用创建和授权。修改渠道名称后可重新生成。" : "使用手机企业微信扫码，创建或关联智能机器人。请在企业微信确认机器人名称。", authorized: "请补齐渠道名称、关联 Agent 和会话关联用户，点击右下角「保存」。保存后启用渠道，机器人才能接收消息。", expired: "二维码已过期，请重新生成。", denied: "授权已拒绝，可重新扫码或使用已有凭据。", failed: "授权失败，请重试。", cancelled: "授权已取消。" };
  status.textContent = data.error || labels[data.status] || (dingtalk ? "使用钉钉扫码创建应用；确认授权后补齐信息并保存渠道。" : feishu ? "可先填写渠道名称，生成二维码后用手机飞书扫码。应用名称会自动预填，确认创建和授权后保存渠道。" : "使用手机企业微信扫码，创建或关联智能机器人。机器人名称需在企业微信确认，完成后请保存渠道。");
  button.disabled = ["starting", "waiting", "scanned", "selecting", "creating"].includes(data.status);
  button.hidden = retryable || ["waiting", "scanned", "selecting", "creating", "authorized"].includes(data.status);
  button.textContent = data.status === "starting" ? "正在生成…" : "生成二维码";
  qr.hidden = true; qr.innerHTML = ""; link.hidden = true; link.removeAttribute("href");
  if (data.status === "waiting" && data.verification_url) {
    try {
      const url = new URL(data.verification_url);
      const officialHost = feishu
        ? ["feishu.cn", "larksuite.com"].some(domain => url.hostname === domain || url.hostname.endsWith(`.${domain}`))
        : dingtalk ? (url.hostname === "dingtalk.com" || url.hostname.endsWith(".dingtalk.com")) : url.hostname === "work.weixin.qq.com";
      if (url.protocol !== "https:" || url.username || url.password || url.port || !officialHost) throw new Error("授权地址不是平台官方地址");
      const code = qrcode(0, "H"); code.addData(url.href); code.make();
      // Reserve whole modules for the brand, rather than overlaying a rounded tile.
      const count = code.getModuleCount(), quiet = 4, size = count + quiet * 2;
      const gap = Math.max(5, Math.floor(count * .14) | 1);
      const first = (count - gap) / 2, last = first + gap;
      const paths = [];
      for (let row = 0; row < count; row++) for (let col = 0; col < count; col++) {
        if (row >= first && row < last && col >= first && col < last) continue;
        if (code.isDark(row, col)) paths.push(`M${col + quiet},${row + quiet}h1v1h-1z`);
      }
      const inset = .6, brandSize = gap - inset * 2, brandStart = first + quiet + inset;
      qr.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="${platform.name}授权二维码"><rect width="${size}" height="${size}" fill="white"/><path d="${paths.join("")}" fill="black" shape-rendering="crispEdges"/><image href="./assets/channel-logos/${platform.logo}" x="${brandStart}" y="${brandStart}" width="${brandSize}" height="${brandSize}"/></svg>`;
      qr.hidden = false;
      // WeCom's QR target is a mobile-client flow, not a desktop authorization page.
      if (feishu) { link.href = url.href; link.hidden = false; }
    } catch (error) { status.textContent = error.message; button.hidden = true; retry.hidden = false; }
  }
}
function setChannelRegistrationMode(mode) {
  if (channelAdminState.saving) return;
  channelAdminState.registrationMode = mode === "scan" ? "scan" : "manual";
  const scan = !channelAdminState.editingId && channelAdminState.registrationMode === "scan";
  document.querySelectorAll("[data-registration-mode]").forEach(button => {
    const selected = button.dataset.registrationMode === channelAdminState.registrationMode;
    button.classList.toggle("active", selected); button.setAttribute("aria-pressed", String(selected));
  });
  document.getElementById("channelRegistrationScan").hidden = !scan;
  document.getElementById("channelRegistrationManualHint").hidden = scan;
  ["channelBotId", "channelSecret"].forEach(id => {
    const input = document.getElementById(id); input.closest(".field").hidden = scan; input.disabled = scan;
  });
  document.getElementById("channelBotId").required = !scan;
  document.getElementById("channelSecret").required = !scan && !channelAdminState.editingId;
}
function prepareChannelPlatform(type) {
  clearChannelPermissionCheck();
  channelRegistration?.reset();
  channelAdminState.channelType = Object.hasOwn(channelPlatforms, type) ? type : "wecom";
  const feishu = channelAdminState.channelType === "feishu";
  const dingtalk = channelAdminState.channelType === "dingtalk";
  const platform = channelPlatforms[channelAdminState.channelType];
  channelRegistration = window.createChannelRegistration({ channelType: channelAdminState.channelType, onChange: renderChannelRegistration });
  renderChannelRegistration({});
  const consoleLink = document.getElementById("channelRegistrationConsoleLink");
  consoleLink.href = platform.consoleUrl;
  consoleLink.textContent = `前往${platform.consoleLabel} ↗`;
  document.getElementById("channelRegistrationMode").setAttribute("aria-label", `${platform.name}接入方式`);
  document.getElementById("channelRegistrationManualLabel").textContent = "手动关联";
  document.getElementById("channelPlatformSubtitle").textContent = platform.subtitle;
  document.getElementById("channelBotIdLabel").textContent = platform.credential;
  document.getElementById("channelBotId").placeholder = `请输入 ${platform.credential}`;
  document.getElementById("channelConnectionHelp").innerHTML = dingtalk
    ? '<strong>使用钉钉企业内部应用</strong><p>在<a class="channel-platform-link" href="https://open-dev.dingtalk.com/" target="_blank" rel="noopener noreferrer" title="打开钉钉开发者后台（新标签页）">钉钉开发者后台</a>获取 Client ID 和 Client Secret，开启机器人能力。</p><p>消息接收选择 Stream 模式，配置权限并发布应用，无需公网回调。</p>'
    : feishu
    ? '<strong>使用飞书企业自建应用</strong><p>在<a class="channel-platform-link" href="https://open.feishu.cn/app" target="_blank" rel="noopener noreferrer" title="打开飞书开发者后台（新标签页）">飞书开放平台</a>获取 App ID 和 App Secret，开启机器人能力。</p><p>事件订阅选择长连接，添加接收消息事件并发布应用。</p>'
    : '<strong>使用企微智能机器人</strong><p>在企业微信创建智能机器人，选择 API 模式，获取 Bot ID 和 Secret。</p><p>通过长连接接收消息，无需配置公网回调地址。</p><a class="channel-platform-link" href="https://work.weixin.qq.com/" target="_blank" rel="noopener noreferrer">前往企业微信管理后台 ↗</a>';
  document.getElementById("channelSecretLabel").textContent = platform.secret;
  document.getElementById("channelName").placeholder = `例如：${platform.name}助手`;
  document.getElementById("channelConnectionTitle").textContent = `${platform.name}连接`;
  document.getElementById("channelRegistrationMode").hidden = Boolean(channelAdminState.editingId);
  document.getElementById("channelRegistrationSetup").hidden = Boolean(channelAdminState.editingId);
  document.getElementById(!channelAdminState.editingId ? "channelRegistrationCredentials" : "channelCredentialHome").append(document.getElementById("channelCredentialLayout"));
  document.getElementById("channelMemberTab").hidden = false;
  document.getElementById("channelDingtalkUserDetails").hidden = !dingtalk;
  document.getElementById("channelUserDetailsLabel").textContent = dingtalk ? "启用用户详情" : "启用用户详情查询";
  document.getElementById("channelWecomMemberToggle").hidden = false;
  document.getElementById("channelUserDetailsHint").textContent = dingtalk ? "使用消息自带的用户昵称" : feishu ? "使用当前飞书应用查询姓名" : "获取企微成员账号和姓名";
  document.getElementById("channelFeishuUserDetails").hidden = true;
  setChannelRegistrationMode(!channelAdminState.editingId ? "scan" : "manual");
}

const channelPermissionCheck = { generation: 0, controller: null };

function clearChannelPermissionCheck() {
  channelPermissionCheck.generation++;
  channelPermissionCheck.controller?.abort();
  channelPermissionCheck.controller = null;
  const result = document.getElementById("channelUserDetailsCheckResult");
  if (result) { result.textContent = ""; result.hidden = true; delete result.dataset.state; result.removeAttribute?.("title"); }
  const button = document.getElementById("channelUserDetailsCheckBtn");
  if (button) { button.disabled = false; button.textContent = "检测权限"; }
}

function channelCredentialsAreSaved() {
  const account = channelAdminState.accounts.find(item => item.id === channelAdminState.editingId);
  const secretInput = document.getElementById("channelSecret");
  const secret = secretInput.value.trim();
  return account && document.getElementById("channelBotId").value.trim() === account.bot_id
    && (!secret || secret === secretInput.dataset.maskedValue);
}

function officialFeishuLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "open.feishu.cn" && !url.username && !url.password && !url.port ? url.href : "";
  } catch { return ""; }
}

async function checkChannelUserDetails() {
  if (channelAdminState.channelType !== "feishu" || channelAdminState.saving || document.getElementById("channelUserDetailsEnabled").getAttribute("aria-checked") !== "true") return;
  clearChannelPermissionCheck();
  const result = document.getElementById("channelUserDetailsCheckResult");
  result.hidden = false;
  if (!channelCredentialsAreSaved()) {
    result.dataset.state = "missing";
    result.textContent = "请先保存渠道及当前应用凭据，再检测权限。";
    return;
  }
  const button = document.getElementById("channelUserDetailsCheckBtn");
  const accountId = channelAdminState.editingId;
  const generation = channelPermissionCheck.generation;
  const controller = new AbortController();
  channelPermissionCheck.controller = controller;
  const current = () => generation === channelPermissionCheck.generation
    && channelAdminState.editingId === accountId && document.getElementById("channelAccountModal").open
    && channelCredentialsAreSaved();
  button.disabled = true; button.textContent = "检测中…";
  result.textContent = "正在检测已保存应用的权限…";
  try {
    const data = await apiFetch(`/api/v1/channel-accounts/${encodeURIComponent(accountId)}/user-details/check`, { method: "POST", signal: controller.signal });
    if (!current()) return;
    const status = { authorized: "已授权", missing: "尚未授权", error: "检测失败" }[data.status] || "检测失败";
    const permissions = Array.isArray(data.permissions) ? data.permissions : [];
    const consoleUrl = officialFeishuLink(data.console_url), docsUrl = officialFeishuLink(data.docs_url);
    result.dataset.state = ["authorized", "missing"].includes(data.status) ? data.status : "error";
    result.innerHTML = `<strong>${escapeHtml(status)}</strong><p class="channel-permission-summary" title="${escapeHtml(data.message || "")}">${escapeHtml(data.status === "authorized" ? "姓名读取权限已授权；请确认通讯录范围及应用发布状态。" : data.message || "请核对姓名读取权限及应用发布状态。")}</p><p class="channel-permission-scopes" title="${escapeHtml(permissions.map(item => `${item.scope}: ${item.authorized ? "已授权" : "未授权"}`).join("；"))}">${permissions.map(item => `<span>${escapeHtml(item.name || item.scope)}：${item.authorized ? "已授权" : "未授权"}</span>`).join(" · ")}</p><p>${consoleUrl ? `<a class="channel-platform-link" href="${escapeHtml(consoleUrl)}" target="_blank" rel="noopener noreferrer">前往权限管理 ↗</a>` : ""} ${docsUrl ? `<a class="channel-platform-link" href="${escapeHtml(docsUrl)}" target="_blank" rel="noopener noreferrer">配置说明 ↗</a>` : ""}</p>`;
  } catch (error) {
    if (current()) { result.dataset.state = "error"; result.textContent = error.message || "权限检测失败，请重试。"; result.title = result.textContent; }
  } finally {
    if (generation === channelPermissionCheck.generation) {
      channelPermissionCheck.controller = null;
      button.disabled = channelAdminState.saving; button.textContent = "检测权限";
    }
  }
}

const channelUserQuery = { generation: 0, controller: null };

function clearChannelUserQuery() {
  channelUserQuery.generation++;
  channelUserQuery.controller?.abort();
  channelUserQuery.controller = null;
  const result = document.getElementById("channelSessionUserResult");
  if (result) { result.hidden = true; result.innerHTML = ""; }
  document.getElementById("queryChannelSessionUserBtn")?.setAttribute("aria-expanded", "false");
}

function showChannelUserQuery(state, content) {
  const result = document.getElementById("channelSessionUserResult");
  if (!result) return;
  result.dataset.state = state;
  result.innerHTML = content;
  result.hidden = false;
  document.getElementById("queryChannelSessionUserBtn")?.setAttribute("aria-expanded", "true");
}

async function queryChannelSessionUser() {
  clearChannelUserQuery();
  const input = document.getElementById("channelSessionUserId");
  const userId = input.value.trim();
  if (!userId) { showChannelUserQuery("error", "请先输入平台 User ID"); input.focus(); return; }
  const generation = channelUserQuery.generation;
  const controller = new AbortController();
  channelUserQuery.controller = controller;
  showChannelUserQuery("loading", "正在查询用户…");
  const stillCurrent = () => generation === channelUserQuery.generation
    && input.value.trim() === userId && document.getElementById("channelAccountModal")?.open;
  try {
    const data = await apiFetch(`/api/v1/diagnostics/sessions/user-profile?user_id=${encodeURIComponent(userId)}`, { signal: controller.signal });
    if (!stillCurrent()) return;
    const user = data?.user;
    if (!user || user.user_id !== userId || user.resolution === "unavailable") {
      showChannelUserQuery("error", "查询暂不可用，请稍后重试");
    } else if (user.resolution !== "resolved") {
      showChannelUserQuery("missing", "查不到这个用户<small>请核对 User ID 与当前环境。</small>");
    } else {
      showChannelUserQuery("resolved", `<strong>${escapeHtml(user.username || userId)}</strong>${escapeHtml(user.email || "未设置邮箱")}<small>已查到用户。</small>`);
    }
  } catch (error) {
    if (!stillCurrent() || error.name === "AbortError") return;
    const message = error.status === 401 ? "登录已失效，请重新登录" : error.status === 403
      ? "无权查询用户资料" : "查询暂不可用，请稍后重试";
    showChannelUserQuery("error", message);
  } finally {
    if (generation === channelUserQuery.generation) channelUserQuery.controller = null;
  }
}

const CHANNEL_STATE_LABELS = {
  not_deployed: "未部署",
  unreachable: "运行时不可达",
  closed: "已关闭",
  connecting: "连接中",
  connected: "已连接",
  reconnecting: "重连中",
  conflict: "连接冲突",
  failed: "连接失败",
};

function channelRuntimeFor(account) {
  const runtime = channelAdminState.runtime;
  if (!runtime?.deployed) return { state: "not_deployed", message: runtime?.message || "渠道网关未部署" };
  if (!runtime?.reachable) return { state: "unreachable", message: runtime?.message || "渠道网关不可达" };
  if (!account.enabled) return { state: "closed", message: "渠道已关闭" };
  return runtime.accounts?.find((item) => item.account_id === account.id)
    || { state: "connecting", message: "等待渠道网关加载配置" };
}

function renderChannelAgentOptions(selectedId = "") {
  const select = document.getElementById("channelAgentId");
  if (!select) return;
  select.innerHTML = channelAdminState.agents.length
    ? `<option value="" disabled hidden ${selectedId ? "" : "selected"}>请选择</option>` + channelAdminState.agents.map((agent) => `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.name)}</option>`).join("")
    : '<option value="">（请先创建 Agent）</option>';
  select.disabled = !channelAdminState.agents.length;
}

function setChannelSecretEditor({ maskedValue = "", secretSet = false, required = false } = {}) {
  const input = document.getElementById("channelSecret");
  const masked = String(maskedValue || "");
  input.dataset.maskedValue = masked;
  input.value = masked;
  input.type = masked ? "text" : "password";
  input.required = required;
  input.placeholder = masked || secretSet ? "留空则保持不变" : "请输入 Secret";
}

function channelUserDetailsSettings(settings, enabled, applicationId) {
  return { ...settings, wecom: { ...(settings?.wecom || {}), user_details_enabled: enabled, application_id: applicationId || null } };
}

function selectChannelExtension(key) {
  document.querySelectorAll("[data-channel-extension]").forEach(button => {
    const selected = button.dataset.channelExtension === key;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    document.getElementById(button.getAttribute("aria-controls")).hidden = !selected;
  });
}

function setChannelApiKeyEditor(settings = {}) {
  const input = document.getElementById("channelDeepfosApiKey");
  channelAdminState.apiKeyDraft = "";
  channelAdminState.clearApiKey = false;
  input.dataset.maskedValue = settings.api_key_masked || "";
  input.value = input.dataset.maskedValue;
  input.type = input.value ? "text" : "password";
}

function channelApiKeySettings(previous = {}) {
  const { api_key, api_key_set, api_key_masked, clear_api_key, ...settings } = previous;
  if (channelAdminState.apiKeyDraft) settings.api_key = channelAdminState.apiKeyDraft;
  else if (channelAdminState.clearApiKey) settings.clear_api_key = true;
  return settings;
}

function setChannelUserDetailsEnabled(enabled) {
  const button = document.getElementById("channelUserDetailsEnabled");
  if (!button) return;
  button.setAttribute("aria-checked", String(enabled));
  button.classList.toggle("on", enabled);
  button.classList.toggle("off", !enabled);
  setChannelExtensionStatus("channelMemberTab", enabled);
  document.getElementById("channelFeishuUserDetails").hidden = !(enabled && channelAdminState.channelType === "feishu");
  if (!enabled) clearChannelPermissionCheck();
  enabled = enabled && channelAdminState.channelType === "wecom";
  document.getElementById("channelWecomApplicationField").hidden = !enabled;
  document.getElementById("channelWecomApplicationId").required = enabled;
}

function setChannelExtensionStatus(tabId, enabled) {
  const status = document.querySelector(`#${tabId} .channel-extension-status`);
  if (!status) return;
  status.classList.toggle("on", enabled);
  status.setAttribute("aria-label", enabled ? "已开启" : "未开启");
  status.setAttribute("title", enabled ? "已开启" : "未开启");
}

function setChannelDeepfosEnabled(enabled) {
  const button = document.getElementById("channelDeepfosEnabled");
  button.setAttribute("aria-checked", String(enabled));
  button.classList.toggle("on", enabled);
  button.classList.toggle("off", !enabled);
  setChannelExtensionStatus("channelDeepfosTab", enabled);
  document.getElementById("channelDeepfosAppField").hidden = !enabled;
  const input = document.getElementById("channelDeepfosAppId");
  input.required = enabled;
  input.disabled = !enabled;
  document.getElementById("channelDeepfosApiKey").disabled = !enabled;
}

async function prepareChannelApplicationEditor(settings = {}) {
  selectChannelExtension("deepfos");
  setChannelApiKeyEditor(settings?.deepfos || {});
  document.getElementById("channelDeepfosAppId").value = settings?.deepfos?.app_id || "";
  setChannelDeepfosEnabled(settings?.deepfos?.enabled === true);
  const select = document.getElementById("channelWecomApplicationId");
  if (!select) return;
  const generation = ++channelAdminState.applicationLoadGeneration;
  if (channelAdminState.channelType !== "wecom") {
    channelAdminState.applications = [];
    setChannelUserDetailsEnabled(settings?.[channelAdminState.channelType]?.user_details_enabled === true);
    select.disabled = true;
    return;
  }
  const selectedId = settings?.wecom?.application_id || "";
  select.dataset.selectedId = selectedId;
  select.innerHTML = '<option value="" hidden disabled selected>正在加载企微应用…</option>';
  select.disabled = true;
  channelAdminState.applications = [];
  setChannelUserDetailsEnabled(settings?.wecom?.user_details_enabled === true);
  const hint = document.getElementById("channelWecomApplicationHint");
  hint.textContent = "";
  try {
    const items = await apiFetch("/api/v1/wecom-applications");
    if (generation !== channelAdminState.applicationLoadGeneration) return;
    channelAdminState.applications = items;
    select.innerHTML = `<option value="" hidden disabled ${!selectedId ? "selected" : ""}>请选择</option>${selectedId && !items.some(item => item.id === selectedId) ? `<option value="${escapeHtml(selectedId)}" hidden disabled selected>应用已不可用</option>` : ""}${items.map(item => `<option value="${escapeHtml(item.id)}" ${item.id === selectedId ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}`;
    select.disabled = !items.length;
    hint.textContent = !items.length ? "暂无企微应用，请先在渠道 → 企微应用中新建。" : "";
  } catch (error) {
    if (generation !== channelAdminState.applicationLoadGeneration) return;
    hint.textContent = `企微应用读取失败：${error.message}`;
    select.innerHTML = '<option value="" hidden disabled selected>企微应用加载失败</option>';
  }
}

const channelsAdmin = {
  selectView(view) {
    if (!["accounts", "applications"].includes(view)) return;
    if (view === "accounts" && window.wecomApplicationsAdmin?.deactivate() === false) return;
    channelAdminState.view = view;
    this.renderView();
    return view === "applications" ? window.wecomApplicationsAdmin?.refresh() : undefined;
  },
  renderView() {
    const applications = channelAdminState.view === "applications";
    document.querySelectorAll("[data-channel-view]").forEach(button => {
      const active = button.dataset.channelView === channelAdminState.view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    const description = document.getElementById("channelViewDescription");
    if (description) description.textContent = applications
      ? "配置企业微信应用，按需查询渠道用户姓名与资料。"
      : "连接外部聊天平台，将消息交给绑定的 Agent 处理。";
    const visibility = { channelAccountsView: applications, channelAccountActions: applications, channelRuntimeBanner: applications, wecomApplicationsView: !applications, newWecomApplicationBtn: !applications };
    Object.entries(visibility).forEach(([id, hidden]) => { const node = document.getElementById(id); if (node) node.hidden = hidden; });
  },
  async refresh() {
    if (channelAdminState.view === "applications") return window.wecomApplicationsAdmin?.refresh();
    const tbody = document.getElementById("channelTableBody");
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">正在加载渠道…</td></tr>';
    try {
      const [accounts, agents, runtime] = await Promise.all([
        apiFetch("/api/v1/channel-accounts").then((accounts) => {
          // Association display does not depend on Gateway deployment/health.
          window.agentsAdmin?.setChannelAccounts(accounts);
          return accounts;
        }),
        apiFetch("/api/v1/agents"),
        apiFetch("/api/v1/channel-runtime"),
      ]);
      channelAdminState.accounts = accounts;
      channelAdminState.agents = agents;
      channelAdminState.runtime = runtime;
      this.renderRuntime();
      this.renderTable();
      showDbBanner(false);
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  },

  renderRuntime() {
    const banner = document.getElementById("channelRuntimeBanner");
    const runtime = channelAdminState.runtime;
    if (!banner || !runtime) return;
    const state = !runtime.deployed ? "not_deployed" : runtime.reachable ? "connected" : "unreachable";
    banner.dataset.state = state;
    const stateLabel = { not_deployed: "未部署", connected: "运行中", unreachable: "不可达" }[state];
    banner.querySelector("strong").textContent = `网关：${stateLabel}`;
    banner.title = runtime.message || "";
  },

  openHelp() {
    this.selectHelpTab("messages");
    const dialog = document.getElementById("channelHelpModal");
    if (!dialog.open) dialog.showModal();
  },
  selectHelpTab(tab) {
    if (!["messages", "commands"].includes(tab)) return;
    document.querySelectorAll("[data-channel-help]").forEach(button => {
      const active = button.dataset.channelHelp === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    document.getElementById("channelHelpMessagesPanel").hidden = tab !== "messages";
    document.getElementById("channelHelpCommandsPanel").hidden = tab !== "commands";
    if (tab === "commands") return this.loadCommands();
  },
  async loadCommands() {
    const content = document.getElementById("channelCommandsContent");
    const retry = document.getElementById("retryChannelCommandsBtn");
    if (!content || !retry) return;
    const requestId = (this.commandsRequestId || 0) + 1;
    this.commandsRequestId = requestId;
    content.innerHTML = '<p class="field-hint" role="status">正在加载快捷命令…</p>';
    retry.hidden = true;
    try {
      const catalog = await apiFetch("/api/v1/channel-commands");
      if (this.commandsRequestId !== requestId) return;
      if (!Array.isArray(catalog?.commands) || !Array.isArray(catalog?.notes)) {
        throw new Error("快捷命令说明格式不正确，请重试。");
      }
      content.innerHTML = `<dl class="channel-command-list">${catalog.commands.map((item) =>
        `<div><dt><code>${escapeHtml(item.command)}</code></dt><dd>${escapeHtml(item.description)}</dd></div>`,
      ).join("")}</dl><ul class="channel-command-notes">${catalog.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`;
    } catch (error) {
      if (this.commandsRequestId !== requestId) return;
      content.innerHTML = `<p class="channel-command-error" role="alert">${escapeHtml(error.message)}</p>`;
      retry.hidden = false;
    }
  },

  renderTable() {
    const tbody = document.getElementById("channelTableBody");
    if (!tbody) return;
    if (!channelAdminState.accounts.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">暂无渠道配置</td></tr>';
      return;
    }
    tbody.innerHTML = channelAdminState.accounts.map((account) => {
      const runtime = channelRuntimeFor(account);
      return `<tr class="channel-list-row" data-open-channel="${escapeHtml(account.id)}" tabindex="0">
        <td class="channel-enabled-cell"><button type="button" class="model-enabled-switch ${account.enabled ? "on" : "off"}" data-toggle-channel="${escapeHtml(account.id)}" role="switch" aria-checked="${account.enabled}" title="${account.enabled ? "停用渠道" : "启用渠道"}" aria-label="${account.enabled ? "停用渠道" : "启用渠道"}"><span></span></button></td>
        <td><strong>${escapeHtml(account.name)}</strong></td>
        <td class="channel-type-cell"><span class="channel-type-label"><img src="./assets/channel-logos/${channelPlatforms[account.channel_type]?.logo || "wecom.png"}" width="18" height="18" alt="" />${channelPlatforms[account.channel_type]?.name || account.channel_type}（WS）</span></td>
        <td>${escapeHtml(account.agent_name || account.agent_id)}</td>
        <td><span class="channel-status" data-state="${escapeHtml(runtime.state)}" title="${escapeHtml(runtime.message || "")}"><span></span>${escapeHtml(CHANNEL_STATE_LABELS[runtime.state] || runtime.state)}</span></td>
        <td>${escapeHtml(formatDate(account.updated_at))}</td>
        <td><div class="row-actions channel-row-actions compact-icon-actions">
          <button class="icon-btn" type="button" data-channel-test="${escapeHtml(account.id)}" title="测试连接" aria-label="测试连接"><i data-lucide="plug-zap"></i></button>
          <button class="icon-btn danger" type="button" data-channel-delete="${escapeHtml(account.id)}" title="删除渠道" aria-label="删除渠道"><i data-lucide="trash-2"></i></button>
        </div></td>
      </tr>`;
    }).join("");
    tbody.querySelectorAll("[data-open-channel]").forEach((row) => {
      const open = (event) => {
        if (event.target.closest("a, button")) return;
        this.openEdit(row.dataset.openChannel);
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (event) => {
        if (event.target.closest("a, button")) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this.openEdit(row.dataset.openChannel);
        }
      });
    });
    tbody.querySelectorAll(".channel-row-actions").forEach((actions) => {
      actions.addEventListener("click", (event) => event.stopPropagation());
      actions.addEventListener("keydown", (event) => event.stopPropagation());
    });
    tbody.querySelectorAll("[data-channel-test]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      this.test(button.dataset.channelTest, button);
    }));
    tbody.querySelectorAll("[data-toggle-channel]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      this.toggleEnabled(button.dataset.toggleChannel, button);
    }));
    tbody.querySelectorAll("[data-channel-delete]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      this.remove(button.dataset.channelDelete);
    }));
    window.lucide?.createIcons();
  },

  openCreate(channelType = "wecom") {
    clearChannelUserQuery();
    channelAdminState.editingId = null;
    prepareChannelPlatform(channelType);
    document.getElementById("channelAccountModalTitle").textContent = `新建${channelPlatforms[channelType]?.name || "企微"}渠道`;
    document.getElementById("channelName").value = "";
    renderChannelAgentOptions();
    document.getElementById("channelBotId").value = "";
    document.getElementById("channelSessionUserId").value = "";
    setChannelSecretEditor({ required: true });
    void prepareChannelApplicationEditor();
    AdminFormFeedback.clear(document.getElementById("channelAccountForm"));
    document.getElementById("channelAccountModal").showModal();
  },

  openEdit(accountId) {
    const account = channelAdminState.accounts.find((item) => item.id === accountId);
    if (!account) return;
    clearChannelUserQuery();
    channelAdminState.editingId = accountId;
    prepareChannelPlatform(account.channel_type);
    document.getElementById("channelAccountModalTitle").textContent = account.name;
    document.getElementById("channelName").value = account.name;
    renderChannelAgentOptions(account.agent_id);
    document.getElementById("channelBotId").value = account.bot_id;
    document.getElementById("channelSessionUserId").value = account.session_user_id;
    setChannelSecretEditor({ maskedValue: account.secret_masked, secretSet: account.secret_set });
    void prepareChannelApplicationEditor(account.settings);
    AdminFormFeedback.clear(document.getElementById("channelAccountForm"));
    document.getElementById("channelAccountModal").showModal();
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (channelAdminState.saving || !form.reportValidity()) return;
    const editingId = channelAdminState.editingId;
    const account = channelAdminState.accounts.find(item => item.id === editingId);
    const enabled = document.getElementById("channelUserDetailsEnabled").getAttribute("aria-checked") === "true";
    const applicationSelect = document.getElementById("channelWecomApplicationId");
    const applicationId = applicationSelect.disabled ? applicationSelect.dataset.selectedId : applicationSelect.value;
    const feishu = channelAdminState.channelType === "feishu";
    const dingtalk = channelAdminState.channelType === "dingtalk";
    const scan = !editingId && channelAdminState.registrationMode === "scan";
    if (scan && !channelRegistration?.authorizedId) {
      AdminFormFeedback.show(form, "请先完成扫码授权，或切换到手动关联。"); return;
    }
    if (channelAdminState.channelType === "wecom" && enabled && !channelAdminState.applications.some(item => item.id === applicationId)) {
      AdminFormFeedback.show(form, "请选择有效的企微应用；没有应用时，请先创建。");
      return;
    }
    const payload = {
      settings: {
        ...(dingtalk ? { ...account?.settings, dingtalk: { ...account?.settings?.dingtalk, user_details_enabled: enabled } } : feishu ? { ...account?.settings, feishu: { domain: "feishu", require_mention: true, ...account?.settings?.feishu, user_details_enabled: enabled } } : channelUserDetailsSettings(account?.settings || {}, enabled, applicationId)),
        deepfos: {
          ...channelApiKeySettings(account?.settings?.deepfos),
          enabled: document.getElementById("channelDeepfosEnabled").getAttribute("aria-checked") === "true",
          app_id: document.getElementById("channelDeepfosAppId").value.trim() || null,
        },
      },
      name: document.getElementById("channelName").value.trim(),
      bot_id: document.getElementById("channelBotId").value.trim(),
      agent_id: document.getElementById("channelAgentId").value,
      session_user_id: document.getElementById("channelSessionUserId").value.trim(),
    };
    const secretInput = document.getElementById("channelSecret");
    const secret = secretInput.value.trim();
    if (secret && secret !== secretInput.dataset.maskedValue) payload.secret = secret;
    if (!editingId) { payload.enabled = false; payload.channel_type = channelAdminState.channelType; }
    if (scan) { payload.registration_id = channelRegistration.authorizedId; delete payload.bot_id; delete payload.secret; }
    clearChannelPermissionCheck();
    channelAdminState.saving = true;
    form.querySelectorAll("button").forEach(button => { button.disabled = true; });
    try {
      await apiFetch(editingId ? `/api/v1/channel-accounts/${editingId}` : "/api/v1/channel-accounts", {
        method: editingId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      channelRegistration?.reset({ consumed: scan });
      document.getElementById("channelAccountModal").close();
      showToast(editingId ? "渠道已更新" : "渠道已创建");
      await refreshAfterSuccessfulMutation(editingId ? "渠道已更新" : "渠道已创建", () => this.refresh());
    } catch (error) {
      AdminFormFeedback.show(form, error.message);
    } finally {
      channelAdminState.saving = false;
      form.querySelectorAll("button").forEach(button => { button.disabled = false; });
    }
  },

  async test(accountId, button) {
    button.disabled = true;
    try {
      const result = await apiFetch(`/api/v1/channel-accounts/${accountId}/test-connection`, { method: "POST" });
      showToast(result.warning ? `${result.message}；${result.warning}` : result.message, !result.success);
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
    }
  },

  async toggleEnabled(accountId, button) {
    const account = channelAdminState.accounts.find((item) => item.id === accountId);
    if (!account) return;
    button.disabled = true;
    try {
      await apiFetch(`/api/v1/channel-accounts/${accountId}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !account.enabled }),
      });
      showToast(account.enabled ? "渠道已停用" : "渠道已启用");
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
    }
  },

  deletingAccounts: new Set(),
  async remove(accountId) {
    const account = channelAdminState.accounts.find((item) => item.id === accountId);
    if (!account || this.deletingAccounts.has(accountId)) return;
    this.deletingAccounts.add(accountId);
    try {
    if (!(await confirmAction({
      title: "删除渠道配置？",
      message: `确定删除渠道“${account.name}”？删除后机器人将停止接收消息。`,
      icon: "trash-2",
      tone: "danger",
      confirmLabel: "删除",
    }))) return;
      if (!(await deleteWithImpactConfirmation(`/api/v1/channel-accounts/${encodeURIComponent(accountId)}`, {
        title: "删除渠道及运行记录？",
        describeImpact: (impact) => `渠道“${account.name}”有 ${impact.binding_count} 条会话绑定、${impact.run_count} 条运行记录。\n确认后将停止该渠道的运行，并永久删除渠道及这些记录。Agent 和历史会话会保留。此操作不可撤销。`,
        confirmLabel: "删除渠道及运行记录",
      }))) return;
      showToast("渠道已删除");
      await refreshAfterSuccessfulMutation("渠道已删除", () => this.refresh());
    } catch (error) {
      showToast(error.message, true);
    } finally {
      this.deletingAccounts.delete(accountId);
    }
  },
};

function bindChannelCreateMenu() {
  const trigger = document.getElementById("newChannelBtn");
  const menu = document.getElementById("newChannelMenu");
  const option = document.getElementById("createWecomChannelBtn");
  if (!trigger || !menu || !option) return;
  const close = (restoreFocus = false) => {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  };
  const open = (focusOption = false) => {
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (focusOption) option.focus();
  };
  trigger.addEventListener("click", () => menu.hidden ? open() : close());
  trigger.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    open(true);
  });
  const options = Array.from(menu.querySelectorAll("[role=menuitem]"));
  options.forEach(item => item.addEventListener("click", () => {
    close();
    channelsAdmin.openCreate(item.id === "createDingtalkChannelBtn" ? "dingtalk" : item.id === "createFeishuChannelBtn" ? "feishu" : "wecom");
  }));
  menu.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const index = options.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (index + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length;
      options[next]?.focus();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (menu.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); close(true); }
    if (event.key === "Tab") close();
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !trigger.contains(event.target) && !menu.contains(event.target)) close();
  });
  document.addEventListener("focusin", (event) => {
    if (!menu.hidden && !trigger.contains(event.target) && !menu.contains(event.target)) close();
  });
}

window.channelsAdmin = channelsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-registration-mode]").forEach(button => button.addEventListener("click", () => setChannelRegistrationMode(button.dataset.registrationMode)));
  ["channelRegistrationStartScan", "channelRegistrationRetryScan"].forEach(id => document.getElementById(id)?.addEventListener("click", () => channelRegistration.start({ appName: document.getElementById("channelName").value.trim() })));
  document.getElementById("queryChannelSessionUserBtn")?.addEventListener("click", queryChannelSessionUser);
  document.getElementById("channelSessionUserId")?.addEventListener("input", clearChannelUserQuery);
  document.addEventListener("click", event => {
    if (!event.target.closest(".channel-user-query")) clearChannelUserQuery();
  });
  document.getElementById("channelAccountModal")?.addEventListener("keydown", event => {
    if (event.key === "Escape" && !document.getElementById("channelSessionUserResult").hidden) {
      event.preventDefault(); event.stopPropagation(); clearChannelUserQuery();
    }
  });
  document.getElementById("channelAgentId")?.addEventListener("change", event => {
    if (channelAdminState.editingId) return;
    const agent = channelAdminState.agents.find(item => item.id === event.target.value);
    if (agent) document.getElementById("channelName").value = agent.name;
  });
  bindChannelCreateMenu();
  const extensionTabs = Array.from(document.querySelectorAll("[data-channel-extension]"));
  extensionTabs.forEach(button => {
    button.addEventListener("click", () => selectChannelExtension(button.dataset.channelExtension));
    button.addEventListener("keydown", event => {
      const visibleTabs = extensionTabs.filter(tab => !tab.hidden);
      const index = visibleTabs.indexOf(button);
      const next = event.key === "Home" ? 0 : event.key === "End" ? visibleTabs.length - 1 : event.key === "ArrowDown" ? (index + 1) % visibleTabs.length : event.key === "ArrowUp" ? (index + visibleTabs.length - 1) % visibleTabs.length : -1;
      if (next < 0) return;
      event.preventDefault();
      selectChannelExtension(visibleTabs[next].dataset.channelExtension);
      visibleTabs[next].focus();
    });
  });
  document.getElementById("channelAccountForm")?.addEventListener("invalid", event => {
    const panel = event.target.closest(".channel-extension-panel");
    if (panel) selectChannelExtension(panel.id === "channelDeepfosPanel" ? "deepfos" : "member");
  }, true);
  const keyInput = document.getElementById("channelDeepfosApiKey");
  keyInput?.addEventListener("focus", () => {
    if (channelAdminState.apiKeyDraft) {
      keyInput.type = "password";
      keyInput.value = channelAdminState.apiKeyDraft;
    }
    keyInput.select();
  });
  keyInput?.addEventListener("input", () => {
    channelAdminState.apiKeyDraft = keyInput.value === keyInput.dataset.maskedValue ? "" : keyInput.value.trim();
    keyInput.type = "password";
  });
  keyInput?.addEventListener("blur", () => {
    const key = channelAdminState.apiKeyDraft;
    keyInput.value = key ? (key.length > 10 ? `${key.slice(0, 6)}********${key.slice(-4)}` : "********") : channelAdminState.clearApiKey ? "" : keyInput.dataset.maskedValue;
    keyInput.type = keyInput.value ? "text" : "password";
  });
  document.getElementById("clearChannelDeepfosApiKeyBtn")?.addEventListener("click", () => {
    channelAdminState.apiKeyDraft = "";
    channelAdminState.clearApiKey = true;
    keyInput.value = "";
    keyInput.type = "password";
  });
  const viewButtons = Array.from(document.querySelectorAll("[data-channel-view]"));
  viewButtons.forEach((button, index) => {
    button.addEventListener("click", () => channelsAdmin.selectView(button.dataset.channelView));
    button.addEventListener("keydown", event => {
      const next = event.key === "Home" ? 0 : event.key === "End" ? viewButtons.length - 1 : ["ArrowLeft", "ArrowRight"].includes(event.key) ? (index + 1) % viewButtons.length : -1;
      if (next < 0) return;
      event.preventDefault(); channelsAdmin.selectView(viewButtons[next].dataset.channelView); viewButtons[next].focus();
    });
  });
  document.getElementById("channelDeepfosEnabled")?.addEventListener("click", event => setChannelDeepfosEnabled(event.currentTarget.getAttribute("aria-checked") !== "true"));
  document.getElementById("channelUserDetailsCheckBtn")?.addEventListener("click", checkChannelUserDetails);
  ["channelBotId", "channelSecret"].forEach(id => document.getElementById(id)?.addEventListener("input", clearChannelPermissionCheck));
  document.getElementById("channelUserDetailsEnabled")?.addEventListener("click", event => setChannelUserDetailsEnabled(event.currentTarget.getAttribute("aria-checked") !== "true"));
  document.getElementById("clearChannelWecomApplicationBtn")?.addEventListener("click", () => {
    const select = document.getElementById("channelWecomApplicationId");
    select.value = ""; select.dataset.selectedId = "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  document.getElementById("refreshChannelsBtn")?.addEventListener("click", () => channelsAdmin.refresh());
  document.getElementById("channelHelpBtn")?.addEventListener("click", () => channelsAdmin.openHelp());
  const helpTabs = Array.from(document.querySelectorAll("[data-channel-help]"));
  helpTabs.forEach((button, index) => {
    button.addEventListener("click", () => channelsAdmin.selectHelpTab(button.dataset.channelHelp));
    button.addEventListener("keydown", event => {
      const next = event.key === "Home" ? 0 : event.key === "End" ? helpTabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % helpTabs.length : event.key === "ArrowLeft" ? (index + helpTabs.length - 1) % helpTabs.length : -1;
      if (next < 0) return;
      event.preventDefault();
      channelsAdmin.selectHelpTab(helpTabs[next].dataset.channelHelp);
      helpTabs[next].focus();
    });
  });
  document.getElementById("retryChannelCommandsBtn")?.addEventListener("click", () => channelsAdmin.loadCommands());
  ["closeChannelHelpModal", "dismissChannelHelpBtn"].forEach(id => document.getElementById(id)?.addEventListener("click", () => document.getElementById("channelHelpModal").close()));
  document.getElementById("channelHelpModal")?.addEventListener("close", () => { channelsAdmin.commandsRequestId = (channelsAdmin.commandsRequestId || 0) + 1; });
  document.getElementById("channelAccountForm")?.addEventListener("submit", (event) => channelsAdmin.save(event));
  document.getElementById("channelSecret")?.addEventListener("focus", (event) => {
    const input = event.currentTarget;
    if (input.value === input.dataset.maskedValue) input.select();
  });
  document.getElementById("channelSecret")?.addEventListener("input", (event) => {
    const input = event.currentTarget;
    input.type = input.value && input.value === input.dataset.maskedValue ? "text" : "password";
  });
  const closeAccount = () => { if (!channelAdminState.saving) document.getElementById("channelAccountModal").close(); };
  document.getElementById("closeChannelAccountModal")?.addEventListener("click", closeAccount);
  document.getElementById("cancelChannelAccountBtn")?.addEventListener("click", closeAccount);
  document.getElementById("channelAccountModal")?.addEventListener("cancel", event => { event.preventDefault(); closeAccount(); });
  document.getElementById("channelAccountModal")?.addEventListener("close", () => {
    clearChannelUserQuery();
    clearChannelPermissionCheck();
    channelRegistration?.reset();
    channelAdminState.applicationLoadGeneration++;
    document.getElementById("channelSecret").value = "";
    channelAdminState.apiKeyDraft = "";
    channelAdminState.clearApiKey = false;
    keyInput.value = "";
    keyInput.dataset.maskedValue = "";
  });
});
