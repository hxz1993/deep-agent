/* Each integration owns its editor and table summary; storage remains JSON. */
(() => {
  const el = id => document.getElementById(id);
  const spreadsheetCode = 'deeptable-assistant';
  const appPath = '/api/v1/integrations/deeptable-assistant/app-configs';
  const pathFor = id => `${appPath}/${encodeURIComponent(id)}`;
  const agentEditor = {
    summary(settings, agents) {
      const id = settings?.agent_id;
      if (!id) return '未配置';
      return agents.find(agent => agent.id === id)?.name || `Agent 已不可用（${String(id).slice(0, 8)}）`;
    },
    render(settings, agents) {
      const selected = settings?.agent_id || '';
      const options = agents.map(agent => `<label class="console-select-option integration-agent-option"><input type="radio" name="integrationAgentId" value="${escapeHtml(agent.id)}"${agent.id === selected ? ' checked' : ''}><span>${escapeHtml(agent.name)}</span></label>`).join('');
      if (!options) return '<p class="console-select-empty">暂无 Agent，请先在 Agent 页面创建。</p>';
      return `<div class="integration-agent-options" role="radiogroup" aria-label="可选 Agent">${options}</div>`;
    },
    read(settings, agents) {
      const id = el('integrationConfigForm').querySelector('input[name="integrationAgentId"]:checked')?.value;
      if (!id && settings?.agent_id && !agents.some(agent => agent.id === settings.agent_id)) throw new Error('请选择可用的 Agent，或点击清空。');
      if (!id) return {};
      if (!agents.some(agent => agent.id === id)) throw new Error('请选择可用的 Agent。');
      return { ...settings, agent_id: id };
    },
  };
  const descriptions = {
    'deeptable-assistant': '选择电子表格助手使用的 Agent。可按应用追加提示词和 Skill。',
    'deepcube-assistant': '选择 Python Server 中 DeepCube 助手使用的 Agent。模型、工具与指令由所选 Agent 提供。',
  };
  const editors = { 'deeptable-assistant': agentEditor, 'deepcube-assistant': agentEditor };
  const admin = {
    items: [], agents: [], selectedCode: null, editingCode: null, busy: false,
    listGeneration: 0, apiGeneration: 0, editGeneration: 0, appGeneration: 0,
    apps: null, app: null, appBusy: false,
    async refresh() {
      if (this.busy) return;
      const generation = ++this.listGeneration;
      this.listController?.abort();
      const controller = this.listController = new AbortController();
      el('integrationDetails').hidden = true;
      el('integrationStatus').textContent = '正在加载场景…';
      el('integrationStatus').hidden = false;
      try {
        const options = { signal: controller.signal, reportStatus: false };
        const [result, agents] = await Promise.all([apiFetch('/api/v1/integrations', options), apiFetch('/api/v1/agents', options)]);
        if (generation !== this.listGeneration) return;
        if (!Array.isArray(result?.groups) || result.groups.some(group => !Array.isArray(group.integrations)) || !Array.isArray(agents)) throw new Error('场景列表加载失败，请重试。');
        this.items = result.groups.flatMap(group => group.integrations);
        this.agents = agents;
        this.select(this.find(this.selectedCode)?.code || this.items[0]?.code);
      } catch (error) {
        if (generation !== this.listGeneration) return;
        this.items = []; this.agents = [];
        el('integrationDetails').hidden = true;
        el('integrationDirectory').replaceChildren();
        el('integrationStatus').textContent = error.message;
      }
    },
    find(code) { return this.items.find(item => item.code === code); },
    render() {
      el('integrationDirectory').innerHTML = this.items.map(item => `<button type="button" class="integration-directory-item${item.code === this.selectedCode ? ' active' : ''}" data-integration-code="${escapeHtml(item.code)}" aria-pressed="${item.code === this.selectedCode}"><i data-lucide="${item.code === 'deeptable-assistant' ? 'table-2' : 'square-code'}"></i><span><strong>${escapeHtml(item.name)}</strong><code>${escapeHtml(item.code)}</code></span></button>`).join('');
      const item = this.find(this.selectedCode);
      el('integrationDetails').hidden = !item;
      el('integrationStatus').hidden = Boolean(item);
      el('integrationStatus').textContent = item ? '' : '暂无场景';
      if (!item) return;
      el('integrationConfigTitle').textContent = item.name;
      el('integrationConfigDescription').textContent = descriptions[item.code] || '';
      el('integrationAgentHost').hidden = !editors[item.code];
      el('integrationAgentName').textContent = editors[item.code]?.summary(item.settings, this.agents) || '';
      window.lucide?.createIcons();
    },
    select(code) {
      if (!this.closeConfig(false)) return;
      this.selectedCode = code;
      this.render();
      this.openTab(code === spreadsheetCode ? 'apps' : 'api');
    },
    openConfig(code = this.selectedCode) {
      if (this.busy || !this.find(code) || !editors[code]) return;
      if (this.editingCode === code) return this.closeConfig();
      ++this.editGeneration;
      this.editingCode = code;
      this.cleared = false;
      el('clearIntegrationAgentBtn').setAttribute('aria-pressed', 'false');
      AdminFormFeedback.clear(el('integrationConfigForm'));
      el('integrationEditor').innerHTML = editors[code].render(this.find(code).settings || {}, this.agents);
      const popover = el('integrationAgentPopover');
      popover.classList.remove('hidden');
      popover.inert = false;
      el('openIntegrationAgentBtn').setAttribute('aria-expanded', 'true');
      const editor = el('integrationEditor');
      const option = editor.querySelector('input:checked') || editor.querySelector('input');
      (option || el('clearIntegrationAgentBtn')).focus();
    },
    setBusy(value) {
      this.busy = value;
      el('integrationConfigForm').querySelectorAll('button, input').forEach(node => { node.disabled = value; });
      AdminFormFeedback.setSubmitting(el('integrationConfigForm'), value);
    },
    async save(event) {
      event.preventDefault();
      if (this.busy || !this.editingCode || !editors[this.editingCode]) return;
      const form = el('integrationConfigForm');
      AdminFormFeedback.clear(form);
      const code = this.editingCode;
      let settings;
      try { settings = this.cleared ? {} : editors[code].read(this.find(code)?.settings || {}, this.agents); }
      catch (error) { AdminFormFeedback.show(form, error); return; }
      const generation = this.editGeneration;
      ++this.listGeneration; this.listController?.abort();
      this.setBusy(true);
      const controller = new AbortController();
      let timeout;
      try {
        // A disconnected request must not lock navigation indefinitely. Aborting
        // the client cannot guarantee that the server has not committed the PUT.
        const expired = new Promise((_, reject) => {
          timeout = setTimeout(() => {
            reject(new Error('保存请求超时，结果待确认。请重新打开场景配置页核对后再保存。'));
            controller.abort();
          }, 30000);
        });
        const item = await Promise.race([expired, apiFetch(`/api/v1/integrations/${encodeURIComponent(code)}`, { signal: controller.signal, reportStatus: false, method: 'PUT', body: JSON.stringify({ settings }) })]);
        if (generation !== this.editGeneration) return;
        const current = this.find(code);
        if (current) Object.assign(current, item);
        el('integrationAgentName').textContent = editors[code].summary(current?.settings, this.agents); this.setBusy(false); this.closeConfig();
        showToast('场景配置已保存');
      } catch (error) {
        if (generation === this.editGeneration) AdminFormFeedback.show(form, error);
      } finally { clearTimeout(timeout); this.setBusy(false); }
    },
    closeConfig(restoreFocus = true) {
      if (this.busy) return false;
      if (!this.editingCode) return true;
      ++this.editGeneration;
      this.editingCode = null;
      const popover = el('integrationAgentPopover');
      popover.classList.add('hidden');
      popover.inert = true;
      const trigger = el('openIntegrationAgentBtn');
      trigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) trigger.focus();
      return true;
    },
    openTab(tab) {
      const spreadsheet = this.selectedCode === spreadsheetCode;
      el('integrationAppsTab').hidden = !spreadsheet;
      const showApps = spreadsheet && tab === 'apps';
      el('integrationAppsTab').classList.toggle('active', showApps);
      el('integrationApiTab').classList.toggle('active', !showApps);
      el('integrationAppsPanel').hidden = !showApps;
      el('integrationApiPanel').hidden = showApps;
      if (showApps) this.showApps();
      else {
        ++this.appGeneration;
        this.appController?.abort();
        void this.loadApi(this.selectedCode);
      }
    },
    showApps() {
      if (this.apps === null) void this.loadApps();
      else this.renderApps();
    },
    async loadApps() {
      if (this.appsLoading && !this.appsLoading.signal.aborted) return;
      const generation = ++this.appGeneration;
      this.appController?.abort();
      const controller = this.appsLoading = this.appController = new AbortController();
      const status = el('integrationAppsStatus');
      const refresh = el('refreshIntegrationAppsBtn');
      refresh.disabled = true;
      el('retryIntegrationAppsBtn').hidden = true;
      if (this.apps === null) status.textContent = '正在加载应用配置…';
      try {
        const rows = await apiFetch(appPath, { signal: controller.signal, reportStatus: false });
        if (generation !== this.appGeneration) return;
        if (!Array.isArray(rows)) throw new Error('应用配置读取失败。');
        this.setApps(rows);
      } catch (error) {
        if (generation !== this.appGeneration) return;
        status.textContent = error.message;
        el('retryIntegrationAppsBtn').hidden = false;
      } finally {
        if (this.appsLoading === controller) {
          this.appsLoading = null;
          refresh.disabled = false;
        }
      }
    },
    setApps(rows) {
      this.apps = rows;
      const filter = el('integrationTenantFilter'), selected = filter.value;
      const tenants = new Map(rows.map(row => [row.tenant_id, row.tenant_name || row.tenant_id]));
      filter.replaceChildren(new Option('全部企业', ''), ...[...tenants].map(([id, name]) => new Option(name, id)));
      filter.value = tenants.has(selected) ? selected : '';
      el('retryIntegrationAppsBtn').hidden = true;
      this.renderApps();
    },
    updateAppList(app) {
      if (!this.apps || !app.config_id) return;
      const rows = [...this.apps];
      const index = rows.findIndex(row => row.app_id === app.app_id);
      if (index === -1) rows.push(app);
      else rows[index] = app;
      this.setApps(rows);
    },
    onAppSkillsLoaded(path, count) {
      const app = this.app;
      if (!app || path !== pathFor(app.app_id) + '/skills' || app.skill_count === count) return;
      app.skill_count = count;
      this.updateAppList(app);
    },
    renderApps() {
      if (!this.apps) return;
      const term = el('integrationAppSearch').value.trim().toLowerCase();
      const tenant = el('integrationTenantFilter').value;
      const rows = this.apps.filter(app => (!tenant || tenant === app.tenant_id) &&
        (!term || `${app.app_name} ${app.app_id}`.toLowerCase().includes(term)));
      el('integrationAppsTable').hidden = !rows.length;
      el('integrationAppsEmpty').hidden = Boolean(this.apps.length);
      el('integrationAppsStatus').textContent = !rows.length && this.apps.length ? '没有匹配的应用' : '';
      el('integrationAppsBody').innerHTML = rows.map(app => {
        const id = escapeHtml(app.app_id), name = escapeHtml(app.app_name);
        return `<tr data-integration-app="${id}" tabindex="0" aria-label="查看 ${name}">
          <td><strong>${name}</strong><code>${id}</code></td>
          <td>${escapeHtml(app.space_name || app.space_id)}</td>
          <td>${escapeHtml(app.tenant_name || app.tenant_id)}</td>
          <td><span class="integration-app-skill-count"><i data-lucide="book-open-check"></i>${Number(app.skill_count || 0)}</span></td></tr>`;
      }).join('');
      window.lucide?.createIcons();
    },
    addApp() {
      if (this.selectedCode !== spreadsheetCode) return;
      window.DeepAgentAppPicker.open({
        owner: 'integration', path: `${appPath}/available`, title: '添加应用',
        onSelect: app => { void this.openApp(app.app_id); },
      });
    },
    async openApp(appId) {
      const generation = ++this.appGeneration;
      this.appController?.abort();
      const controller = this.appController = new AbortController();
      try {
        const app = await apiFetch(pathFor(appId), { signal: controller.signal, reportStatus: false });
        if (generation !== this.appGeneration) return;
        this.app = app;
        el('integrationWorkspace').hidden = true;
        el('integrationPageToolbar').hidden = true;
        el('integrationAppDetail').hidden = false;
        el('integrationAppTitle').textContent = app.app_name || app.app_id;
        el('integrationAppMeta').textContent = [app.app_id, app.space_name || app.space_id, app.tenant_name].filter(Boolean).join(' · ');
        el('integrationAppPrompt').value = app.prompt_append || '';
        el('integrationAppSkillCount').textContent = '';
        this.setAppError('');
        this.setAppBusy(false);
        this.scheduleEstimate(true);
        await window.skillsAdmin.mountApp(pathFor(app.app_id) + '/skills');
      } catch (error) {
        if (generation === this.appGeneration) showToast(error.message, true);
      }
    },
    closeApp(restoreList = true) {
      ++this.appGeneration;
      this.appController?.abort();
      clearTimeout(this.estimateTimer);
      this.estimateController?.abort();
      if (!this.app) return;
      this.app = null;
      window.skillsAdmin?.unmountApp();
      el('integrationAppDetail').hidden = true;
      el('integrationWorkspace').hidden = false;
      el('integrationPageToolbar').hidden = false;
      if (restoreList && this.selectedCode === spreadsheetCode) this.showApps();
    },
    setAppBusy(value) {
      this.appBusy = value;
      el('saveIntegrationAppBtn').disabled = value;
      el('removeIntegrationAppBtn').disabled = value || !this.app?.config_id;
      el('integrationAppBack').disabled = value;
      el('integrationAppPrompt').readOnly = value;
      el('installIntegrationSkillBtn').disabled = value;
    },
    setAppError(message) {
      const node = el('integrationAppError');
      node.textContent = message;
      node.classList.toggle('hidden', !message);
    },
    async saveApp() {
      const app = this.app;
      if (!app || this.appBusy) return;
      this.setAppError('');
      this.setAppBusy(true);
      try {
        const updated = await apiFetch(pathFor(app.app_id), {
          method: 'PUT', body: JSON.stringify({ config_id: app.config_id, prompt_append: el('integrationAppPrompt').value }), reportStatus: false,
        });
        if (this.app !== app) return;
        Object.assign(app, updated, { space_name: updated.space_name || app.space_name });
        this.updateAppList(app);
        showToast('应用配置已保存');
      } catch (error) {
        if (this.app === app) this.setAppError(error.message);
      } finally {
        if (this.app === app) this.setAppBusy(false);
      }
    },
    async afterSkillInstall() {
      const app = this.app;
      if (!app || app.config_id) return;
      try {
        const updated = await apiFetch(pathFor(app.app_id), { reportStatus: false });
        if (this.app !== app) return;
        Object.assign(app, updated);
        this.updateAppList(app);
        this.setAppBusy(this.appBusy);
      } catch (error) {
        if (this.app === app) this.setAppError(error.message);
      }
    },
    async removeApp() {
      const app = this.app;
      if (!app?.config_id || this.appBusy) return;
      if (!await confirmAction({
        title: '移除应用配置？', message: '将删除此应用的追加提示词和全部专用 Skill。',
        tone: 'danger', icon: 'trash-2', confirmLabel: '移除应用配置',
      }) || this.app !== app) return;
      this.setAppError('');
      this.setAppBusy(true);
      try {
        await apiFetch(`${pathFor(app.app_id)}?config_id=${encodeURIComponent(app.config_id)}`, {
          method: 'DELETE', reportStatus: false,
        });
        if (this.app !== app) return;
        if (this.apps) this.setApps(this.apps.filter(row => row.app_id !== app.app_id));
        showToast('应用配置已移除');
        this.closeApp();
      } catch (error) {
        if (this.app === app) this.setAppError(error.message);
      } finally {
        if (this.app === app) this.setAppBusy(false);
        else this.appBusy = false;
      }
    },
    scheduleEstimate(immediate = false) {
      clearTimeout(this.estimateTimer);
      this.estimateController?.abort();
      const app = this.app;
      const prompt = el('integrationAppPrompt').value.trim();
      const count = Array.from(prompt).length;
      const output = el('integrationAppPromptCount');
      output.textContent = `${count.toLocaleString('zh-CN')} 字 / ${count ? '—' : '0'} Token`;
      const modelId = this.agents.find(agent => agent.id === this.find(spreadsheetCode)?.settings?.agent_id)?.model_id;
      if (!app || !modelId || !count) return;
      const controller = this.estimateController = new AbortController();
      this.estimateTimer = setTimeout(async () => {
        try {
          const result = await apiFetch('/api/v1/agents/prompt-estimate', {
            method: 'POST', body: JSON.stringify({ model_id: modelId, prompt }),
            reportStatus: false, signal: controller.signal,
          });
          if (this.app === app && !controller.signal.aborted) {
            output.textContent = `${count.toLocaleString('zh-CN')} 字 / ${Number(result.estimated_tokens).toLocaleString('zh-CN')} Token`;
          }
        } catch { /* Keep the character count when token estimation is unavailable. */ }
      }, immediate ? 0 : 250);
    },
    async loadApi(code = this.selectedCode) {
      if (!code) return;
      const generation = ++this.apiGeneration;
      this.apiController?.abort();
      const controller = this.apiController = new AbortController();
      el('integrationApiContent').innerHTML = '<p class="field-hint" role="status">正在加载接口示例…</p>';
      el('retryIntegrationApiBtn').hidden = true;
      try {
        const spec = await apiFetch(`/api/v1/integrations/${encodeURIComponent(code)}/api`, { signal: controller.signal, reportStatus: false });
        if (generation !== this.apiGeneration) return;
        if (typeof spec?.path !== 'string' || typeof spec?.method !== 'string' || !spec.response_example) throw new Error('接口示例加载失败，请重试。');
        el('integrationApiContent').innerHTML = `<p class="field-hint">${escapeHtml(spec.description || '')}</p><pre class="integration-api-example"><code><span class="integration-code-label">请求</span>
${escapeHtml(spec.method)} ${escapeHtml(spec.path)}

<span class="integration-code-label">返回</span>
${escapeHtml(JSON.stringify(spec.response_example, null, 2))}</code></pre>`;
      } catch (error) {
        if (generation !== this.apiGeneration) return;
        el('integrationApiContent').innerHTML = `<p class="form-error" role="alert">${escapeHtml(error.message)}</p>`;
        el('retryIntegrationApiBtn').hidden = false;
      }
    },
    deactivate() {
      if (this.busy || this.appBusy || (this.app && window.skillsAdmin?.installing)) return false;
      window.DeepAgentAppPicker.invalidate(`${appPath}/available`);
      window.DeepAgentAppPicker.close('integration');
      this.closeApp(false);
      ++this.listGeneration; ++this.apiGeneration; this.listController?.abort(); this.apiController?.abort(); this.closeConfig(false); return true;
    },
  };
  window.integrationsAdmin = admin;
  window.DeepAgentIntegrationEditors = editors;
  document.addEventListener('DOMContentLoaded', () => {
    el('integrationDirectory')?.addEventListener('click', event => {
      const button = event.target.closest('[data-integration-code]');
      if (button) admin.select(button.dataset.integrationCode);
    });
    el('openIntegrationAgentBtn')?.addEventListener('click', () => admin.openConfig());
    el('integrationConfigForm')?.addEventListener('submit', event => admin.save(event));
    document.addEventListener('pointerdown', event => {
      if (!event.target.closest?.('#integrationAgentHost')) admin.closeConfig(false);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !event.defaultPrevented) admin.closeConfig();
    });
    el('integrationAppsTab')?.addEventListener('click', () => admin.openTab('apps'));
    el('integrationApiTab')?.addEventListener('click', () => admin.openTab('api'));
    el('retryIntegrationAppsBtn')?.addEventListener('click', () => admin.loadApps());
    el('refreshIntegrationAppsBtn')?.addEventListener('click', () => {
      window.DeepAgentAppPicker.invalidate(`${appPath}/available`);
      void admin.loadApps();
    });
    el('integrationAppSearch')?.addEventListener('input', () => admin.renderApps());
    el('integrationTenantFilter')?.addEventListener('change', () => admin.renderApps());
    el('addIntegrationAppBtn')?.addEventListener('click', () => admin.addApp());
    el('integrationAppBack')?.addEventListener('click', () => admin.closeApp());
    el('integrationAppPrompt')?.addEventListener('input', () => admin.scheduleEstimate());
    el('saveIntegrationAppBtn')?.addEventListener('click', () => admin.saveApp());
    el('removeIntegrationAppBtn')?.addEventListener('click', () => admin.removeApp());
    el('installIntegrationSkillBtn')?.addEventListener('click', () => window.skillsAdmin.openInstallDialog());
    el('integrationAppsBody')?.addEventListener('click', event => {
      const row = event.target.closest('[data-integration-app]');
      if (row) void admin.openApp(row.dataset.integrationApp);
    });
    el('integrationAppsBody')?.addEventListener('keydown', event => {
      if (['Enter', ' '].includes(event.key) && event.target.matches('[data-integration-app]')) {
        event.preventDefault();
        void admin.openApp(event.target.dataset.integrationApp);
      }
    });
    el('retryIntegrationApiBtn')?.addEventListener('click', () => admin.loadApi());
    el('clearIntegrationAgentBtn')?.addEventListener('click', () => {
      if (admin.busy) return;
      const current = el('integrationEditor').querySelector('input:checked');
      if (current) current.checked = false;
      admin.cleared = true;
      el('clearIntegrationAgentBtn').setAttribute('aria-pressed', 'true');
      AdminFormFeedback.clear(el('integrationConfigForm'));
    });
    el('integrationEditor')?.addEventListener('change', () => {
      admin.cleared = false;
      el('clearIntegrationAgentBtn').setAttribute('aria-pressed', 'false');
      AdminFormFeedback.clear(el('integrationConfigForm'));
    });
  });
})();
