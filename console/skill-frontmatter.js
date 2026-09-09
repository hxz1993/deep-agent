function unquoteScalar(value) {
  const text = String(value || "").trim();
  if (text.length < 2) return text;
  if (text.startsWith('"') && text.endsWith('"')) {
    try {
      return JSON.parse(text);
    } catch {
      return text.slice(1, -1);
    }
  }
  if (text.startsWith("'") && text.endsWith("'")) {
    return text.slice(1, -1).replace(/''/g, "'");
  }
  return text;
}

/**
 * Reads the top-level scalar fields from a Markdown YAML frontmatter block.
 * It deliberately leaves nested YAML untouched: the console only needs the
 * human-readable Skill manifest fields, not a second YAML implementation.
 */
export function parseSkillFrontmatter(content) {
  const source = String(content || "").replace(/^\uFEFF/, "");
  const lines = source.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") {
    return { hasFrontmatter: false, entries: [], body: source };
  }

  const closingIndex = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (closingIndex < 0) {
    return { hasFrontmatter: false, entries: [], body: source };
  }

  const entries = [];
  const metadataLines = lines.slice(1, closingIndex);
  for (let index = 0; index < metadataLines.length; index += 1) {
    const match = metadataLines[index].match(/^([A-Za-z0-9_.-]+):(?:\s*(.*))?$/);
    if (!match) continue;
    const [, key, rawValue = ""] = match;
    let value = rawValue.trim();
    if (value === "|" || value === ">") {
      const blockStyle = value;
      const blockLines = [];
      while (index + 1 < metadataLines.length && /^\s+/.test(metadataLines[index + 1])) {
        index += 1;
        blockLines.push(metadataLines[index].replace(/^\s+/, ""));
      }
      value = blockStyle === ">" ? blockLines.join(" ") : blockLines.join("\n");
    } else {
      value = unquoteScalar(value);
    }
    if (value) entries.push({ key, value });
  }

  return {
    hasFrontmatter: true,
    entries,
    body: lines.slice(closingIndex + 1).join("\n").replace(/^\s*\n/, ""),
  };
}
