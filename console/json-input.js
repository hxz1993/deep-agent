// Shared source highlighting for editable JSON. Keep the textarea as the only input.
const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

export function highlightJsonEditorSource(value) {
  const source = String(value ?? "");
  const tokens = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}[\],:])/g;
  let cursor = 0;
  let html = "";
  for (const match of source.matchAll(tokens)) {
    html += escapeHtml(source.slice(cursor, match.index));
    const token = match[0];
    if (match[1]) {
      const colon = token.lastIndexOf(":");
      const key = token.slice(0, colon).trimEnd();
      html += `<span class="json-key">${escapeHtml(key)}</span>${escapeHtml(token.slice(key.length, colon))}:`;
    } else {
      const kind = match[2] ? "string" : match[3] ? "boolean" : match[4] ? "null" : match[5] ? "number" : null;
      html += kind ? `<span class="json-${kind}">${escapeHtml(token)}</span>` : escapeHtml(token);
    }
    cursor = match.index + token.length;
  }
  return `${html}${escapeHtml(source.slice(cursor))}\n`;
}

export function syncJsonInput(input, highlight) {
  if (!input || !highlight) return;
  highlight.innerHTML = highlightJsonEditorSource(input.value);
  highlight.parentElement.scrollTop = input.scrollTop;
  highlight.parentElement.scrollLeft = input.scrollLeft;
}
