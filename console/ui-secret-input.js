/** Shared secret control. Never fetches stored secrets; reveals only the current input value. */
export function initSecretInputs(root = document) {
  const eye = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const eyeOff = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A11 11 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3 3.8M6.2 6.2A20 20 0 0 0 2 12s3.5 7 10 7a12 12 0 0 0 5.8-1.6"/></svg>';
  const visualMask = CSS.supports('-webkit-text-security', 'disc');
  const enhance = () => root.querySelectorAll('input[type="password"]').forEach(input => {
    if (input.closest('.secret-input-control')) return;
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('autocapitalize', 'off');
    input.setAttribute('data-1p-ignore', 'true');
    input.setAttribute('data-lpignore', 'true');
    const label = input.closest('label');
    if (label && !input.hasAttribute('aria-label') && !input.hasAttribute('aria-labelledby')) {
      input.setAttribute('aria-label', label.textContent.trim());
    }
    const wrapper = document.createElement('span');
    wrapper.className = 'secret-input-control';
    input.before(wrapper);
    wrapper.append(input);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secret-input-toggle';
    button.innerHTML = eye;
    const mask = (visible) => {
      input.type = visualMask || visible ? 'text' : 'password';
      input.style.webkitTextSecurity = visualMask && !visible ? 'disc' : 'none';
      button.innerHTML = visible ? eye : eyeOff;
      button.setAttribute('aria-label', visible ? '隐藏密钥' : '显示密钥');
      button.setAttribute('aria-pressed', String(visible));
      button.title = visible ? '隐藏密钥' : '显示密钥';
    };
    mask(false);
    button.addEventListener('click', event => {
      event.preventDefault();
      if (!input.disabled) mask(button.getAttribute('aria-pressed') !== 'true');
    });
    input.closest('form')?.addEventListener('reset', () => mask(false));
    const dialog = input.closest('dialog');
    dialog?.addEventListener('close', () => mask(false));
    wrapper.append(button);
  });
  enhance();
  const observer = new MutationObserver(enhance);
  observer.observe(root.body || root, { childList: true, subtree: true });
  return () => observer.disconnect();
}
