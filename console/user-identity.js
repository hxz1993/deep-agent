// Shared platform account presentation; IDs remain the source of identity.
const e = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
export function renderUserIdentity(profile = {}, {userId = '', copyable = true, compact = false, userIcon = false} = {}) {
  const id = String(userId || profile.user_id || profile.id || '');
  const username = String(('username' in profile ? profile.username : profile.display_name !== id ? profile.display_name : '') || '');
  if (compact) return `<span class="da-user-compact" title="${e(id)}">${e(username || id || '未记录')}</span>`;
  const missing = !username;
  const name = username || (profile.resolution === 'unavailable' ? '查询失败' : '未查到');
  const description = username || (profile.resolution === 'unavailable' ? '用户资料暂不可用，请稍后重试' : '未查到用户资料');
  const contact = missing ? id || '未记录 User ID' : profile.email || '未设置邮箱';
  return `<span class="da-user-identity" data-user-id="${e(id)}"><span class="da-user-top">
    ${userIcon ? `<span class="user-popover-icon da-user-symbol">${icon('circle-user-round')}</span>` : ''}
    <span class="da-user-text"><span class="da-user-name${missing ? ' da-user-secondary' : ''}" title="${e(description)}">${e(name)}</span><span class="da-user-contact${missing ? ' is-id' : ''}" title="${e(contact)}">${e(contact)}</span></span>
    ${copyable && id ? `<button type="button" class="da-user-action" data-user-copy="${e(id)}" aria-label="复制 User ID" title="复制 User ID"><span class="da-user-copy-icon">${icon('copy')}</span><span class="da-user-copied-icon">${icon('check')}</span></button>` : ''}
    </span></span>`;
}
export function initUserIdentityControls(doc = document) {
  const timers = new Map();
  const pending = new WeakSet();
  let active = true;
  const reset = button => {
    button.classList.remove('is-copied');
    button.title = '复制 User ID';
    button.setAttribute('aria-label', button.title);
  };
  const click = async event => {
    const button = event.target.closest?.('[data-user-copy]');
    if (!button) return;
    event.preventDefault(); event.stopPropagation();
    if (pending.has(button)) return;
    pending.add(button);
    button.disabled = true;
    clearTimeout(timers.get(button));
    timers.delete(button);
    reset(button);
    try {
      await navigator.clipboard.writeText(button.dataset.userCopy);
      if (!active) return;
      clearTimeout(timers.get(button));
      button.classList.add('is-copied');
      button.title = 'User ID 已复制';
      button.setAttribute('aria-label', button.title);
      timers.set(button, setTimeout(() => {
        reset(button);
        timers.delete(button);
      }, 1600));
    } catch { if (active) globalThis.DeepAgentConsole?.toast?.('复制失败，请重试', true); }
    finally { pending.delete(button); button.disabled = false; }
  };
  doc.addEventListener('click', click, true);
  return () => { active = false; doc.removeEventListener('click', click, true); for (const timer of timers.values()) clearTimeout(timer); timers.clear(); };
}
