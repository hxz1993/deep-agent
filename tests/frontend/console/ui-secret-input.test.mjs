import test from 'node:test';
import assert from 'node:assert/strict';
import { initSecretInputs } from '../../../console/ui-secret-input.js';

function harness() {
  const form = { listeners: {}, addEventListener(t, fn) { this.listeners[t] = fn; } };
  const dialog = { listeners: {}, addEventListener(t, fn) { this.listeners[t] = fn; } };
  const node = () => ({ attrs: {}, style: {}, listeners: {}, children: [],
    setAttribute(k,v) { this.attrs[k]=v; }, getAttribute(k) { return this.attrs[k]; }, hasAttribute(k) { return k in this.attrs; },
    addEventListener(k,fn) { this.listeners[k]=fn; }, append(child) { this.children.push(child); },
  });
  const input = { ...node(), type:'password', value:'draft-only',
    closest(selector) { return selector==='form' ? form : selector==='dialog' ? dialog : selector==='.secret-input-control' ? this.wrapper : null; },
    before(wrapper) { this.wrapper=wrapper; },
  };
  return {input, form, dialog, document: {createElement:node}, root: {querySelectorAll:()=>[input]}};
}

test('Shared secret control keeps draft, toggles eyes, and masks on form reset/dialog close',()=>{
  const h=harness(); const previous={document:globalThis.document, CSS:globalThis.CSS, MutationObserver:globalThis.MutationObserver};
  globalThis.document=h.document; globalThis.CSS={supports:()=>true};
  globalThis.MutationObserver=class {observe(){} disconnect(){}};
  try {
    const dispose=initSecretInputs(h.root); const toggle=h.input.wrapper.children[1];
    assert.equal(h.input.type,'text'); assert.equal(h.input.autocomplete,'off');
    assert.equal(h.input.style.webkitTextSecurity,'disc');
    const closedEye=toggle.innerHTML;
    toggle.listeners.click({preventDefault(){}});
    assert.equal(h.input.style.webkitTextSecurity,'none'); assert.equal(toggle.getAttribute('aria-label'),'隐藏密钥');
    assert.notEqual(toggle.innerHTML,closedEye); assert.equal(h.input.value,'draft-only');
    h.form.listeners.reset(); assert.equal(h.input.style.webkitTextSecurity,'disc');
    toggle.listeners.click({preventDefault(){}}); h.dialog.listeners.close();
    assert.equal(toggle.getAttribute('aria-pressed'),'false'); assert.equal(toggle.innerHTML,closedEye);
    h.input.disabled=true; toggle.listeners.click({preventDefault(){}});
    assert.equal(h.input.style.webkitTextSecurity,'disc'); dispose();
  } finally { Object.assign(globalThis,previous); }
});
