function mergeSystemToolDomains(domains) {
  const mcpCodes = new Set(["runtime:mcp-tool-search", "runtime:mcp-resource"]);
  const mcpDomains = (domains || []).filter((domain) => mcpCodes.has(domain.code));
  if (!mcpDomains.length) return domains || [];
  const firstMcpIndex = (domains || []).findIndex((domain) => mcpCodes.has(domain.code));
  const merged = {
    code: "runtime:mcp",
    label: "MCP",
    description: mcpDomains
      .map((domain) => `${domain.label || domain.code}：${domain.description || "暂无补充说明"}`)
      .join("；"),
    tools: mcpDomains.flatMap((domain) => domain.tools || []),
  };
  return (domains || []).reduce((result, domain, index) => {
    if (index === firstMcpIndex) result.push(merged);
    if (!mcpCodes.has(domain.code)) result.push(domain);
    return result;
  }, []);
}

function renderSystemTools(domains) {
  window.__BuiltinToolsUI.updateToolCatalogGroup(true, mergeSystemToolDomains(domains));
}

const systemToolsAdmin = {
  async refresh() {
    if (!document.getElementById("builtinToolCatalog")) return;
    window.__BuiltinToolsUI.renderToolCatalog();
    try {
      renderSystemTools(await apiFetch("/api/v1/system-tools"));
    } catch (error) {
      window.__BuiltinToolsUI.updateToolCatalogGroup(true, [], error instanceof Error ? error.message : String(error));
    }
  },
};

window.systemToolsAdmin = systemToolsAdmin;
window.__SystemToolsUI = { mergeSystemToolDomains, renderSystemTools };
