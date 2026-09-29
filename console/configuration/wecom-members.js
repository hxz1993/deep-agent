/* Loaded only when a WeCom application detail is opened. Reads cached records only. */
import { parseServerTimestamp } from '../time.js';

// Own request lifetime independently of application editing and cache-reset mutations.
export function createMemberQuery({ applicationId, request, onChange, schedule = setTimeout, unschedule = clearTimeout }) {
  const state = { q: '', status: 'all', synced: 'all', sort: 'desc', limit: 10, offset: 0, items: [], total: 0, loading: false, error: '' };
  let revision = 0, controller, timer, disposed = false;
  function cancel() { revision++; controller?.abort(); unschedule(timer); }
  async function load() {
    if (disposed) return;
    cancel();
    const ticket = revision;
    controller = new AbortController();
    state.loading = true; state.error = ''; onChange(state);
    const params = new URLSearchParams();
    for (const key of ['q', 'status', 'synced', 'sort', 'limit', 'offset']) params.set(key, String(state[key]));
    try {
      const result = await request(`/api/v1/wecom-applications/${encodeURIComponent(applicationId)}/cache/members?${params}`, { signal: controller.signal });
      if (disposed || revision !== ticket) return;
      if (!Array.isArray(result.items) || !Number.isInteger(result.total) || result.total < 0) throw new Error('成员列表响应格式不正确');
      if (state.offset && state.offset >= result.total) {
        state.offset = Math.max(0, Math.ceil(result.total / state.limit) - 1) * state.limit;
        return load();
      }
      state.items = result.items; state.total = result.total; state.loading = false; onChange(state);
      return true;
    } catch (error) {
      if (disposed || revision !== ticket) return;
      state.loading = false; state.items = []; state.error = error.message || '成员列表读取失败'; onChange(state);
      return false;
    }
  }
  return {
    state, load,
    update(changes, { debounce = false } = {}) {
      if (disposed) return;
      cancel(); Object.assign(state, { offset: 0 }, changes);
      if (debounce) {
        state.loading = true; state.error = ''; onChange(state);
        timer = schedule(() => void load(), 250);
      } else return load();
    },
    dispose() { disposed = true; cancel(); },
  };
}

export function createWecomMemberTable({ host, applicationId, request, escapeHtml, formatDate }) {
  const esc = value => escapeHtml(String(value ?? '')).replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const icons = () => window.lucide?.createIcons();
  const time = value => {
    const date = parseServerTimestamp(value);
    if (!date) return '<td class="wecom-member-time">—</td>';
    const pad = number => String(number).padStart(2, '0');
    const short = `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    return `<td class="wecom-member-time" title="${esc(formatDate(value))}">${short}</td>`;
  };
  host.innerHTML = `<div class="wecom-member-toolbar">
    <label class="wecom-member-search">${icon('search')}<input type="search" data-member-search placeholder="搜索姓名、User ID 或 Open User ID" aria-label="搜索企微成员" autocomplete="off" maxlength="200"></label>
    <div class="wecom-member-filters"><label><select data-member-filter="status" aria-label="缓存状态"><option value="all">全部缓存状态</option><option value="valid">有效</option><option value="stale">待更新</option></select></label><label><select data-member-filter="synced" aria-label="最近同步时间"><option value="all">全部同步时间</option><option value="day">最近 24 小时</option><option value="week">最近 7 天</option><option value="older">7 天以前</option><option value="never">尚未同步</option></select></label></div>
  </div><div class="table-wrap wecom-member-table"><table class="data-table"><colgroup><col style="width:8%"><col style="width:13%"><col style="width:22%"><col style="width:8%"><col style="width:12%"><col style="width:12%"><col style="width:12%"><col style="width:13%"></colgroup><thead><tr><th>姓名</th><th>企微 User ID</th><th>Open User ID</th><th>缓存状态</th><th>首次缓存</th><th data-member-sort-header aria-sort="descending"><button type="button" data-member-sort>最近同步 ${icon('arrow-down')}</button></th><th>有效期至</th><th>最近错误</th></tr></thead><tbody data-member-rows></tbody></table></div>
  <nav class="wecom-member-pager" aria-label="成员缓存分页"><button type="button" class="btn" data-member-prev aria-label="上一页">上一页</button><span data-member-page role="status"></span><button type="button" class="btn" data-member-next aria-label="下一页">下一页</button></nav>`;
  const find = selector => host.querySelector(selector);
  function render(state) {
    host.setAttribute('aria-busy', String(state.loading));
    const body = find('[data-member-rows]');
    if (state.loading) body.innerHTML = '<tr><td colspan="8" class="empty-cell">正在读取成员缓存…</td></tr>';
    else if (state.error) body.innerHTML = `<tr><td colspan="8" class="empty-cell"><span class="error">${esc(state.error)}</span><button type="button" class="btn" data-member-retry>重试</button></td></tr>`;
    else body.innerHTML = state.items.map(member => {
      const error = [member.last_error_stage, member.last_error_code].filter(value => value != null && value !== '').join(' · ');
      return `<tr><td title="${esc(member.name || '')}">${esc(member.name || '未获取')}</td><td class="wecom-member-id" title="${esc(member.userid || '')}">${esc(member.userid || '—')}</td><td class="wecom-member-id" title="${esc(member.open_userid || '')}">${esc(member.open_userid || '—')}</td><td><span class="wecom-member-state ${member.status === 'valid' ? 'valid' : 'stale'}">${member.status === 'valid' ? '有效' : '待更新'}</span></td>${time(member.created_at)}${time(member.profile_synced_at)}${time(member.expires_at)}<td class="wecom-member-error" title="${esc(error)}">${esc(error || '—')}</td></tr>`;
    }).join('') || `<tr><td colspan="8" class="empty-cell">${state.q || state.status !== 'all' || state.synced !== 'all' ? '没有符合条件的成员' : '暂无成员缓存，收到成员消息并成功查询后会显示在这里。'}</td></tr>`;
    find('[data-member-page]').textContent = state.loading ? '正在读取…' : state.error ? '读取失败' : `第 ${Math.floor(state.offset / state.limit) + 1} / ${Math.max(1, Math.ceil(state.total / state.limit))} 页 · 共 ${state.total} 项`;
    find('[data-member-prev]').disabled = state.loading || !!state.error || state.offset === 0;
    find('[data-member-next]').disabled = state.loading || !!state.error || state.offset + state.limit >= state.total;
    find('[data-member-sort-header]').setAttribute('aria-sort', state.sort === 'desc' ? 'descending' : 'ascending');
    find('[data-member-sort]').innerHTML = `最近同步 ${icon(state.sort === 'desc' ? 'arrow-down' : 'arrow-up')}`;
    icons();
  }
  const query = createMemberQuery({ applicationId, request, onChange: render });
  find('[data-member-search]').addEventListener('input', event => { query.update({ q: event.target.value.trim() }, { debounce: true }); });
  host.querySelectorAll('[data-member-filter]').forEach(select => select.addEventListener('change', () => { query.update({ [select.dataset.memberFilter]: select.value }); }));
  host.addEventListener('click', event => {
    const button = event.target.closest('button'); if (!button) return;
    if (button.hasAttribute('data-member-retry')) void query.load();
    if (button.hasAttribute('data-member-sort')) void query.update({ sort: query.state.sort === 'desc' ? 'asc' : 'desc' });
    if (button.hasAttribute('data-member-prev')) void query.update({ offset: Math.max(0, query.state.offset - query.state.limit) });
    if (button.hasAttribute('data-member-next')) void query.update({ offset: query.state.offset + query.state.limit });
  });
  return { load: query.load, dispose: query.dispose };
}
