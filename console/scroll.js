/** Center a target in its owned pane without scrolling the surrounding shell. */
export function centerInScrollContainer(container, target) {
  if (!container || !target) return;
  const viewportTop = container.getBoundingClientRect().top + container.clientTop;
  const rect = target.getBoundingClientRect();
  const top = container.scrollTop + rect.top - viewportTop
    - Math.max(0, (container.clientHeight - rect.height) / 2);
  container.scrollTop = Math.max(0, Math.min(top, container.scrollHeight - container.clientHeight));
}
