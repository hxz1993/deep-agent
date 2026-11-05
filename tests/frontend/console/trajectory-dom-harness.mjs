// Small DOM fixture for controller behavior; real DOM/rendering is covered by browser replay.
export function trajectoryDomHarness({ immediate = false } = {}) {
  const elements = new Map();
  const frames = new Map();
  let frameId = 0;
  const document = { activeElement: null };
  class Element {
    constructor(name = "div") {
      this.name = name; this.dataset = {}; this.children = []; this.parentNode = null;
      this.listeners = new Map(); this.attributes = new Map(); this.value = "";
      this.scrollTop = 0; this.writes = 0; this._html = ""; this.className = "";
      this.classList = {
        contains: value => this.className.split(/\s+/).includes(value),
        toggle: (value, force) => {
          const classes = new Set(this.className.split(/\s+/).filter(Boolean));
          const on = force === undefined ? !classes.has(value) : Boolean(force);
          if (on) classes.add(value); else classes.delete(value);
          this.className = [...classes].join(" ");
          return on;
        },
      };
    }
    get innerHTML() { return this.children.length ? this.children.map(child => child.outerHTML).join("") : this._html; }
    get outerHTML() {
      const data = Object.entries(this.dataset).map(([key, value]) => ` data-${key.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}="${value}"`).join("");
      return `<${this.name} class="${this.className}"${data}>${this.innerHTML}</${this.name}>`;
    }
    set innerHTML(value) {
      this._html = value; this.writes += 1;
      for (const child of this.children) child.parentNode = null;
      this.children = [];
      if (value.includes('class="trajectory-empty"')) {
        const empty = new Element("empty"); empty.className = "trajectory-empty";
        this.insertBefore(empty, null);
      }
    }
    addEventListener(type, handler) { this.listeners.set(type, handler); }
    removeEventListener(type, handler) { if (this.listeners.get(type) === handler) this.listeners.delete(type); }
    setAttribute(name, value) { this.attributes.set(name, value); }
    getAttribute(name) { return this.attributes.get(name); }
    insertBefore(node, before) {
      if (node === before) return;
      node.remove();
      this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, node);
      node.parentNode = this;
    }
    remove() {
      if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
      this.parentNode = null;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
      if (selector === ".trajectory-detail-head, .trajectory-detail-empty" && /class="trajectory-detail-(?:head|empty)"/.test(this._html)) return [new Element("trajectory-detail")];
      if (selector === ".trajectory-empty") return this.children.filter(node => node.classList.contains("trajectory-empty"));
      if (selector === "[data-trajectory-id]") return this.children.filter(node => node.dataset.trajectoryId);
      return [];
    }
    focus() { document.activeElement = this; }
    getBoundingClientRect() { return { top: 0, bottom: 100, left: 0, right: 100, height: 100, width: 100 }; }
    scrollTo({ top }) { this.scrollTop = top; }
  }
  const element = (name) => {
    if (!elements.has(name)) elements.set(name, new Element(name));
    return elements.get(name);
  };
  Object.assign(document, {
    createElement: name => new Element(name),
    getElementById: element,
    querySelector: element,
    querySelectorAll: () => [],
  });
  const window = {
    requestAnimationFrame(fn) {
      const id = ++frameId;
      if (immediate) fn(); else frames.set(id, fn);
      return id;
    },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  const flush = () => {
    const pending = [...frames.values()]; frames.clear();
    for (const callback of pending) callback();
  };
  return { document, window, elements, element, frames, flush };
}
