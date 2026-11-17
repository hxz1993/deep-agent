export function resizeComposerInput(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
}

export function bindComposerInput(input, send) {
  let composing = false;
  input.addEventListener("compositionstart", () => { composing = true; });
  input.addEventListener("compositionend", () => { composing = false; });
  input.addEventListener("keydown", (event) => {
    // Safari can end composition before keydown and still report IME key code 229.
    if (composing || event.isComposing || event.keyCode === 229 || event.key === "Process") return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  input.addEventListener("input", () => resizeComposerInput(input));
}

export function bindComposerAttachments(input, dropZone, overlay, upload) {
  let dragDepth = 0;
  const setDragging = (active) => {
    dropZone.classList.toggle("dragging", active);
    overlay.setAttribute("aria-hidden", String(!active));
  };
  const hasFiles = (event) => event.dataTransfer?.types?.includes("Files");
  input.addEventListener("paste", (event) => {
    const files = Array.from(event.clipboardData?.files || []);
    if (!files.length || input.disabled) return;
    event.preventDefault();
    void upload(files);
  });
  dropZone.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    setDragging(!input.disabled);
  });
  dropZone.addEventListener("dragover", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = input.disabled ? "none" : "copy";
  });
  dropZone.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) setDragging(false);
  });
  dropZone.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    setDragging(false);
    if (!input.disabled) void upload(Array.from(event.dataTransfer?.files || []));
  });
}
