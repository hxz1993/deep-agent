// Keep the list usable while bounding the inspector on wide displays.
export function channelWidthBounds(hostWidth) {
  const max = Math.max(280, Math.min(720, hostWidth - 460));
  return { min: Math.min(320, max), max };
}

export function bindChannelResizer(host, initialWidth, onChange) {
  const handle = host?.querySelector(".channel-resizer");
  if (!handle) return () => {};
  let width = initialWidth, drag = null;
  function apply(value, remember = false) {
    const { min, max } = channelWidthBounds(host.getBoundingClientRect().width);
    width = Math.round(Math.max(min, Math.min(max, value)));
    host.style.setProperty("--channel-inspector-width", `${width}px`);
    handle.setAttribute("aria-valuemin", min);
    handle.setAttribute("aria-valuemax", max);
    handle.setAttribute("aria-valuenow", width);
    if (remember) onChange(width);
  }
  const down = event => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag = { x: event.clientX, width, id: event.pointerId };
    handle.setPointerCapture(event.pointerId);
  };
  const move = event => { if (drag) apply(drag.width + drag.x - event.clientX, true); };
  const end = () => {
    if (drag && handle.hasPointerCapture(drag.id)) handle.releasePointerCapture(drag.id);
    drag = null;
  };
  const key = event => {
    const { min, max } = channelWidthBounds(host.getBoundingClientRect().width);
    const next = { ArrowLeft: width + 20, ArrowRight: width - 20, Home: min, End: max }[event.key];
    if (next == null) return;
    event.preventDefault(); apply(next, true);
  };
  const events = { pointerdown: down, pointermove: move, pointerup: end, pointercancel: end, lostpointercapture: () => { drag = null; }, keydown: key };
  for (const [name, listener] of Object.entries(events)) handle.addEventListener(name, listener);
  const observer = new ResizeObserver(() => apply(width));
  observer.observe(host); apply(width);
  return () => {
    end(); observer.disconnect();
    for (const [name, listener] of Object.entries(events)) handle.removeEventListener(name, listener);
  };
}
