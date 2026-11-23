/* Shared single-app selector for configuration. */
(() => {
  const el = id => document.getElementById(id);
  const pageSize = 100;
  const cacheMs = 30_000;
  const cache = new Map();
  const picker = {
    current: null,
    open({ owner, path, title, selectedAppId = null, onSelect }) {
      if (this.current) return;
      const state = this.current = {
        owner, path, onSelect, apps: [], tenant: '', selection: selectedAppId,
        limit: pageSize, directoryRequest: null, state: 'loading',
      };
      el('integrationAppPickerTitle').textContent = title;
      el('integrationAppPickerInput').value = '';
      el('integrationAppPickerTenants').replaceChildren();
      el('retryIntegrationAppPickerBtn').hidden = true;
      const cached = cache.get(path);
      if (cached && cached.expiresAt > Date.now()) this.setAppChoices(state, cached.apps);
      else {
        this.renderAppChoices(state);
        void this.loadAppChoices(state);
      }
      el('integrationAppPicker').showModal();
    },
    invalidate(path) {
      cache.delete(path);
    },
    async getApps(path, { signal } = {}) {
      const cached = cache.get(path);
      if (cached && cached.expiresAt > Date.now()) return cached.apps;
      const apps = await apiFetch(path, { signal, reportStatus: false });
      if (!Array.isArray(apps)) throw new Error('应用列表加载失败。');
      cache.set(path, { apps, expiresAt: Date.now() + cacheMs });
      return apps;
    },
    setAppChoices(picker, apps) {
      const selectedIndex = apps.findIndex(app => app.app_id === picker.selection);
      // Keep an existing selection visible without expanding the initial page.
      picker.apps = selectedIndex >= pageSize
        ? [apps[selectedIndex], ...apps.slice(0, selectedIndex), ...apps.slice(selectedIndex + 1)]
        : apps;
      picker.state = 'ready';
      const tenants = new Map(apps.map(app => [app.tenant_id, app.tenant_name || app.tenant_id]));
      const choices = [...tenants].sort((a, b) => a[1].localeCompare(b[1], 'zh-CN'));
      el('integrationAppPickerTenants').innerHTML = [['', '全部企业'], ...choices].map(([id, name]) =>
        `<button type="button" class="integration-directory-item" data-picker-tenant="${escapeHtml(id)}" aria-pressed="false"><span title="${escapeHtml(name)}">${escapeHtml(name)}</span></button>`
      ).join('');
      this.renderAppChoices(picker);
    },
    async loadAppChoices(picker) {
      if (this.current !== picker || picker.directoryRequest) return;
      const controller = picker.directoryRequest = new AbortController();
      const status = el('integrationAppPickerStatus');
      el('retryIntegrationAppPickerBtn').hidden = true;
      picker.state = 'loading';
      status.textContent = '正在加载应用…';
      try {
        const apps = await this.getApps(picker.path, { signal: controller.signal });
        if (this.current !== picker) return;
        this.setAppChoices(picker, apps);
      } catch (error) {
        if (this.current !== picker) return;
        picker.state = 'error';
        status.textContent = error.message || '应用列表加载失败。';
        el('retryIntegrationAppPickerBtn').hidden = false;
      } finally {
        picker.directoryRequest = null;
      }
    },
    renderAppChoices(picker, resetScroll = true) {
      const terms = el('integrationAppPickerInput').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const matches = picker.apps.filter(app => {
        if (picker.tenant && app.tenant_id !== picker.tenant) return false;
        if (!terms.length) return true;
        const text = [app.app_name, app.app_id, app.space_name, app.space_id, app.tenant_name, app.tenant_id].join(' ').toLowerCase();
        return terms.every(term => text.includes(term));
      });
      const visible = matches.slice(0, picker.limit);
      if (picker.state === 'ready' && !visible.some(app => app.app_id === picker.selection)) picker.selection = null;
      for (const button of el('integrationAppPickerTenants').querySelectorAll('[data-picker-tenant]')) {
        const selected = picker.tenant === button.dataset.pickerTenant;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
      }
      const options = el('integrationAppPickerOptions');
      const container = options.parentElement;
      const scrollTop = resetScroll ? 0 : container.scrollTop;
      options.innerHTML = visible.map(app => {
        const id = escapeHtml(app.app_id), name = escapeHtml(app.app_name || app.app_id);
        return `<label class="integration-app-picker-option"><span class="integration-app-picker-name"><strong title="${name}">${name}</strong><code title="${id}">${id}</code></span>
          <span class="integration-app-picker-meta"><span>${escapeHtml(app.space_name || app.space_id)}</span><code>${escapeHtml(app.space_id)}</code></span>
          <input type="radio" name="integrationAppChoice" value="${id}"${app.app_id === picker.selection ? ' checked' : ''}>
          <i data-lucide="check" aria-hidden="true" class="integration-app-picker-check"></i></label>`;
      }).join('');
      window.lucide?.createIcons({ root: options });
      container.scrollTop = scrollTop;
      el('moreIntegrationAppPickerBtn').hidden = matches.length <= picker.limit;
      el('confirmIntegrationAppPickerBtn').disabled = picker.state !== 'ready' || !picker.selection;
      const status = el('integrationAppPickerStatus');
      if (picker.state === 'loading') status.textContent = '正在加载应用…';
      if (picker.state === 'ready') {
        status.textContent = matches.length ? '' : (picker.apps.length ? '没有匹配的应用' : '暂无可配置的应用');
      }
    },
    close(owner) {
      const picker = this.current;
      if (!picker || (owner && picker.owner !== owner)) return;
      this.current = null;
      picker.directoryRequest?.abort();
      el('integrationAppPicker').close();
    },
    confirmAppSelection(event) {
      event.preventDefault();
      const picker = this.current;
      const app = picker?.apps.find(item => item.app_id === picker.selection);
      if (!app) return;
      this.close();
      picker.onSelect(app);
    },
  };
  window.DeepAgentAppPicker = picker;
  document.addEventListener('DOMContentLoaded', () => {
    el('integrationAppPickerForm')?.addEventListener('submit', event => picker.confirmAppSelection(event));
    el('cancelIntegrationAppPickerBtn')?.addEventListener('click', () => picker.close());
    el('retryIntegrationAppPickerBtn')?.addEventListener('click', () => {
      if (picker.current) void picker.loadAppChoices(picker.current);
    });
    el('integrationAppPickerInput')?.addEventListener('input', () => {
      const state = picker.current;
      if (!state) return;
      state.limit = pageSize;
      picker.renderAppChoices(state);
    });
    el('integrationAppPickerTenants')?.addEventListener('click', event => {
      const button = event.target.closest('[data-picker-tenant]');
      const state = picker.current;
      if (!state || !button) return;
      state.tenant = button.dataset.pickerTenant;
      state.limit = pageSize;
      picker.renderAppChoices(state);
    });
    el('integrationAppPickerOptions')?.addEventListener('change', event => {
      if (!picker.current || event.target.name !== 'integrationAppChoice') return;
      picker.current.selection = event.target.value;
      el('confirmIntegrationAppPickerBtn').disabled = false;
    });
    el('moreIntegrationAppPickerBtn')?.addEventListener('click', () => {
      const state = picker.current;
      if (!state) return;
      const count = state.limit;
      state.limit += pageSize;
      picker.renderAppChoices(state, false);
      el('integrationAppPickerOptions').children[count]?.querySelector('input')?.focus();
    });
    el('integrationAppPicker')?.addEventListener('cancel', event => {
      event.preventDefault();
      picker.close();
    });
    el('integrationAppPicker')?.addEventListener('close', event => {
      if (!event.currentTarget.open) picker.close();
    });
  });
})();
