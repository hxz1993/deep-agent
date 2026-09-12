/* Each integration owns its editor and table summary; storage remains JSON. */
(() => {
  const el = id => document.getElementById(id);
  const agentEditor = {
    summary(settings, agents) {
      const id = settings?.agent_id;
      if (!id) return '未配置';
      return agents.find(agent => agent.id === id)?.name || `Agent 已不可用（${String(id).slice(0, 8)}）`;
    },
    render(settings, agents) {
      const selected = settings?.agent_id || '';
      const missing = selected && !agents.some(agent => agent.id === selected);
      return `<label class="field"><span>Agent</span><select id="integrationAgentId" aria-label="Agent"><option value="">未配置</option>${missing ? `<option value="${escapeHtml(selected)}" selected disabled>Agent 已不可用（${escapeHtml(String(selected).slice(0, 8))}）</option>` : ''}${agents.map(agent => `<option value="${escapeHtml(agent.id)}" ${agent.id === selected ? 'selected' : ''}>${escapeHtml(agent.name)}</option>`).join('')}</select></label>${!agents.length ? '<p class="field-hint">暂无 Agent，请先在 Agent 页面创建。</p>' : ''}`;
    },
    read(settings, agents) {
      const id = el('integrationAgentId').value;
      if (!id) return {};
      if (!agents.some(agent => agent.id === id)) throw new Error('请选择可用的 Agent。');
      return { ...settings, agent_id: id };
    },
  };
  const descriptions = {
    'deeptable-assistant': '选择电子表格助手关联的 Agent。电子表格组件会读取此配置，并使用所选 Agent 发起对话；助手的模型、工具与指令由该 Agent 的配置决定。',
    'deepcube-assistant': '选择 Python Server 中 DeepCube 助手关联的 Agent。组件会读取此配置，并使用所选 Agent 发起对话；助手的模型、工具与指令由该 Agent 的配置决定。',
  };
  const editors = { 'deeptable-assistant': agentEditor, 'deepcube-assistant': agentEditor };
  const admin = {
    items: [], agents: [], editingCode: null, busy: false,
    listGeneration: 0, apiGeneration: 0, editGeneration: 0,
    async refresh() {
      if (this.busy) return;
      const generation = ++this.listGeneration;
      this.listController?.abort();
      const controller = this.listController = new AbortController();
      el('integrationTableBody').innerHTML = '<tr><td colspan="4" class="empty-cell">正在加载集成…</td></tr>';
      try {
        const options = { signal: controller.signal, reportStatus: false };
        const [result, agents] = await Promise.all([apiFetch('/api/v1/integrations', options), apiFetch('/api/v1/agents', options)]);
        if (generation !== this.listGeneration) return;
        if (!Array.isArray(result?.groups) || result.groups.some(group => !Array.isArray(group.integrations)) || !Array.isArray(agents)) throw new Error('集成目录加载失败，请重试。');
        this.items = result.groups.flatMap(group => group.integrations);
        this.agents = agents;
        this.render();
      } catch (error) {
        if (generation !== this.listGeneration) return;
        this.items = []; this.agents = [];
        el('integrationTableBody').innerHTML = `<tr><td colspan="4" class="empty-cell" role="alert">${escapeHtml(error.message)}</td></tr>`;
      }
    },
    render() {
      el('integrationTableBody').innerHTML = this.items.length ? this.items.map(item => `<tr><td><strong>${escapeHtml(item.name)}</strong></td><td><code>${escapeHtml(item.code)}</code></td><td>${escapeHtml(editors[item.code]?.summary(item.settings, this.agents) || '—')}</td><td><div class="row-actions integration-row-actions"><button type="button" class="btn" data-integration-configure="${escapeHtml(item.code)}"><i data-lucide="settings-2"></i>配置</button></div></td></tr>`).join('') : '<tr><td colspan="4" class="empty-cell">暂无集成</td></tr>';
      window.lucide?.createIcons();
    },
    find(code) { return this.items.find(item => item.code === code); },
    openConfig(code) {
      if (this.busy) return;
      const item = this.find(code);
      if (!item) return;
      ++this.editGeneration; this.editingCode = code;
      el('integrationConfigTitle').textContent = item.name;
      el('integrationConfigCode').textContent = code;
      el('integrationConfigDescription').textContent = descriptions[code] || '';
      AdminFormFeedback.clear(el('integrationConfigForm'));
      el('integrationEditor').innerHTML = editors[code]?.render(item.settings || {}, this.agents) || '<p class="field-hint">此集成暂不支持配置。</p>';
      el('saveIntegrationConfigBtn').disabled = !editors[code];
      if (!el('integrationConfigModal').open) el('integrationConfigModal').showModal();
      void this.loadApi(code);
    },
    setBusy(value) {
      this.busy = value;
      el('integrationConfigForm').querySelectorAll('button, select').forEach(node => { node.disabled = value; });
      AdminFormFeedback.setSubmitting(el('integrationConfigForm'), value);
    },
    async save(event) {
      event.preventDefault();
      if (this.busy || !this.editingCode || !editors[this.editingCode]) return;
      const form = el('integrationConfigForm');
      AdminFormFeedback.clear(form);
      const code = this.editingCode;
      let settings;
      try { settings = editors[code].read(this.find(code)?.settings || {}, this.agents); }
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
            reject(new Error('保存请求超时，结果待确认。请重新打开集成页核对后再保存。'));
            controller.abort();
          }, 30000);
        });
        const item = await Promise.race([expired, apiFetch(`/api/v1/integrations/${encodeURIComponent(code)}`, { signal: controller.signal, reportStatus: false, method: 'PUT', body: JSON.stringify({ settings }) })]);
        if (generation !== this.editGeneration) return;
        const current = this.find(code);
        if (current) Object.assign(current, item);
        this.render(); this.setBusy(false); this.closeConfig();
        showToast('集成配置已保存');
      } catch (error) {
        if (generation === this.editGeneration) AdminFormFeedback.show(form, error);
      } finally { clearTimeout(timeout); this.setBusy(false); }
    },
    closeConfig() {
      if (this.busy) return false;
      ++this.editGeneration; ++this.apiGeneration; this.apiController?.abort(); this.editingCode = null;
      if (el('integrationConfigModal').open) el('integrationConfigModal').close();
      return true;
    },
    async loadApi(code = this.editingCode) {
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
      if (this.busy) return false;
      ++this.listGeneration; this.listController?.abort(); this.closeConfig(); return true;
    },
  };
  window.integrationsAdmin = admin;
  window.DeepAgentIntegrationEditors = editors;
  document.addEventListener('DOMContentLoaded', () => {
    el('integrationTableBody')?.addEventListener('click', event => { const button = event.target.closest('[data-integration-configure]'); if (button) admin.openConfig(button.dataset.integrationConfigure); });
    el('integrationConfigForm')?.addEventListener('submit', event => admin.save(event));
    el('integrationConfigModal')?.querySelectorAll('[data-integration-close]').forEach(button => button.addEventListener('click', () => admin.closeConfig()));
    el('integrationConfigModal')?.addEventListener('cancel', event => { event.preventDefault(); admin.closeConfig(); });
    el('retryIntegrationApiBtn')?.addEventListener('click', () => admin.loadApi());
    el('integrationEditor')?.addEventListener('change', () => AdminFormFeedback.clear(el('integrationConfigForm')));
  });
})();
