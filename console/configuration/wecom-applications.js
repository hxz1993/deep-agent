/* WeCom application configuration is shared by channels; member rows stay backend-only. */
(() => {
  const el = id => document.getElementById(id);
  const path = id => `/api/v1/wecom-applications${id ? `/${encodeURIComponent(id)}` : ''}`;
  const state = { items: [], selectedId: null, editingId: null, cache: null, active: false, busy: false, jobs: new Map(), cacheMessages: new Map(), generation: 0 };
  const requestControllers = new Set();
  let pollTimer = null;
  let cacheConfirmation = null;
  function stopReads() {
    closeCacheConfirmation();
    state.generation++;
    clearTimeout(pollTimer);
    requestControllers.forEach(controller => controller.abort());
    requestControllers.clear();
  }
  async function read(url) {
    const controller = new AbortController();
    requestControllers.add(controller);
    try { return await apiFetch(url, { signal: controller.signal }); }
    finally { requestControllers.delete(controller); }
  }
  async function mutate(url, method, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try { return await apiFetch(url, { method, signal: controller.signal, ...(body ? { body: JSON.stringify(body) } : {}) }); }
    catch (error) {
      if (controller.signal.aborted) throw new Error('请求超时，结果待确认；请重新打开应用核对。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function current() { return state.items.find(item => item.id === state.selectedId); }
  function status(message = '', error = false) {
    const target = el('wecomApplicationStatus');
    if (!target) return;
    target.textContent = message;
    target.classList.toggle('error', error);
  }
  function setBusy(value) {
    state.busy = value;
    el('wecomApplicationForm')?.querySelectorAll('input, button').forEach(node => { node.disabled = value; });
    el('wecomApplicationDetail')?.querySelectorAll('button').forEach(node => { node.disabled = value; });
    if (!value) renderJob();
  }
  function renderList() {
    const list = el('wecomApplicationList');
    const scroll = list.scrollTop;
    list.innerHTML = state.items.length ? state.items.map(item => `<button type="button" class="provider-list-item wecom-application-list-item ${item.id === state.selectedId ? 'active' : ''}" data-wecom-application="${escapeHtml(item.id)}" aria-pressed="${item.id === state.selectedId}"><span class="provider-list-copy"><strong>${escapeHtml(item.name)}</strong><small>Agent ID ${escapeHtml(item.wecom_agent_id)}</small></span></button>`).join('') : '<div class="provider-list-state"><span>暂无企微应用</span><button type="button" class="btn" data-wecom-new><i data-lucide="plus"></i>新建应用</button></div>';
    list.scrollTop = scroll;
    list.querySelectorAll('[data-wecom-application]').forEach(button => button.addEventListener('click', () => admin.select(button.dataset.wecomApplication)));
    list.querySelector('[data-wecom-new]')?.addEventListener('click', () => admin.openCreate());
    window.lucide?.createIcons();
  }
  function renderDetail() {
    closeCacheConfirmation();
    const item = current();
    if (!item) { el('wecomApplicationDetail').innerHTML = '<p class="provider-detail-empty">选择一个企微应用查看详情</p>'; return; }
    const channels = Array.isArray(item.channels) ? item.channels : [];
    const linked = channels.length ? `<ul class="wecom-linked-channels">${channels.map(channel => `<li>${escapeHtml(channel.name)}</li>`).join('')}</ul>` : '<span class="wecom-placeholder">未关联</span>';
    el('wecomApplicationDetail').innerHTML = `<div class="wecom-detail-content">
      <header class="wecom-detail-heading"><h3>${escapeHtml(item.name)}</h3><div class="wecom-detail-actions"><button type="button" class="btn" data-wecom-action="edit"><i data-lucide="pencil"></i>编辑</button><button type="button" class="btn" data-wecom-action="test"><i data-lucide="plug-zap"></i>测试连接</button><button type="button" class="btn danger" data-wecom-action="delete"><i data-lucide="trash-2"></i>删除</button></div></header>
      <p id="wecomApplicationStatus" class="wecom-inline-status" role="status"></p>
      <section class="wecom-info-card" aria-label="应用配置"><dl class="wecom-detail-fields"><div><dt>企业 ID</dt><dd>${escapeHtml(item.corp_id)}</dd></div><div><dt>企微 Agent ID</dt><dd>${escapeHtml(item.wecom_agent_id)}</dd></div><div><dt>App Secret</dt><dd>${escapeHtml(item.app_secret_masked || '未配置')}</dd></div><div><dt>关联渠道</dt><dd>${linked}</dd></div></dl></section>
      <section class="wecom-cache-region" aria-label="成员缓存"><div class="wecom-card-heading"><span class="field-label-row"><h3>成员缓存</h3><span class="field-help" tabindex="0" aria-label="成员缓存说明" aria-describedby="wecomCacheHelp"><i data-lucide="circle-help" aria-hidden="true"></i><span class="field-help-tooltip" id="wecomCacheHelp" role="tooltip">仅缓存已遇到的用户，姓名资料有效期为一周；过期后在收到消息时按需刷新。</span></span></span><div class="wecom-detail-actions"><button type="button" class="btn" data-wecom-action="refresh" aria-haspopup="dialog" aria-expanded="false"><i data-lucide="refresh-cw"></i>刷新缓存</button><button type="button" class="btn" data-wecom-action="invalidate" aria-haspopup="dialog" aria-expanded="false"><i data-lucide="rotate-ccw"></i>重置缓存</button></div></div>
        <p id="wecomCacheStatus" class="wecom-inline-status" role="status"></p><dl class="wecom-detail-fields" id="wecomApplicationCache"><div><dt>已缓存用户</dt><dd class="wecom-placeholder">正在读取…</dd></div></dl>
      </section>
    </div>`;
    el('wecomApplicationDetail').querySelectorAll('[data-wecom-action]').forEach(button => button.addEventListener('click', () => {
      const action = button.dataset.wecomAction;
      if (action === 'edit') admin.openEdit();
      else if (action === 'test') admin.test();
      else if (action === 'delete') admin.remove();
      else askCacheConfirmation(button, action);
    }));
    renderCache();
    renderJob();
    window.lucide?.createIcons();
  }
  function closeCacheConfirmation({ focus = false } = {}) {
    if (!cacheConfirmation) return;
    const { node, trigger } = cacheConfirmation;
    cacheConfirmation = null;
    trigger.setAttribute('aria-expanded', 'false');
    if (node.matches(':popover-open')) node.hidePopover();
    node.remove();
    if (focus && trigger.isConnected) trigger.focus();
  }
  function askCacheConfirmation(trigger, action) {
    if (state.busy || running(state.jobs.get(state.selectedId))) return;
    if (cacheConfirmation?.trigger === trigger) { closeCacheConfirmation({ focus: true }); return; }
    closeCacheConfirmation();
    const id = state.selectedId;
    const node = document.createElement('div');
    node.className = 'wecom-cache-confirm';
    node.setAttribute('popover', 'auto');
    node.setAttribute('role', 'dialog');
    node.setAttribute('aria-label', action === 'refresh' ? '刷新成员缓存确认' : '重置成员缓存确认');
    const copy = action === 'refresh' ? '更新所有已缓存成员的姓名和 7 天有效期；不拉取全公司通讯录。' : '保留姓名和 ID 映射，仅将资料标记为过期；下次收到消息时按需更新。';
    node.innerHTML = `<p>${copy}</p><div class="wecom-detail-actions"><button type="button" class="btn" data-cache-cancel>取消</button><button type="button" class="btn primary" data-cache-confirm>${action === 'refresh' ? '确认刷新' : '确认重置'}</button></div>`;
    el('wecomApplicationDetail').appendChild(node);
    cacheConfirmation = { node, trigger };
    trigger.setAttribute('aria-expanded', 'true');
    node.showPopover?.();
    const bounds = trigger.getBoundingClientRect();
    node.style.left = `${Math.max(12, Math.min(window.innerWidth - node.offsetWidth - 12, bounds.right - node.offsetWidth))}px`;
    const below = bounds.bottom + 8;
    node.style.top = `${Math.max(12, below + node.offsetHeight < window.innerHeight - 12 ? below : bounds.top - node.offsetHeight - 8)}px`;
    node.querySelector('[data-cache-cancel]').addEventListener('click', () => closeCacheConfirmation({ focus: true }));
    node.querySelector('[data-cache-confirm]').addEventListener('click', () => {
      closeCacheConfirmation({ focus: true });
      if (state.selectedId !== id || !state.active) return;
      if (action === 'refresh') void admin.refreshCache();
      else void admin.invalidate();
    });
    node.addEventListener('toggle', event => { if (event.newState === 'closed' && cacheConfirmation?.node === node) closeCacheConfirmation(); });
    node.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeCacheConfirmation({ focus: true }); } });
    node.querySelector('[data-cache-cancel]').focus();
  }
  function renderCache() {
    const target = el('wecomApplicationCache');
    if (!target || !state.cache) return;
    const value = state.cache;
    const time = date => date ? escapeHtml(formatDate(date)) : '<span class="wecom-placeholder">—</span>';
    target.innerHTML = `<div><dt>已缓存用户</dt><dd>${Number(value.total) || 0} 人 <span class="wecom-placeholder">（${Number(value.pending) || 0} 人待更新）</span></dd></div><div><dt>最早创建</dt><dd>${time(value.earliest_created_at)}</dd></div><div><dt>最早更新</dt><dd>${time(value.earliest_synced_at)}</dd></div>`;
  }
  function running(job) { return !!job && ['queued', 'pending', 'running'].includes(job.status); }
  function cacheStatus(message, error = false) {
    state.cacheMessages.set(state.selectedId, { message, error });
    renderJob();
  }
  function renderJob() {
    const feedback = state.cacheMessages.get(state.selectedId);
    const job = state.jobs.get(state.selectedId);
    const target = el('wecomCacheStatus');
    if (target) {
      target.textContent = feedback ? feedback.message : !job ? '' : running(job) ? `正在刷新 ${job.completed || 0} / ${job.total || 0}` : (job.unknown || job.status === 'unknown') ? '结果待确认，已重新读取缓存统计。' : `刷新结束：成功 ${job.succeeded || 0}，失败 ${job.failed || 0}${job.status === 'failed' ? '；任务未完成，请重试。' : ''}`;
      target.classList.toggle('error', feedback ? feedback.error : !!job && (job.unknown || job.status === 'unknown' || job.status === 'failed' || job.failed > 0));
    }
    el('wecomApplicationDetail')?.querySelectorAll('[data-wecom-action="refresh"], [data-wecom-action="invalidate"]').forEach(button => { button.disabled = state.busy || running(job); });
  }
  async function loadCache(id, generation) {
    try {
      const result = await read(`${path(id)}/cache`);
      if (state.selectedId !== id || state.generation !== generation) return;
      state.cache = result; renderCache(); return true;
    } catch (error) {
      if (state.generation === generation && state.selectedId === id) cacheStatus(`缓存统计读取失败：${error.message}`, true);
      return false;
    }
  }
  function schedulePoll(id) {
    clearTimeout(pollTimer);
    if (!state.active || id !== state.selectedId || !running(state.jobs.get(id))) return;
    pollTimer = setTimeout(() => poll(id), 1500);
  }
  async function poll(id) {
    const generation = state.generation;
    const job = state.jobs.get(id);
    if (!state.active || !job || id !== state.selectedId) return;
    try {
      const next = await read(`${path(id)}/cache/refresh/${encodeURIComponent(job.task_id)}`);
      if (state.generation !== generation || state.selectedId !== id) return;
      state.jobs.set(id, next); renderJob();
      if (running(next)) schedulePoll(id);
      else await loadCache(id, generation);
    } catch (error) {
      if (state.generation !== generation || state.selectedId !== id) return;
      // A restart may discard an in-memory task. A failed poll never proves success.
      state.jobs.set(id, { ...job, status: 'unknown', unknown: true }); renderJob();
      await loadCache(id, generation);
    }
  }
  const admin = {
    async refresh() {
      state.active = true;
      stopReads();
      const generation = state.generation;
      try {
        const items = await read(path());
        if (generation !== state.generation) return;
        if (!Array.isArray(items)) throw new Error('企微应用列表格式不正确');
        state.items = items;
        if (!items.some(item => item.id === state.selectedId)) state.selectedId = items[0]?.id || null;
        renderList();
        if (state.selectedId) await this.select(state.selectedId);
        else renderDetail();
      } catch (error) {
        if (generation !== state.generation) return;
        el('wecomApplicationList').innerHTML = `<p class="provider-list-state error">${escapeHtml(error.message)}</p>`;
      }
    },
    async select(id) {
      if (state.busy || !state.items.some(item => item.id === id)) return;
      stopReads();
      const generation = state.generation;
      state.selectedId = id; state.cache = null;
      renderList(); renderDetail();
      try {
        const item = await read(path(id));
        if (generation !== state.generation || state.selectedId !== id) return;
        state.items = state.items.map(old => old.id === id ? item : old);
        renderList(); renderDetail();
        await loadCache(id, generation);
        if (generation === state.generation) schedulePoll(id);
      } catch (error) { if (generation === state.generation) status(error.message, true); }
    },
    deactivate() {
      if (state.busy) return false;
      state.active = false; stopReads(); this.closeEditor(); return true;
    },
    openCreate() { this.openEditor(null); },
    openEdit() { if (current()) this.openEditor(current()); },
    openEditor(item) {
      if (state.busy) return;
      state.editingId = item?.id || null;
      el('wecomApplicationModalTitle').textContent = item ? '编辑企微应用' : '新建企微应用';
      el('wecomApplicationName').value = item?.name || '';
      el('wecomApplicationCorpId').value = item?.corp_id || '';
      el('wecomApplicationAgentId').value = item?.wecom_agent_id || '';
      el('wecomApplicationCorpId').readOnly = !!item;
      el('wecomApplicationAgentId').readOnly = !!item;
      const input = el('wecomApplicationSecret');
      input.dataset.maskedValue = item?.app_secret_masked || '';
      input.dataset.clearRequested = 'false';
      const clearButton = el('clearWecomApplicationSecretBtn');
      clearButton.classList.toggle('hidden', !item?.secret_set && !input.dataset.maskedValue);
      clearButton.textContent = '清空';
      clearButton.classList.toggle('is-undo', false);
      input.value = input.dataset.maskedValue;
      input.type = input.value ? 'text' : 'password';
      input.required = !item;
      input.placeholder = item ? '留空则保持不变' : '请输入 App Secret';
      AdminFormFeedback.clear(el('wecomApplicationForm'));
      el('wecomApplicationModal').showModal();
    },
    closeEditor() {
      if (state.busy) return;
      el('wecomApplicationModal').close();
      el('wecomApplicationSecret').value = '';
      el('wecomApplicationSecret').dataset.clearRequested = 'false';
      state.editingId = null;
    },
    async save(event) {
      event.preventDefault();
      if (state.busy || !event.currentTarget.reportValidity()) return;
      const id = state.editingId;
      const payload = { name: el('wecomApplicationName').value.trim() };
      if (!id) { payload.corp_id = el('wecomApplicationCorpId').value.trim(); payload.wecom_agent_id = el('wecomApplicationAgentId').value.trim(); }
      const input = el('wecomApplicationSecret');
      const secret = input.value.trim();
      if (secret && secret !== input.dataset.maskedValue) payload.app_secret = secret;
      else if (id && input.dataset.clearRequested === 'true') payload.clear_app_secret = true;
      setBusy(true);
      let saved = false;
      try {
        const result = await mutate(path(id), id ? 'PUT' : 'POST', payload);
        saved = true;
        state.selectedId = result.id;
        setBusy(false); this.closeEditor();
        showToast(id ? '企微应用已更新' : '企微应用已创建');
        await this.refresh();
      } catch (error) { AdminFormFeedback.show(el('wecomApplicationForm'), `${saved ? '已保存，读取结果失败：' : ''}${error.message}`); }
      finally { setBusy(false); }
    },
    async test() {
      if (state.busy || !current()) return;
      const id = state.selectedId;
      setBusy(true); status('正在测试连接…');
      try {
        const result = await mutate(`${path(id)}/test-connection`, 'POST');
        status(result.success ? '连接成功；成员查询范围仍取决于应用权限。' : (result.message || '连接失败'), !result.success);
      } catch (error) { status(error.message, true); }
      finally { setBusy(false); }
    },
    async remove(applicationId = state.selectedId) {
      if (state.busy) return;
      const item = state.items.find(value => value.id === applicationId);
      if (!item) return;
      if (!(await confirmAction({ title: '删除企微应用？', message: `删除“${item.name}”及其成员缓存；已关联渠道需先清空引用。`, tone: 'danger', icon: 'trash-2', confirmLabel: '删除' }))) return;
      setBusy(true);
      try {
        await mutate(path(item.id), 'DELETE');
        state.jobs.delete(item.id); if (state.selectedId === item.id) state.selectedId = null;
        setBusy(false); await this.refresh(); showToast('企微应用已删除');
      } catch (error) { status(error.message, true); }
      finally { setBusy(false); }
    },
    async refreshCache() {
      if (state.busy || !current() || running(state.jobs.get(state.selectedId))) return;
      const id = state.selectedId;
      stopReads();
      state.jobs.delete(id);
      setBusy(true); cacheStatus('正在开始刷新…');
      try {
        const job = await mutate(`${path(id)}/cache/refresh`, 'POST');
        state.jobs.set(id, job); state.cacheMessages.delete(id); renderJob();
        if (running(job)) schedulePoll(id);
        else await loadCache(id, state.generation);
      } catch (error) { cacheStatus(error.message, true); }
      finally { setBusy(false); }
    },
    async invalidate() {
      if (state.busy || !current() || running(state.jobs.get(state.selectedId))) return;
      const id = state.selectedId;
      stopReads();
      state.jobs.delete(id);
      setBusy(true); cacheStatus('正在重置缓存…');
      try {
        await mutate(`${path(id)}/cache/invalidate`, 'POST');
        state.jobs.delete(id); renderJob();
        if (await loadCache(id, state.generation)) cacheStatus('成员资料已标记为过期，姓名和 ID 映射保留。');
      } catch (error) { cacheStatus(error.message, true); }
      finally { setBusy(false); }
    },
  };
  window.wecomApplicationsAdmin = admin;
  document.addEventListener('DOMContentLoaded', () => {
    el('newWecomApplicationBtn')?.addEventListener('click', () => admin.openCreate());
    el('wecomApplicationForm')?.addEventListener('submit', event => admin.save(event));
    ['closeWecomApplicationModal', 'cancelWecomApplicationBtn'].forEach(id => el(id)?.addEventListener('click', () => admin.closeEditor()));
    el('wecomApplicationModal')?.addEventListener('cancel', event => { event.preventDefault(); admin.closeEditor(); });
    el('wecomApplicationSecret')?.addEventListener('focus', event => { if (event.target.value === event.target.dataset.maskedValue) event.target.select(); });
    el('wecomApplicationSecret')?.addEventListener('input', event => {
      event.target.type = event.target.value && event.target.value === event.target.dataset.maskedValue ? 'text' : 'password';
      event.target.dataset.clearRequested = 'false';
      const button = el('clearWecomApplicationSecretBtn');
      button.textContent = '清空'; button.classList.toggle('is-undo', false);
    });
    el('clearWecomApplicationSecretBtn')?.addEventListener('click', () => {
      if (state.busy) return;
      const input = el('wecomApplicationSecret');
      const clear = input.dataset.clearRequested !== 'true';
      input.dataset.clearRequested = String(clear);
      input.value = clear ? '' : input.dataset.maskedValue;
      input.type = clear || !input.value ? 'password' : 'text';
      input.placeholder = clear ? '保存后清空 App Secret' : '留空则保持不变';
      const button = el('clearWecomApplicationSecretBtn');
      button.textContent = clear ? '撤销清空' : '清空'; button.classList.toggle('is-undo', clear);
    });
  });
  document.addEventListener('deep-agent-configuration-tab', event => { if (event.detail?.name !== 'channels') admin.deactivate(); });
  window.addEventListener('resize', () => closeCacheConfirmation());
  document.addEventListener('scroll', () => closeCacheConfirmation(), true);
  window.addEventListener('pagehide', () => { state.active = false; stopReads(); });
})();
