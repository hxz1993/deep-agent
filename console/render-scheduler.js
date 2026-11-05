/** Small, host-owned render queues. They never buffer protocol facts or events. */
export function createRenderBatch(render, { delay = 75, schedule = setTimeout, cancel = clearTimeout } = {}) {
  let timer = null;
  let disposed = false;
  function flush() {
    if (disposed) return;
    if (timer !== null) { cancel(timer); timer = null; }
    render();
  }
  return {
    request() {
      if (disposed || timer !== null) return;
      timer = schedule(() => { timer = null; if (!disposed) render(); }, delay);
    },
    flush,
    dispose() { disposed = true; if (timer !== null) cancel(timer); timer = null; },
  };
}

/** Lucide 0.468 createIcons ignores a root option; use its public SVG factory. */
export function renderIconsWithin(root, lucide) {
  if (!root?.querySelectorAll || !lucide?.createElement || !lucide?.icons) return;
  for (const element of root.querySelectorAll('i[data-lucide]')) {
    const name = element.getAttribute('data-lucide') || '';
    const key = name.replace(/(\w)(\w*)(_|-|\s*)/g, (_match, first, rest) => first.toUpperCase() + rest.toLowerCase());
    const definition = lucide.icons[key];
    if (!definition) continue;
    const attributes = Object.fromEntries(Array.from(element.attributes, (attribute) => [attribute.name, attribute.value]));
    attributes.class = ['lucide', `lucide-${name}`, attributes.class || ''].filter(Boolean).join(' ');
    const svg = lucide.createElement([definition[0], { ...definition[1], ...attributes }, definition[2]]);
    element.parentNode?.replaceChild(svg, element);
  }
}
