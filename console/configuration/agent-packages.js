/* Uploaded and bundled packages share the same preview/install flow. */
(() => {
  const el = id => document.getElementById(id);
  const statusLabels = { new: '新建', update: '覆盖', reuse: '保持不变', missing: '缺失', blocked: '无法安装', removed: '取消关联', unlinked: '取消关联' };
  const kindLabels = { agent: 'Agent', skill: 'Skill', ui: 'UI 工具包', builtin_skill: '内置 Skill', builtin_tool: '内置工具', mcp: 'MCP', ui_tool: 'UI 工具', capability: '工具包', removed: '取消关联' };
  const rows = value => Array.isArray(value) ? value : value?.items || [];
  function groupedResources(resources) {
    const groups = new Map();
    for (const resource of resources || []) {
      const kind = resource.kind || '其他';
      if (!groups.has(kind)) groups.set(kind, []);
      groups.get(kind).push(resource);
    }
    return [...groups].map(([kind, items]) => ({ kind, items }));
  }
  const option = (value, label, selected) => `<option value="${escapeHtml(value)}"${value === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
  function sourceBody(source, options) {
    const body = new FormData();
    if (source.file) body.append('file', source.file);
    else body.append('builtin_code', source.code);
    if (options) body.append('options', JSON.stringify(options));
    return body;
  }
  async function request(path, options = {}, blob = false) {
    const response = await daFetch(serviceUrl(path), { ...options, reportStatus: false });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try { const result = await response.json(); detail = window.AdminFormFeedback?.formatDetail(result.detail || result.message) || String(result.detail || result.message || detail); } catch { /* retain HTTP status */ }
      const error = new Error(detail); error.status = response.status; throw error;
    }
    return blob ? response.blob() : response.json();
  }
  const admin = {
    step: 1, mode: 'builtin', items: [], catalogErrors: [], source: null, preview: null, choices: null,
    busy: false, installing: false, generation: 0, installed: null, installResult: null,
    operationMode: 'create', targetAgentId: '', createName: '', createModelId: null, uploadState: 'idle', uploadError: '', agents: [], sharedAcknowledged: false,
    message(text, error = false, warning = false) {
      el('agentPackageStatus').textContent = text;
      el('agentPackageStatus').classList.toggle('is-error', error);
      el('agentPackageStatus').classList.toggle('is-warning', warning && Boolean(text));
    },
    async open() {
      if (this.installing) return;
      this.cancelRead(); this.step = 1; this.mode = 'builtin'; this.items = []; this.source = null;
      this.preview = null; this.choices = null; this.installed = null; this.installResult = null; this.operationMode = 'create'; this.targetAgentId = ''; this.createName = ''; this.createModelId = null; this.uploadState = 'idle'; this.uploadError = ''; this.sharedAcknowledged = false; this.catalogErrors = []; this.catalogFailed = false;
      el('agentPackageModal').showModal(); this.render();
      await this.loadCatalog();
    },
    cancelRead() { ++this.generation; this.controller?.abort(); this.controller = null; this.busy = false; },
    close(force = false) {
      if (this.installing && !force) return false;
      if (this.installing) {
        showToast('安装请求已中断，结果待确认。请在 Agent 列表核对后再安装。', true);
        void window.agentsAdmin?.refresh();
      }
      this.cancelRead(); this.installing = false; el('agentPackageModal')?.close(); return true;
    },
    async run(operation, timeoutMs = 60000) {
      this.cancelRead();
      const generation = this.generation;
      this.busy = true; this.syncButtons();
      const controller = this.controller = new AbortController();
      let timer;
      try {
        const timeout = new Promise((_, reject) => { timer = setTimeout(() => {
          const error = new Error('请求超时，请重试。'); error.uncertain = true;
          reject(error); controller.abort();
        }, timeoutMs); });
        const value = await Promise.race([operation(controller.signal), timeout]);
        return generation === this.generation ? value : undefined;
      } catch (error) { if (generation === this.generation) throw error; return undefined; }
      finally { clearTimeout(timer); if (generation === this.generation) { this.busy = false; this.controller = null; this.syncButtons(); } }
    },
    async loadCatalog() {
      this.catalogFailed = false; this.message('正在加载预制安装包…');
      try {
        const loaded = await this.run(signal => Promise.all([request('/api/v1/agent-packages', { signal }), request('/api/v1/agents', { signal })]));
        if (!loaded) return;
        const [result, agents] = loaded; this.agents = rows(agents);
        if (!result) return;
        this.items = result.items || []; this.catalogErrors = result.errors || []; this.render(); this.message('');
      } catch (error) { this.catalogFailed = true; this.message(`目录加载失败：${error.message}。可以重试或上传安装包。`, true); this.renderSource(); }
    },
    setMode(mode) {
      if (this.installing) return;
      this.cancelRead(); this.mode = mode; this.source = null; this.resetConfiguration();
      this.render();
      if (mode === 'builtin') void this.loadCatalog();
    },
    sharedAgents() {
      return [...new Map((this.preview?.resources || []).filter(item => item.status === 'update')
        .flatMap(item => item.affected_agents || []).map(agent => [agent.id, agent])).values()];
    },
    needsAcknowledgement() { return this.sharedAgents().length > 0 && !this.sharedAcknowledged; },
    syncButtons() {
      const finished = Boolean(this.installed);
      el('agentPackageTitle').textContent = finished ? '安装完成' : '安装 Agent';
      el('agentPackageSteps').hidden = finished;
      el('agentPackageBackBtn').hidden = this.step === 1 || finished;
      el('agentPackageBackBtn').disabled = this.busy;
      el('agentPackageNextBtn').disabled = this.busy || (!finished && ((this.step === 1 && (!this.source || (this.mode === 'upload' && this.uploadState === 'error'))) || (this.step === 3 && (this.isBlocked() || this.needsAcknowledgement()))));
      el('agentPackageNextBtn').hidden = false;
      el('agentPackageNextBtn').textContent = finished ? '确定' : this.busy ? (this.installing ? '正在安装…' : '正在读取…') : this.step === 3 ? '确认安装' : '下一步';
      el('cancelAgentPackageBtn').hidden = this.step !== 1;
      el('cancelAgentPackageBtn').disabled = this.installing;
      el('closeAgentPackageBtn').disabled = this.installing;
      el('agentPackageConfigureBtn').hidden = !finished;
      el('agentPackageAcknowledge').hidden = finished || this.step !== 3 || !this.sharedAgents().length;
      el('agentPackageSharedAck').checked = this.sharedAcknowledged;
      el('agentPackageSharedAck').disabled = this.busy;
      el('agentPackageContent').querySelectorAll('input, select, button').forEach(node => {
        node.disabled = finished || this.installing || (this.busy && !node.matches('[data-package-mode]')) || node.dataset.packageUnavailable === 'true';
      });
      document.querySelectorAll('[data-package-step]').forEach(node => {
        const number = Number(node.dataset.packageStep);
        node.classList.toggle('active', number === this.step);
        node.classList.toggle('passed', number < this.step || finished);
        node.setAttribute('aria-current', number === this.step ? 'step' : 'false');
        const marker = node.querySelector('b');
        if (marker) marker.innerHTML = number < this.step || finished ? '<i data-lucide="check"></i>' : String(number);
      });
      window.lucide?.createIcons();
    },
    render() {
      this.message('');
      if (this.installed) this.renderSuccess();
      else if (this.step === 1) this.renderSource();
      else if (this.step === 2) this.renderConfigure();
      else this.renderPreview();
      this.syncButtons(); window.lucide?.createIcons();
    },
    renderSource() {
      const file = this.source?.file;
      const packages = this.items;
      const catalog = `<div class="agent-package-catalog">${packages.length ? packages.map(item => `<label class="agent-package-choice ${this.source?.code === item.code ? 'selected' : ''}"><span class="agent-package-avatar"><i data-lucide="package-plus"></i></span><span class="agent-package-choice-copy"><strong>${escapeHtml(item.name)}${item.prompt_version ? `<span class="agent-package-version">提示词 ${escapeHtml(item.prompt_version)}</span>` : ''}</strong><small>${escapeHtml(item.description || item.code)}</small></span><input type="radio" name="builtinPackage" aria-label="${escapeHtml(item.name)}" value="${escapeHtml(item.code)}" ${this.source?.code === item.code ? 'checked' : ''}></label>`).join('') : '<div class="agent-package-empty"><strong>暂无预制安装包</strong><span>可以上传 Agent 安装包。</span></div>'}</div>${this.catalogErrors.map(item => `<p class="field-hint">${escapeHtml(item.code)}：${escapeHtml(item.message)}</p>`).join('')}${this.catalogFailed || this.catalogErrors.length ? '<button type="button" class="btn-link agent-package-retry" data-package-retry>重试</button>' : ''}`;
      const upload = `<div class="agent-package-dropzone" id="agentPackageDropzone"><i data-lucide="${file ? this.uploadState === 'ready' ? 'file-check-2' : this.uploadState === 'error' ? 'file-warning' : 'file' : 'cloud-upload'}"></i><strong>${escapeHtml(file?.name || '拖入安装包，或选择文件')}</strong><button type="button" class="btn" data-package-file>${file ? '重新选择' : '选择 ZIP 文件'}</button><input id="agentPackageFile" class="hidden" type="file" accept=".zip,application/zip"><small>ZIP 最大 50 MiB</small></div>${this.renderUploadInfo()}`;
      el('agentPackageContent').innerHTML = `<p class="agent-package-intro">安装完整 Agent 及包内的 Skill 和工具。</p><div class="agent-package-source-switch" role="group" aria-label="安装包来源"><button type="button" data-package-mode="builtin" class="${this.mode === 'builtin' ? 'active' : ''}"><i data-lucide="package"></i>预制安装包</button><button type="button" data-package-mode="upload" class="${this.mode === 'upload' ? 'active' : ''}"><i data-lucide="upload"></i>上传安装包</button></div>${this.mode === 'builtin' ? catalog : upload}`;
      this.syncButtons();
    },
    isBlocked() { return Boolean(this.preview?.blocked?.length || this.preview?.resources?.some(item => item.status === 'blocked')); },
    async chooseFile(file) {
      if (this.installing || !file) return;
      this.cancelRead(); this.resetConfiguration(); this.source = { file }; this.message('');
      if (!/\.zip$/i.test(file.name)) this.uploadError = '请选择 ZIP 安装包。';
      else if (file.size > 50 * 1024 * 1024) this.uploadError = '安装包不能超过 50 MiB。';
      this.uploadState = this.uploadError ? 'error' : 'loading'; this.renderSource();
      if (!this.uploadError) await this.preflight(1);
    },
    renderUploadInfo() {
      if (this.uploadState === 'loading') return '<p class="agent-package-upload-feedback" role="status">正在解析安装包…</p>';
      if (this.uploadState === 'error') return `<p class="agent-package-upload-feedback is-error" role="status">解析失败：${escapeHtml(this.uploadError)}</p>`;
      if (this.uploadState !== 'ready' || !this.preview) return '';
      const data = this.preview;
      const skills = data.resources.filter(item => ['skill', 'builtin_skill'].includes(item.kind));
      const tools = data.resources.flatMap(item => item.tools || []);
      const names = items => items.slice(0, 3).map(item => this.resourceName(item)).join('、') + (items.length > 3 ? ` 等 ${items.length} 项` : '');
      return `<div class="agent-package-upload-summary" role="status"><div><strong>${escapeHtml(data.agent.name)}</strong><span>${skills.length} Skill · ${tools.length} UI 工具</span></div>${skills.length ? `<p>Skill：${escapeHtml(names(skills))}</p>` : ''}${tools.length ? `<p>UI 工具：${escapeHtml(names(tools))}</p>` : ''}</div>`;
    },
    readSource() {
      if (this.mode === 'upload') {
        const file = el('agentPackageFile')?.files?.[0] || this.source?.file;
        if (!file) throw new Error('请选择 Agent 安装包。');
        if (!/\.zip$/i.test(file.name)) throw new Error('请选择 ZIP 安装包。');
        if (file.size > 50 * 1024 * 1024) throw new Error('安装包不能超过 50 MiB。');
        return { file };
      }
      const code = document.querySelector('input[name="builtinPackage"]:checked')?.value || this.source?.code;
      if (!code || !this.items.some(item => item.code === code)) throw new Error('请选择预制安装包。');
      return { code };
    },
    resetConfiguration() {
      this.preview = null; this.choices = null; this.createName = ''; this.createModelId = null; this.uploadState = 'idle'; this.uploadError = '';
      this.operationMode = 'create'; this.targetAgentId = ''; this.sharedAcknowledged = false;
    },
    async preflight(destination = 2) {
      try {
        this.source = this.readSource();
        if (destination === 2 && this.mode === 'upload' && this.uploadState === 'error') { this.message('请重新选择有效的安装包。', true); return; }
        if (destination === 2 && this.mode === 'upload' && this.uploadState === 'ready' && this.preview && this.choices) { this.step = 2; this.render(); return; }
        const configuring = destination === 3;
        const draft = configuring ? this.readChoices() : this.choices;
        const mode = configuring ? this.operationMode : 'create';
        const body = sourceBody(this.source); body.append('mode', mode);
        if (mode === 'overwrite') body.append('target_agent_id', this.targetAgentId);
        this.message(destination === 1 ? '' : configuring ? '正在检查安装内容…' : '正在读取安装包…');
        const loaded = await this.run(signal => Promise.all([
          request('/api/v1/agent-packages/preview', { method: 'POST', body, signal }),
          request('/api/v1/agents', { signal }),
        ]));
        if (!loaded) return;
        const [result, agents] = loaded;
        if (!result.digest || !result.agent || !Array.isArray(result.resources)) throw new Error('安装包预检结果不完整。');
        this.agents = rows(agents); this.preview = result;
        if (!draft) { this.createName = result.agent.name; this.createModelId = result.references?.model?.matched_id || null; }
        this.choices = {
          digest: result.digest, mode, target_agent_id: mode === 'overwrite' ? this.targetAgentId : null,
          name: mode === 'overwrite' ? result.target_agent.name : draft?.name ?? result.agent.name,
          model_id: draft ? draft.model_id : result.references?.model?.matched_id || null,
        };
        if (configuring) {
          this.validateChoices(this.choices);
          const target = this.agents.find(agent => agent.id === this.targetAgentId);
          if (mode === 'overwrite' && (target?.model_id || null) !== draft.model_id) {
            this.renderConfigure(); throw new Error('目标 Agent 的默认模型已变化，请确认后重试。');
          }
          if (this.choices.model_id && !(result.models || []).some(model => model.id === this.choices.model_id)) {
            this.renderConfigure();
            throw new Error(mode === 'overwrite' ? '目标 Agent 的默认模型已不可用，请先在 Agent 配置中调整后再安装。' : '所选默认模型已不可用，请重新选择。');
          }
        }
        if (destination === 1) { this.uploadState = 'ready'; this.uploadError = ''; }
        this.step = destination; this.sharedAcknowledged = false; this.render();
      } catch (error) {
        if (destination === 1) { this.uploadState = 'error'; this.uploadError = error.message; this.renderSource(); this.message(''); }
        else this.message(error.message, true);
      }
    },
    resourceName(item) { return item.label || item.name || item.code || kindLabels[item.kind] || '资源'; },
    actionLabel(item, finished = false) {
      if (item.status === 'reuse' && ['mcp', 'builtin_tool', 'builtin_skill'].includes(item.kind)) return finished ? '已关联' : '自动关联';
      if (item.status === 'update' && item.kind === 'ui') return finished ? '已更新' : '更新';
      if (finished && ['new', 'update', 'unlinked', 'removed'].includes(item.status)) return { new: '已新建', update: '已覆盖', unlinked: '已取消关联', removed: '已取消关联' }[item.status];
      return statusLabels[item.status] || item.status;
    },
    renderResources(resources, finished = false) {
      const entries = [];
      for (const { kind, items } of groupedResources(resources)) {
        for (const item of items) {
          const notes = [];
          if (kind === 'agent') notes.push(this.operationMode === 'overwrite' ? '替换提示词与关联，保留会话及外部引用' : '安装提示词与关联配置');
          if (item.status === 'missing') notes.push(item.kind === 'mcp'
            ? '未找到同名 MCP；请在 MCP 中配置服务，再到 Agent 中关联，否则相关工具不可用。'
            : ['builtin_tool', 'builtin_skill'].includes(item.kind)
              ? '当前环境缺少该内置能力；请检查部署版本，补齐后在 Agent 中关联。'
              : '该资源缺失；请先安装资源，再到 Agent 配置中关联。');
          if (['unlinked', 'removed'].includes(item.status)) notes.push('资源保留');
          if (item.status === 'update' && item.affected_agents?.length) notes.push(`影响：${item.affected_agents.map(agent => agent.name).join('、')}`);
          entries.push({ ...item, note: notes.join('；') });
          for (const tool of item.tools || []) entries.push({ ...tool, kind: 'ui_tool', note: '' });
          for (const name of item.retained_tools || []) entries.push({ kind: 'ui_tool', name, status: 'retained', note: '目标已有；整包启用后可用' });
        }
      }
      return `<div class="agent-package-table-wrap"><table class="agent-package-table"><colgroup><col class="package-col-kind"><col class="package-col-content"><col class="package-col-action"><col class="package-col-note"></colgroup><thead><tr><th>类型</th><th>内容</th><th>处理</th><th>说明</th></tr></thead><tbody>${entries.map(item => `<tr class="package-row-${escapeHtml(item.status)}"><td>${escapeHtml(kindLabels[item.kind] || item.kind)}</td><td class="package-resource-name">${escapeHtml(this.resourceName(item))}</td><td><span class="agent-package-action action-${escapeHtml(item.status)}">${escapeHtml(item.status === 'retained' ? '保留' : this.actionLabel(item, finished))}</span></td><td class="package-resource-note">${escapeHtml(item.note)}</td></tr>`).join('')}</tbody></table></div>`;
    },
    previewResources() {
      const data = this.preview;
      return [{ kind: 'agent', label: this.choices?.name || data.target_agent?.name || data.agent.name, status: this.operationMode === 'overwrite' ? 'update' : 'new' }, ...data.resources,
        ...(data.references?.mcps || []).map(item => ({ kind: 'mcp', label: item.code, code: item.code, status: item.matched_code ? 'reuse' : 'missing' }))];
    },
    renderPreview() {
      const removed = this.preview.removed_associations || [];
      el('agentPackageContent').innerHTML = `${this.renderSummary()}${this.renderResources([...this.previewResources(), ...removed])}`;
      if (this.isBlocked()) this.message((this.preview.blocked || []).map(item => typeof item === 'string' ? item : item.message || item.label || item.code).join('；') || '存在无法安装的资源，请先修复。', true);
      else this.message('');
    },
    validateChoices(choices) {
      if (this.operationMode === 'overwrite') {
        if (!this.agents.some(agent => agent.id === this.targetAgentId)) throw new Error('请选择要覆盖的 Agent。');
      } else {
        if (!choices.name) throw new Error('请输入 Agent 名称。');
        if (this.agents.some(agent => agent.name === choices.name)) throw new Error(`Agent「${choices.name}」已存在，请修改名称或选择覆盖。`);
      }
    },
    readChoices(validate = true) {
      if (this.operationMode === 'create') { this.createName = el('agentPackageName').value.trim(); this.createModelId = el('agentPackageModel').value || null; }
      const target = this.agents.find(agent => agent.id === this.targetAgentId);
      const choices = { ...this.choices, mode: this.operationMode, target_agent_id: this.operationMode === 'overwrite' ? this.targetAgentId : null,
        name: this.operationMode === 'overwrite' ? target?.name || '' : this.createName,
        model_id: this.operationMode === 'overwrite' ? target?.model_id || null : this.createModelId };
      if (validate) this.validateChoices(choices);
      return choices;
    },
    renderConfigure() {
      const data = this.preview, choices = this.choices;
      const operation = `<div class="agent-package-form-row"><span id="agentPackageOperationLabel">安装方式</span><div class="agent-package-radios" role="radiogroup" aria-labelledby="agentPackageOperationLabel">${[['create', '新建'], ['overwrite', '覆盖']].map(([value, label]) => `<label><input type="radio" name="packageOperation" value="${value}" ${this.operationMode === value ? 'checked' : ''}>${label}</label>`).join('')}</div></div>`;
      const name = this.operationMode === 'overwrite'
        ? `<label class="field agent-package-form-row"><span>覆盖目标</span><select id="agentPackageTarget" aria-label="覆盖目标"><option value="" hidden ${!this.targetAgentId ? 'selected' : ''}>${this.agents.length ? '选择 Agent' : '暂无 Agent'}</option>${this.agents.map(agent => option(agent.id, agent.name, this.targetAgentId)).join('')}</select></label>`
        : `<label class="field agent-package-form-row"><span>名称</span><input id="agentPackageName" aria-label="Agent 名称" maxlength="191" value="${escapeHtml(this.createName)}" required></label>`;
      const model = this.operationMode === 'overwrite'
        ? ''
        : `<label class="field agent-package-form-row"><span>默认模型</span><select id="agentPackageModel" aria-label="默认模型" data-package-unavailable="${!data.models?.length}"><option value="" hidden ${!choices.model_id ? 'selected' : ''}>${data.models?.length ? '选择默认模型' : '没选'}</option>${(data.models || []).map(model => option(model.id, model.name, choices.model_id)).join('')}</select></label>`;
      el('agentPackageContent').innerHTML = `<div class="agent-package-configure">${operation}${name}${model}</div>`;
    },
    modelLabel(id, agent = null) {
      if (!id) return '没选';
      return (this.preview?.models || []).find(model => model.id === id)?.name || agent?.model?.name || agent?.model_name || id;
    },
    renderSummary() {
      const choices = this.choices;
      const target = this.agents.find(agent => agent.id === this.targetAgentId);
      return `<div class="agent-package-summary"><span>${this.operationMode === 'overwrite' ? '覆盖' : '新建'} <strong>${escapeHtml(choices.name)}</strong></span><span>默认模型：${escapeHtml(this.modelLabel(choices.model_id, target))}</span></div>`;
    },
    renderSuccess() {
      const result = this.installResult, agent = this.installed;
      const resources = result?.results || [];
      el('agentPackageContent').innerHTML = `<div class="agent-package-completed"><i data-lucide="circle-check"></i><strong>${result?.mode === 'overwrite' ? '已覆盖' : '已新建'} ${escapeHtml(agent.name)}</strong><span>默认模型：${escapeHtml(this.modelLabel(agent.model_id, agent))}</span></div>${this.renderResources(resources, true)}`;
      this.message('');
    },
    back() {
      if (this.busy || this.installed) return;
      if (this.step === 2) this.choices = this.readChoices(false);
      this.step = Math.max(1, this.step - 1); this.render();
    },
    async next(event) {
      event?.preventDefault(); if (this.busy) return;
      if (this.installed) return this.close();
      if (this.step === 1) return this.preflight();
      if (this.step === 2) return this.preflight(3);
      if (this.isBlocked()) { this.message('存在无法安装的资源，请先修复。', true); return; }
      if (this.needsAcknowledgement()) { this.message('请确认共享资源变更的影响。', true); return; }
      return this.install();
    },
    async install() {
      if (this.installing || this.installed || !this.preview || !this.choices || this.isBlocked() || this.needsAcknowledgement()) return;
      this.installing = true; this.message('正在安装，请稍候…');
      const generation = this.generation + 1; // run() starts a new request generation.
      try {
        const result = await this.run(signal => request('/api/v1/agent-packages/install', { method: 'POST', body: sourceBody(this.source, this.choices), signal }), 120000);
        if (!result) return;
        if (!result.agent?.id) throw new Error('安装结果不完整。');
        this.installResult = result; this.installed = result.agent; this.step = 4;
        this.renderSuccess();
        window.lucide?.createIcons();
        void Promise.allSettled([window.agentsAdmin?.refresh(), loadSkills(), loadCapabilities(), loadLlmModels()]);
      } catch (error) {
        const uncertain = error.uncertain || !error.status || error.status >= 500;
        if (uncertain) {
          this.preview = null; this.choices = null; this.step = 1; this.renderSource();
          this.message('安装结果待确认，请先在 Agent 列表核对后再安装。' + error.message, true);
          void window.agentsAdmin?.refresh();
        } else if (error.status === 409) {
          this.preview = null; this.choices = null; this.step = 1; this.renderSource();
          this.message(`${error.message} 请重新预检并核对名称与资源。`, true);
        } else this.message(error.message, true);
      } finally { if (generation === this.generation) { this.installing = false; this.syncButtons(); } }
    },
    async exportAgent(id, button) {
      if (button?.disabled) return;
      return window.agentPackageExportAdmin.open(id);
    },
    init() {
      const form = el('agentPackageForm'); if (!form || form.dataset.bound) return;
      form.dataset.bound = '1';
      el('installAgentBtn').addEventListener('click', () => this.open());
      form.addEventListener('submit', event => this.next(event));
      el('agentPackageBackBtn').addEventListener('click', () => this.back());
      el('cancelAgentPackageBtn').addEventListener('click', () => this.close());
      el('closeAgentPackageBtn').addEventListener('click', () => this.close());
      el('agentPackageConfigureBtn').addEventListener('click', () => { if (!this.installed) return; const id = this.installed.id; this.close(); void window.agentsAdmin.openEdit(id); });
      el('agentPackageSharedAck').addEventListener('change', event => { this.sharedAcknowledged = event.target.checked; this.message(''); this.syncButtons(); });
      el('agentPackageModal').addEventListener('cancel', event => { event.preventDefault(); this.close(); });
      el('agentPackageContent').addEventListener('click', event => {
        const mode = event.target.closest('[data-package-mode]'); if (mode) this.setMode(mode.dataset.packageMode);
        if (event.target.closest('[data-package-file]')) el('agentPackageFile').click();
        if (event.target.closest('[data-package-retry]')) void this.loadCatalog();
      });
      el('agentPackageContent').addEventListener('change', event => {
        if (event.target.id === 'agentPackageModel') { this.choices.model_id = event.target.value || null; this.createModelId = this.choices.model_id; }
        if (event.target.name === 'packageOperation') { this.choices = this.readChoices(false); this.operationMode = event.target.value; if (this.operationMode === 'create') this.choices.model_id = this.createModelId; this.sharedAcknowledged = false; this.message(''); this.renderConfigure(); this.syncButtons(); }
        if (event.target.id === 'agentPackageTarget') { this.targetAgentId = event.target.value; this.message(''); this.renderConfigure(); this.syncButtons(); }
        if (event.target.id === 'agentPackageFile') this.chooseFile(event.target.files?.[0]);
        if (event.target.name === 'builtinPackage') { this.source = { code: event.target.value }; this.resetConfiguration(); this.renderSource(); }
      });
      el('agentPackageContent').addEventListener('dragover', event => {
        const zone = event.target.closest('#agentPackageDropzone');
        if (zone && !this.busy) { event.preventDefault(); zone.classList.add('dragging'); }
      });
      el('agentPackageContent').addEventListener('dragleave', event => event.target.closest('#agentPackageDropzone')?.classList.remove('dragging'));
      el('agentPackageContent').addEventListener('drop', event => {
        const zone = event.target.closest('#agentPackageDropzone');
        if (!zone) return;
        event.preventDefault(); zone.classList.remove('dragging');
        if (event.dataTransfer?.files?.length !== 1) { this.cancelRead(); this.source = null; this.resetConfiguration(); this.uploadState = 'error'; this.uploadError = '一次请选择一个安装包。'; this.message(''); this.renderSource(); }
        else this.chooseFile(event.dataTransfer.files[0]);
      });
      document.addEventListener('deep-agent-configuration-tab', event => { if (event.detail?.name !== 'agents' && el('agentPackageModal').open) this.close(true); });
      window.addEventListener('hashchange', () => { if (el('agentPackageModal').open) this.close(true); });
      window.addEventListener('pagehide', () => this.close(true));
    },
  };
  window.agentPackagesAdmin = admin;
  window.__AgentPackageUI = { sourceBody, statusLabels, kindLabels, groupedResources };
  document.addEventListener('DOMContentLoaded', () => admin.init());
})();
