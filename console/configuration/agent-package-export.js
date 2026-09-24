/* Read the saved resource overview before explicitly downloading an Agent ZIP. */
(() => {
  const el = id => document.getElementById(id);
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const list = value => Array.isArray(value) ? value : value?.items || [];
  async function request(path, signal, blob = false) {
    const response = await daFetch(serviceUrl(path), { signal, reportStatus: false });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try { const data = await response.json(); message = window.AdminFormFeedback?.formatDetail(data.detail || data.message) || message; } catch { /* retain status */ }
      throw new Error(message);
    }
    return blob ? response.blob() : response.json();
  }
  function overview(agent, skillRows, packageRows, toolRows) {
    const skills = (agent.skills || []).map(code => list(skillRows).find(row => row.code === code) || { code, label: code, missing: true });
    const packages = (agent.capabilities || []).filter(code => code.startsWith('ui:')).map(code => list(packageRows).find(row => row.code === code) || { code, label: code, missing: true });
    const uploaded = skills.filter(row => row.source !== 'builtin' && !row.missing);
    const custom = packages.filter(row => row.source !== 'builtin' && !row.missing);
    const tools = list(toolRows).filter(tool => packages.some(pkg => pkg.code === tool.package_code));
    return { agent, skills, packages, uploaded, custom, tools, toolCount: tools.length,
      missing: [...skills, ...packages].some(row => row.missing) };
  }
  function resourceRow(kind, names, badge, reference = false) {
    const shown = names.slice(0, 3).join('、');
    const more = names.length > 3 ? `<span class="agent-export-more">+${names.length - 3}</span>` : '';
    return `<tr class="${reference ? 'is-reference' : 'is-included'}"><td>${escapeHtml(kind)}</td><td><div class="agent-export-content-list" title="${escapeHtml(names.join('、'))}"><span>${escapeHtml(shown)}</span>${more}</div></td><td><span class="agent-export-mode">${escapeHtml(badge)}</span></td></tr>`;
  }
  const admin = {
    generation: 0, busy: false, data: null,
    message(text, error = false) { el('agentExportStatus').textContent = text; el('agentExportStatus').classList.toggle('is-error', error); },
    controls() {
      el('confirmAgentExportBtn').disabled = this.busy || !this.data || this.data.missing;
      el('confirmAgentExportBtn').innerHTML = `${icon('download')}${this.busy && this.data ? '正在导出…' : '导出安装包'}`;
      window.lucide?.createIcons();
    },
    close() { ++this.generation; this.controller?.abort(); this.controller = null; this.busy = false; this.data = null; el('agentExportModal').close(); },
    async run(operation) {
      this.controller?.abort(); const generation = ++this.generation;
      const controller = this.controller = new AbortController(); let timeout;
      this.busy = true; this.controls();
      try {
        const result = await Promise.race([operation(controller.signal), new Promise((_, reject) => {
          timeout = setTimeout(() => { reject(new Error('请求超时，请重试。')); controller.abort(); }, 120000);
        })]);
        return generation === this.generation ? result : undefined;
      } catch (error) { if (generation === this.generation) throw error; }
      finally { clearTimeout(timeout); controller.abort(); if (generation === this.generation) { this.busy = false; this.controller = null; this.controls(); } }
    },
    async open(id) {
      if (this.busy) return;
      this.data = null; el('agentExportContent').innerHTML = '<p class="field-hint">正在读取 Agent 与关联资源…</p>';
      this.message(''); el('agentExportModal').showModal();
      try {
        const result = await this.run(signal => Promise.all([
          request(`/api/v1/agents/${encodeURIComponent(id)}`, signal), request('/api/v1/skills', signal), request('/api/v1/ui-tool-packages', signal), request('/api/v1/ui-tools', signal),
        ]));
        if (!result) return;
        this.data = overview(...result); this.render(); this.controls();
      } catch (error) { this.message(`无法读取导出内容：${error.message}`, true); }
    },
    render() {
      const data = this.data, agent = data.agent;
      const rows = [resourceRow('Agent', ['名称、提示词与关联配置'], '随包导出')];
      if (data.skills.length) rows.push(resourceRow('Skill', data.skills.map(skill => skill.label || skill.code), data.uploaded.length === data.skills.length ? '随包导出' : data.uploaded.length ? `${data.uploaded.length} 随包 · ${data.skills.length - data.uploaded.length} 引用` : '内置引用', !data.uploaded.length));
      if (data.packages.length) rows.push(resourceRow('UI 工具', data.tools.length ? data.tools.map(tool => tool.metadata?.display_name || tool.name) : data.packages.map(pkg => pkg.label || pkg.code), data.custom.length === data.packages.length ? '工具包与定义' : data.custom.length ? '导出与引用' : '内置引用', !data.custom.length));
      const mcp = (agent.capabilities || []).filter(code => code.startsWith('mcp:'));
      const builtin = (agent.capabilities || []).filter(code => code.startsWith('builtin:'));
      if (agent.model) rows.push(resourceRow('模型', [agent.model.name || '已配置模型'], '仅参考', true));
      if (mcp.length) rows.push(resourceRow('MCP', mcp, '仅参考', true));
      if (builtin.length) rows.push(resourceRow('内置工具', builtin, '内置引用', true));
      el('agentExportContent').innerHTML = `<div class="agent-export-heading"><div class="agent-export-summary"><span class="agent-export-avatar">${icon('package')}</span><strong>${escapeHtml(agent.name)}</strong></div><div class="agent-export-facts"><span><b>1</b> Agent</span><span><b>${data.skills.length}</b> Skill</span><span><b>${data.toolCount}</b> UI 工具</span></div></div><div class="table-wrap"><table class="data-table agent-export-resources"><thead><tr><th>类型</th><th>内容</th><th>说明</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
      if (data.missing) this.message('有关联资源已缺失，请先修复 Agent 配置后再导出。', true);
      window.lucide?.createIcons();
    },
    async downloadConfirmed() {
      if (this.busy || !this.data || this.data.missing) return;
      const id = this.data.agent.id; this.message('');
      try {
        const blob = await this.run(signal => request(`/api/v1/agents/${encodeURIComponent(id)}/export`, signal, true));
        if (!blob) return;
        if (!blob.size) throw new Error('导出的安装包为空。');
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = `agent-${id}.zip`; document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        this.close(); showToast('Agent 安装包已导出');
      } catch (error) { this.message(`导出失败：${error.message}`, true); }
    },
    init() {
      const form = el('agentExportForm'); if (!form || form.dataset.bound) return; form.dataset.bound = '1';
      form.addEventListener('submit', event => { event.preventDefault(); void this.downloadConfirmed(); });
      el('closeAgentExportBtn').addEventListener('click', () => this.close());
      el('cancelAgentExportBtn').addEventListener('click', () => this.close());
      el('agentExportModal').addEventListener('cancel', event => { event.preventDefault(); this.close(); });
      document.addEventListener('deep-agent-configuration-tab', event => { if (event.detail?.name !== 'agents') this.close(); });
      window.addEventListener('hashchange', () => this.close()); window.addEventListener('pagehide', () => this.close());
    },
  };
  window.agentPackageExportAdmin = admin;
  document.addEventListener('DOMContentLoaded', () => admin.init());
})();
