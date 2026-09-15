// Resize neighboring columns together; no persistence or change to row data.
export const COLUMN_BOUNDS = [[240, 720], [80, 220], [64, 200], [90, 240], [56, 130], [120, 220]];
export function resizeColumns(widths, index, delta) {
  if (index < 0 || index >= widths.length - 1) return [...widths];
  const [lower, upper] = COLUMN_BOUNDS[index], [nextLower, nextUpper] = COLUMN_BOUNDS[index + 1];
  const min = Math.min(lower, widths[index]), max = Math.max(upper, widths[index]);
  const nextMin = Math.min(nextLower, widths[index + 1]), nextMax = Math.max(nextUpper, widths[index + 1]);
  const change = Math.max(Math.max(min - widths[index], widths[index + 1] - nextMax), Math.min(delta, Math.min(max - widths[index], widths[index + 1] - nextMin)));
  return widths.map((width, i) => width + (i === index ? change : i === index + 1 ? -change : 0));
}
export function bindDiagnosticColumns(table, saved, onChange) {
  if (!table) return () => {};
  const header = table.querySelector('.diagnostic-session-columns');
  const handles = [...header.querySelectorAll('[data-resize-column]')];
  let drag = null;
  const widths = () => [...header.children].map(cell => cell.getBoundingClientRect().width);
  const announce = values => handles.forEach((handle, i) => {
    handle.setAttribute('aria-valuenow', Math.round(values[i]));
    handle.setAttribute('aria-valuemin', Math.floor(Math.min(COLUMN_BOUNDS[i][0], values[i])));
    handle.setAttribute('aria-valuemax', Math.ceil(Math.max(COLUMN_BOUNDS[i][1], values[i])));
  });
  const apply = values => {
    table.style.setProperty('--diagnostic-columns', values.map(width => `${width}px`).join(' '));
    table.style.minWidth = `${values.reduce((a, b) => a + b, 0) + (values.length - 1) * 8 + 20}px`;
    announce(values);
  };
  if (saved?.length === header.children.length) apply(saved);
  else announce(widths());
  const move = event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const next = resizeColumns(drag.widths, drag.index, event.clientX - drag.x);
    apply(next); onChange(next);
  };
  const finish = () => { if (drag) { if (drag.handle.hasPointerCapture?.(drag.pointer)) drag.handle.releasePointerCapture(drag.pointer); drag = null; } };
  const down = event => {
    const handle = event.target.closest('[data-resize-column]');
    if (!handle || event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    drag = { handle, pointer: event.pointerId, x: event.clientX, widths: widths(), index: Number(handle.dataset.resizeColumn) };
    handle.setPointerCapture(event.pointerId);
  };
  const key = event => {
    const handle = event.target.closest('[data-resize-column]');
    if (!handle || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const next = resizeColumns(widths(), Number(handle.dataset.resizeColumn), event.key === 'ArrowLeft' ? -10 : 10);
    apply(next); onChange(next);
  };
  header.addEventListener('pointerdown', down); header.addEventListener('pointermove', move);
  header.addEventListener('pointerup', finish); header.addEventListener('pointercancel', finish); header.addEventListener('keydown', key);
  return () => {
    finish(); header.removeEventListener('pointerdown', down); header.removeEventListener('pointermove', move);
    header.removeEventListener('pointerup', finish); header.removeEventListener('pointercancel', finish); header.removeEventListener('keydown', key);
  };
}
