function mergeSystemToolDomains(domains) {
  // Display groups only: backend domains and each tool's availability stay intact.
  const source = domains || [];
  const groups = [
    {
      code: "runtime:basic", label: "基础运行",
      codes: ["runtime:environment", "runtime:planning", "runtime:conversation-history"],
      description: "当前时间、任务计划与会话历史；具体可用条件以各工具说明为准。",
    },
    {
      code: "runtime:file", label: "文件与结果",
      codes: ["runtime:file", "runtime:tool-result"],
      description: "读取、查看和发布文件，按需回读完整工具结果；各工具沿用原有授权与注入条件。",
    },
    { codes: ["runtime:skill"] },
    { code: "runtime:mcp", label: "MCP", codes: ["runtime:mcp-tool-search", "runtime:mcp-resource"] },
    { codes: ["runtime:remote-agent"] },
    { codes: ["runtime:channel"] },
  ];
  const knownCodes = new Set(groups.flatMap((group) => group.codes));
  const merged = groups.flatMap((group) => {
    const members = group.codes.flatMap((code) => source.filter((domain) => domain.code === code));
    if (!members.length) return [];
    if (!group.code) return members;
    return [{
      ...members[0],
      code: group.code,
      label: group.label,
      description: group.description || members
        .map((domain) => `${domain.label || domain.code}：${domain.description || "暂无补充说明"}`)
        .join("；"),
      tools: members.flatMap((domain) => domain.tools || []),
    }];
  });
  return merged.concat(source.filter((domain) => !knownCodes.has(domain.code)));
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
