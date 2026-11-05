// Minimal structural DOM; browser replay separately checks real layout and downloads.
export class Element {
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.nodeType = tag === "#comment" ? 8 : 1; this.childNodes = []; this.parentNode = null; this.attributes = {}; this.dataset = {}; this.listeners = new Map(); this._text = ""; }
  get children() { return this.childNodes.filter(child => child.nodeType === 1); }
  get className() { return this.attributes.class || ""; }
  set className(value) { this.attributes.class = value; }
  get classList() { const el = this; return { contains: name => el.className.split(/\s+/).includes(name), add(...names) { el.className = [...new Set([...el.className.split(/\s+/), ...names])].join(" ").trim(); }, remove(...names) { el.className = el.className.split(/\s+/).filter(name => !names.includes(name)).join(" "); }, toggle(name, value) { const next = value ?? !this.contains(name); next ? this.add(name) : this.remove(name); return next; } }; }
  setAttribute(key, value) { this.attributes[key] = String(value); if (key.startsWith("data-")) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  addEventListener(name, fn) { this.listeners.set(name, [...(this.listeners.get(name) || []), fn]); }
  appendChild(node) { node.remove(); node.parentNode = this; this.childNodes.push(node); return node; }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  insertBefore(node, sibling) { node.remove(); node.parentNode = this; this.childNodes.splice(sibling ? this.childNodes.indexOf(sibling) : this.childNodes.length, 0, node); }
  remove() { if (this.parentNode) this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1); this.parentNode = null; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this.childNodes = []; this._text = String(value); }
  set innerHTML(html) {
    this.childNodes = []; this._text = ""; this._html = html;
    const stack = [this];
    for (const match of String(html).matchAll(/<\/?([\w-]+)([^>]*)>|([^<]+)/g)) {
      if (match[3]) { stack.at(-1)._text += match[3]; continue; }
      if (match[0].startsWith("</")) { if (stack.length > 1) stack.pop(); continue; }
      const child = new Element(match[1]);
      for (const attr of match[2].matchAll(/([\w-]+)="([^"]*)"/g)) child.setAttribute(attr[1], attr[2]);
      stack.at(-1).appendChild(child); if (!["input", "img", "br"].includes(match[1])) stack.push(child);
    }
  }
  get innerHTML() { return this._html || ""; }
  replaceChildren(...nodes) { this.childNodes = []; this._text = ""; nodes.forEach(node => this.appendChild(node)); }
  click() { for (const handler of this.listeners.get("click") || []) handler({ target: this, preventDefault() {}, stopPropagation() {} }); }
  querySelectorAll(selector) {
    const matches = el => selector.split(",").some(part => {
      const simple = part.trim(); if (simple.startsWith(".")) return simple.slice(1).split(".").every(name => el.classList.contains(name));
      if (simple.startsWith("[")) { const [, name, value] = simple.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/) || []; return name && (el.getAttribute(name) !== null || name.startsWith("data-") && el.dataset[name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] != null) && (value == null || el.getAttribute(name) === value); }
      return el.tagName.toLowerCase() === simple;
    });
    const found = []; const visit = el => { for (const child of el.children) { if (matches(child)) found.push(child); visit(child); } }; visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
