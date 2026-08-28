export async function copyTextToClipboard(text, clipboard = globalThis.navigator?.clipboard) {
  if (!clipboard?.writeText) {
    throw new Error("Clipboard API is unavailable");
  }
  await clipboard.writeText(String(text ?? ""));
}
