import { readFile } from "node:fs/promises";

// Keep existing presentation contract assertions attached to the actual shared
// implementation after extracting it from the workspace execution controllers.
export async function readPresentationSource(name) {
  const dependencies = name === "chat.js" ? ["conversation-presenter.js"] : name === "workspace.js" ? ["session-inspector.js"] : [];
  return (await Promise.all([...dependencies, name].map((file) => readFile(new URL(`../../../console/${file}`, import.meta.url), "utf8")))).join("\n");
}
