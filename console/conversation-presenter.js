import { copyTextToClipboard } from "./message-actions.js?v=20260912-21";
import { parseServerTimestamp, serverTimestampMilliseconds } from "./time.js?v=20260912-21";
import { attachmentPreviewUrl } from "./attachment-preview.js?v=20260912-21";
import { renderWebImagePreview } from "./web-image.js?v=20260912-21";
import { isPlanningToolEvent, processDisclosureLabel, toolResultPresentation } from "./assistant-presentation.js?v=20260912-21";

// No controller imports or initialization: each host owns its DOM and capabilities.
export function createConversationPresenter({
  messages, readOnly = false, userLabel = "你", document = globalThis.document, window = globalThis.window,
  scrollToBottom = () => {}, showConsoleToast = () => {},
  mergeUiToolCall = (call) => call, isUiToolCall = () => false,
  maybeAttachHitlWidget = () => {}, renderHitlWidget = () => {}, resumeDeferred = () => {},
  onContextReference = () => {}, isContextReferenceSelected = () => false,
} = {}) {
  if (!messages) throw new TypeError("Conversation presentation requires its own message host");
  const lucideIcon = (name, className = "") => `<i data-lucide="${name}" class="${className}" aria-hidden="true"></i>`;
  const refreshLucideIcons = () => window.requestAnimationFrame(() => window.lucide?.createIcons());
function createMessageActions({
  getText = null,
  timestamp,
  copyLabel = "复制消息",
  copiedLabel = "消息已复制",
  toastMessage = "已复制消息",
}) {
  const actions = document.createElement("div");
  actions.className = "msg-actions";

  if (typeof getText === "function") {
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "msg-copy-btn";
    copyBtn.title = "复制";
    copyBtn.setAttribute("aria-label", copyLabel);
    copyBtn.innerHTML = lucideIcon("copy");
    copyBtn.addEventListener("click", async () => {
      const text = String(getText() || "");
      if (!text) return;
      copyBtn.disabled = true;
      try {
        await copyTextToClipboard(text);
        copyBtn.classList.add("copied");
        copyBtn.title = "已复制";
        copyBtn.setAttribute("aria-label", copiedLabel);
        copyBtn.innerHTML = lucideIcon("check");
        refreshLucideIcons();
        showConsoleToast(toastMessage);
        window.setTimeout(() => {
          if (!copyBtn.isConnected) return;
          copyBtn.classList.remove("copied");
          copyBtn.title = "复制";
          copyBtn.setAttribute("aria-label", copyLabel);
          copyBtn.innerHTML = lucideIcon("copy");
          copyBtn.disabled = false;
          refreshLucideIcons();
        }, 1500);
      } catch {
        copyBtn.disabled = false;
        showConsoleToast("复制失败，请重试", true);
      }
    });
    actions.appendChild(copyBtn);
  }

  const timeEl = document.createElement("time");
  timeEl.className = "msg-time";
  timeEl.dateTime = timestamp || "";
  timeEl.textContent = formatMessageTime(timestamp);
  actions.appendChild(timeEl);
  return actions;
}

function addMessage(role, text, timestamp, branchPoint, attachments, contextRefs) {
  const messageTimestamp = timestamp || (readOnly ? null : new Date().toISOString());
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;
  if (branchPoint != null) row.dataset.branchPoint = String(branchPoint);
  row.innerHTML = `
    <div class="avatar ${role}">${role === "user" ? escapeHtml(userLabel) : lucideIcon("bot")}</div>
    <div class="bubble-wrap"><div class="bubble"></div></div>`;
  const wrap = row.querySelector(".bubble-wrap");
  const bubble = wrap.querySelector(".bubble");
  if (attachments?.length) {
    renderMessageAttachments(bubble, attachments);
  }
  if (contextRefs?.length) {
    renderMessageContextRefs(bubble, contextRefs, branchPoint);
  }
  if (text) {
    const textEl = document.createElement("div");
    textEl.className = "bubble-text";
    textEl.textContent = text;
    bubble.appendChild(textEl);
  }
  if (role === "user") {
    wrap.appendChild(createMessageActions({
      getText: text ? () => text : null,
      timestamp: messageTimestamp,
      copyLabel: "复制用户消息",
      copiedLabel: "用户消息已复制",
      toastMessage: "已复制用户消息",
    }));
  }
  messages.appendChild(row);
  refreshLucideIcons();
  scrollToBottom();
  return row;
}

function renderMessageContextRefs(container, contextRefs, branchPoint) {
  const wrap = document.createElement("div");
  wrap.className = "msg-context-refs";
  for (const ref of contextRefs) {
    const chip = document.createElement("div");
    chip.className = "context-ref-chip";
    chip.title = ref.label || ref.type || "引用";
    const label = document.createElement("span");
    label.className = "context-ref-label";
    label.textContent = ref.label || ref.type || "引用";
    const info = document.createElement("button");
    info.type = "button";
    info.className = "context-ref-info";
    info.title = "查看上下文 JSON";
    info.setAttribute("aria-label", `查看 ${ref.label || ref.type || "上下文引用"} JSON`);
    info.dataset.contextRefId = [branchPoint ?? "live", ref.key || "", ref.type || ""].join(":");
    info.setAttribute(
      "aria-pressed",
      isContextReferenceSelected(ref, branchPoint) ? "true" : "false",
    );
    info.innerHTML = lucideIcon("info");
    info.addEventListener("click", (event) => {
      event.stopPropagation();
      onContextReference(ref, branchPoint);
    });
    chip.append(label, info);
    wrap.appendChild(chip);
  }
  container.appendChild(wrap);
}

function renderMessageAttachments(container, attachments) {
  const wrap = document.createElement("div");
  wrap.className = "msg-attachments";
  for (const att of attachments) {
    const previewUrl = attachmentPreviewUrl(att);
    const chip = document.createElement(previewUrl ? "button" : "div");
    chip.className = `attachment-chip${previewUrl ? " attachment-image-trigger" : ""}`;
    if (previewUrl) {
      chip.type = "button";
      chip.dataset.attachmentImageUrl = previewUrl;
      chip.dataset.attachmentImageName = att.name || "图片附件";
      chip.setAttribute("aria-label", `预览图片 ${att.name || "图片附件"}`);
    }
    chip.innerHTML = `${lucideIcon(previewUrl ? "image" : "paperclip")}<span class="attachment-name">${escapeHtml(att.name || att.id || "附件")}</span>`;
    wrap.appendChild(chip);
  }
  container.appendChild(wrap);
  refreshLucideIcons();
}

function processElapsedSecondsBetween(startedAt, completedAt) {
  const startedMs = serverTimestampMilliseconds(startedAt);
  const completedMs = serverTimestampMilliseconds(completedAt);
  if (!startedMs || !completedMs || completedMs < startedMs) return null;
  return Math.max(0, Math.round((completedMs - startedMs) / 1000));
}

function createAssistantMessage(timestamp, branchPoint, beforeNode = null, processTiming = {}) {
  const allowDeferred = !readOnly && processTiming.allowDeferred !== false;
  const autoAttachDeferred = allowDeferred && processTiming.autoAttachDeferred !== false;
  let isLive = !readOnly && !timestamp;
  const messageTimestamp = timestamp || (readOnly ? null : new Date().toISOString());
  const suppliedStartMs = serverTimestampMilliseconds(processTiming.startedAt);
  let processStartedAt = suppliedStartMs || Date.now();
  let processFinished = !isLive;
  let processElapsedSeconds = processElapsedSecondsBetween(
    processTiming.startedAt,
    processTiming.completedAt,
  ) ?? (readOnly ? null : 0);
  let processTimer = null;
  const row = document.createElement("div");
  row.className = "msg-row assistant assistant-answer";
  if (branchPoint != null) row.dataset.branchPoint = String(branchPoint);
  row.innerHTML = `<div class="bubble-wrap assistant-message"></div>`;
  const wrap = row.querySelector(".bubble-wrap");

  const processBlock = document.createElement("div");
  processBlock.className = "assistant-process hidden";
  processBlock.innerHTML = `
    <div class="assistant-process-header">
      <button type="button" class="assistant-process-summary" aria-expanded="false">
        <span class="chevron">${lucideIcon("chevron-right")}</span>
        <span class="assistant-process-icon">${lucideIcon("activity")}</span>
        <span class="assistant-process-title">处理过程</span>
      </button>
    </div>
    <div class="assistant-process-body"></div>`;
  wrap.appendChild(processBlock);
  const processSummary = processBlock.querySelector(".assistant-process-summary");
  const processTitle = processBlock.querySelector(".assistant-process-title");
  const processBody = processBlock.querySelector(".assistant-process-body");

  if (beforeNode?.parentNode === messages) messages.insertBefore(row, beforeNode);
  else messages.appendChild(row);
  refreshLucideIcons();
  scrollToBottom();

  let textBuffer = "";
  let finalContent = null;
  let finalActions = null;
  let thinkingBlock = null;
  let thinkingBuffer = "";
  let interruptedStatus = null;
  const toolBlocks = new Map(); // tool_call_id -> element

  function updateProcessTimer() {
    processElapsedSeconds = Math.max(0, Math.floor((Date.now() - processStartedAt) / 1000));
    updateProcessDisclosure();
  }

  function startProcess() {
    if (readOnly) return;
    isLive = true;
    processFinished = false;
    if (processTimer) window.clearInterval(processTimer);
    updateProcessTimer();
    processTimer = window.setInterval(updateProcessTimer, 1000);
  }

  function finishProcess() {
    if (!processFinished) {
      processElapsedSeconds = Math.max(0, Math.round((Date.now() - processStartedAt) / 1000));
    }
    processFinished = true;
    if (processTimer) {
      window.clearInterval(processTimer);
      processTimer = null;
    }
    processBlock.classList.remove("open", "running");
    processSummary.setAttribute("aria-expanded", "false");
    updateProcessDisclosure();
  }

  function updateProcessDisclosure() {
    const hasProcess = processBody.children.length > 0;
    const pending = Boolean(processBody.querySelector(".tool-block.hitl-pending"));
    processBlock.classList.toggle("hidden", !hasProcess);
    processBlock.classList.toggle("running", isLive && !processFinished && hasProcess);
    if (isLive && !processFinished && hasProcess && !processBlock.dataset.disclosureTouched) {
      processBlock.classList.add("open");
      processSummary.setAttribute("aria-expanded", "true");
    }
    processTitle.textContent = processDisclosureLabel({
      pending,
      running: isLive && !processFinished,
      elapsedSeconds: processElapsedSeconds,
    });
    processSummary.title = pending
      ? (readOnly ? "查看等待中的操作（只读）" : "展开并完成等待中的操作")
      : processBlock.classList.contains("open") ? "收起处理过程" : "展开处理过程";
  }

  processSummary.addEventListener("click", () => {
    processBlock.dataset.disclosureTouched = "1";
    const open = !processBlock.classList.contains("open");
    processBlock.classList.toggle("open", open);
    processSummary.setAttribute("aria-expanded", String(open));
    if (open) {
      processBody.querySelectorAll(".tool-block.hitl-pending").forEach((block) => {
        setToolBlockExpanded(block, true);
      });
    }
    updateProcessDisclosure();
  });
  if (isLive) startProcess();

  function ensureFinalContent() {
    if (!finalContent) {
      finalContent = document.createElement("div");
      finalContent.className = "assistant-final markdown streaming";
      wrap.appendChild(finalContent);
    }
    if (!finalActions) {
      finalActions = createMessageActions({
        getText: () => textBuffer,
        timestamp: messageTimestamp,
        copyLabel: "复制 Agent 回复",
        copiedLabel: "Agent 回复已复制",
        toastMessage: "已复制 Agent 回复",
      });
      wrap.appendChild(finalActions);
      refreshLucideIcons();
    }
    return finalContent;
  }

  function addProcessText(content) {
    const value = String(content || "");
    if (!value) return;
    const text = document.createElement("div");
    text.className = "assistant-process-text markdown";
    text.innerHTML = renderMarkdownBody(value);
    processBody.appendChild(text);
    updateProcessDisclosure();
  }

  function promoteFinalToProcess() {
    if (!textBuffer) return;
    addProcessText(textBuffer);
    finalContent?.remove();
    finalActions?.remove();
    finalContent = null;
    finalActions = null;
    textBuffer = "";
  }

  function ensureThinkingBlock() {
    if (!thinkingBlock) {
      thinkingBlock = document.createElement("div");
      thinkingBlock.className = "assistant-thinking";
      thinkingBlock.innerHTML = `
        <button type="button" class="assistant-thinking-head" aria-expanded="false">
          <span class="thinking-icon">${lucideIcon("brain")}</span>
          <span class="thinking-heading">
            <span class="thinking-title">思考中…</span>
            <span class="thinking-status"><span class="spinner" role="status" aria-label="思考中"></span></span>
          </span>
          <span class="thinking-chevron">${lucideIcon("chevron-right")}</span>
        </button>
        <div class="assistant-thinking-body">
          <pre class="thinking-content"></pre>
        </div>`;
      const block = thinkingBlock;
      const thinkingHead = block.querySelector(".assistant-thinking-head");
      thinkingHead.addEventListener("click", () => {
        const open = !block.classList.contains("open");
        block.classList.toggle("open", open);
        thinkingHead.setAttribute("aria-expanded", String(open));
        thinkingHead.title = open ? "收起思考过程" : "展开思考过程";
      });
      thinkingHead.title = "展开思考过程";
      processBody.appendChild(thinkingBlock);
      refreshLucideIcons();
      updateProcessDisclosure();
    }
    return thinkingBlock;
  }

  function renderThinkingContent() {
    const pre = ensureThinkingBlock().querySelector(".thinking-content");
    pre.textContent = thinkingBuffer;
  }

  function finalizeThinkingBlock() {
    if (!thinkingBlock) return;
    thinkingBlock.querySelector(".thinking-title").textContent = "思考过程";
    thinkingBlock.querySelector(".thinking-status").innerHTML = "";
    thinkingBlock = null;
    thinkingBuffer = "";
    updateProcessDisclosure();
  }

  function sealFinalContent() {
    if (!finalContent) return;
    finalContent.classList.remove("streaming");
    finalContent.innerHTML = renderMarkdownBody(textBuffer);
    void renderMarkdownDiagrams(finalContent);
  }

  const presenter = {
    row,
    resumeProcess() {
      startProcess();
    },
    appendThinking(delta) {
      promoteFinalToProcess();
      thinkingBuffer += delta;
      renderThinkingContent();
      updateProcessDisclosure();
      scrollToBottom();
    },
    finalizeThinking() {
      finalizeThinkingBlock();
    },
    addThinkingBlock(content) {
      if (thinkingBlock) finalizeThinkingBlock();
      thinkingBuffer = String(content || "");
      renderThinkingContent();
      finalizeThinkingBlock();
    },
    addProcessText,
    appendText(delta) {
      textBuffer += delta;
      ensureFinalContent().innerHTML = renderMarkdownBody(textBuffer);
    },
    setFinalText(content) {
      const output = typeof content === "string"
        ? content
        : content == null ? "" : pretty(content);
      if (!output) return;
      if (textBuffer && textBuffer !== output && !output.startsWith(textBuffer)) {
        promoteFinalToProcess();
      }
      textBuffer = output;
      ensureFinalContent().innerHTML = renderMarkdownBody(textBuffer);
    },
    finalizeText() {
      sealFinalContent();
      if (isLive) finishProcess();
    },
    complete(output) {
      finalizeThinkingBlock();
      if (output != null) this.setFinalText(output);
      sealFinalContent();
      finishProcess();
    },
    markInterrupted() {
      if (interruptedStatus) return;
      for (const block of toolBlocks.values()) {
        if (block.dataset.done) continue;
        block.dataset.done = "1";
        block.classList.remove("hitl-pending");
        block.querySelector(".hitl-widget")?.remove();
        const title = block.querySelector(".tool-name");
        if (title && block.dataset.defaultTitle) title.textContent = block.dataset.defaultTitle;
        const status = block.querySelector(".tool-status");
        if (status) {
          status.innerHTML = '<span class="tool-status-interrupted">已中断</span>';
        }
      }
      interruptedStatus = document.createElement("div");
      interruptedStatus.className = "message-run-status interrupted";
      interruptedStatus.innerHTML = `${lucideIcon("circle-stop")}<span>已停止</span>`;
      wrap.appendChild(interruptedStatus);
      row.classList.add("interrupted");
      finishProcess();
      refreshLucideIcons();
    },
    addToolCall(data) {
      finalizeThinkingBlock();
      promoteFinalToProcess();
      const merged = mergeUiToolCall(data);
      const key = merged.tool_call_id || `${merged.tool}-${toolBlocks.size}`;
      if (isPlanningToolEvent(merged)) return;
      const hasArgs = hasNonEmptyArgs(merged.args);
      const existing = toolBlocks.get(key);

      if (existing) {
        if (hasArgs) {
          const argsEl = existing.querySelector(".args");
          if (argsEl) argsEl.innerHTML = window.DeepAgentJson?.highlight(merged.args) || escapeHtml(pretty(merged.args));
        }
        if (merged.widget) existing.dataset.widget = merged.widget;
        if (merged.metadata) existing.dataset.metadata = JSON.stringify(merged.metadata);
        if (autoAttachDeferred) maybeAttachHitlWidget(existing, merged);
        updateProcessDisclosure();
        scrollToBottom();
        return;
      }

      const block = document.createElement("div");
      const isToolSearch =
        merged.source === "tool_search" || ["search_tools", "tool_search"].includes(merged.tool);
      const isSkillLoad = merged.tool === "load_capability";
      const defaultTitle = merged.display_name || merged.tool || "tool";
      block.className = `tool-block${isToolSearch ? " tool-search-block" : ""}${isSkillLoad ? " skill-load-block" : ""}`;
      block.dataset.toolCallId = key;
      block.dataset.defaultTitle = defaultTitle;
      if (merged.widget) block.dataset.widget = merged.widget;
      if (merged.metadata) block.dataset.metadata = JSON.stringify(merged.metadata);
      const renderedArgs = hasArgs ? merged.args : {};
      const argsHtml = window.DeepAgentJson?.renderBlock(renderedArgs, { className: "args", ariaLabel: "工具参数 JSON" })
        || `<pre class="args">${escapeHtml(pretty(renderedArgs))}</pre>`;
      block.innerHTML = `
        <button type="button" class="tool-head" aria-expanded="false">
          <span class="tool-icon">${lucideIcon(isToolSearch ? "search" : isSkillLoad ? "book-open-check" : "wrench")}</span>
          <span class="tool-name">${escapeHtml(defaultTitle)}</span>
          <span class="tool-status">${readOnly ? '<span class="tool-status-pending">尚无已保存结果</span>' : '<span class="spinner" role="status" aria-label="运行中"></span>'}</span>
          <span class="chevron">${lucideIcon("chevron-right")}</span>
        </button>
        <div class="tool-body">
          <div class="label">参数 args</div>
          ${argsHtml}
        </div>`;
      block.querySelector(".tool-head").addEventListener("click", () => {
        setToolBlockExpanded(block, !block.classList.contains("open"));
      });
      processBody.appendChild(block);
      refreshLucideIcons();
      toolBlocks.set(key, block);
      if (autoAttachDeferred) maybeAttachHitlWidget(block, merged);
      updateProcessDisclosure();
      scrollToBottom();
    },
    addToolResult(data) {
      promoteFinalToProcess();
      const key = data.tool_call_id;
      if (isPlanningToolEvent(data)) return;
      let block = key && toolBlocks.get(key);
      if (!block) {
        block = [...toolBlocks.values()].reverse().find((b) => !b.dataset.done);
      }
      if (!block) return;
      block.dataset.done = "1";
      block.classList.remove("hitl-pending");
      const title = block.querySelector(".tool-name");
      if (title && block.dataset.defaultTitle) title.textContent = block.dataset.defaultTitle;
      const resultStatus = toolResultPresentation(data);
      block.querySelector(".tool-status").innerHTML = `<span class="${resultStatus.className}">${resultStatus.label}</span>`;
      refreshLucideIcons();
      const body = block.querySelector(".tool-body");
      const existingWidget = body.querySelector(".hitl-widget");
      if (existingWidget) existingWidget.remove();
      body.querySelector("[data-tool-result]")?.remove();
      const res = document.createElement("div");
      res.dataset.toolResult = "1";
      res.innerHTML = `${renderToolResultArtifacts(data.artifacts)}<div class="label" style="margin-top:8px">结果 result</div>${renderToolResult(data.result)}${renderLocalFileDownload(data.result, data.artifacts)}`;
      body.appendChild(res);
      refreshLucideIcons();
      updateProcessDisclosure();
    },
    removeToolCall(data) {
      const key = data.tool_call_id;
      if (!key) return;
      if (isPlanningToolEvent(data)) return;
      const block = toolBlocks.get(key);
      if (!block) return;
      block.remove();
      toolBlocks.delete(key);
      updateProcessDisclosure();
    },
    attachHitlWidget(call) {
      if (!allowDeferred) {
        const block = toolBlocks.get(call.tool_call_id);
        if (block && !block.dataset.done) {
          block.classList.add("hitl-pending");
          block.querySelector(".tool-status").innerHTML = '<span class="tool-status-pending">等待操作 · 只读</span>';
          updateProcessDisclosure();
        }
        return;
      }
      const merged = mergeUiToolCall(call);
      const key = merged.tool_call_id;
      let block = key && toolBlocks.get(key);
      if (!block) {
        this.addToolCall(merged);
        block = toolBlocks.get(key);
      }
      if (!block || !isUiToolCall(merged)) return;
      renderHitlWidget(block, merged, resumeDeferred);
      updateProcessDisclosure();
    },
    dispose() {
      if (processTimer) window.clearInterval(processTimer);
      processTimer = null;
    },
  };
  return presenter;
}

function pretty(v) {
  if (v == null) return "null";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
}

function renderToolResult(result) {
  const imagePreview = renderWebImagePreview(result, { className: "tool-web-image-preview" });
  if (imagePreview) return imagePreview;
  if (typeof result === "string") {
    return `<div class="md-content tool-result-md markdown-body">${renderMarkdown(result)}</div>`;
  }
  return window.DeepAgentJson?.renderBlock(result, { ariaLabel: "工具结果 JSON" })
    || `<pre>${escapeHtml(pretty(result))}</pre>`;
}

function findLocalFileResult(value) {
  if (Array.isArray(value)) {
    for (const child of value) {
      const found = findLocalFileResult(child);
      if (found) return found;
    }
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const fileId = String(value.file_id || "");
  if (fileId.startsWith("local_")) {
    return { fileId, fileName: value.file_name || fileId };
  }
  for (const child of Object.values(value)) {
    const found = findLocalFileResult(child);
    if (found) return found;
  }
  return null;
}

function renderLocalFileDownload(result, artifacts = []) {
  if (artifacts.some((item) => item?.kind === "sandbox_output")) return "";
  const file = findLocalFileResult(result);
  if (!file) return "";
  const href = window.serviceUrl(
    `api/v1/local-files/${encodeURIComponent(file.fileId)}/download`,
  );
  return `<a class="local-file-download" href="${escapeHtml(href)}" download>${lucideIcon("download")}<span>${escapeHtml(file.fileName)}</span></a>`;
}

function renderToolResultArtifacts(artifacts) {
  if (!Array.isArray(artifacts) || artifacts.length === 0) return "";
  const overflow = artifacts.filter((item) => item?.kind !== "sandbox_output");
  const generated = artifacts.filter((item) => item?.kind === "sandbox_output");
  const totalBytes = overflow.reduce(
    (total, item) => total + (Number.isFinite(Number(item?.byte_size)) ? Number(item.byte_size) : 0),
    0,
  );
  const size = totalBytes > 0 ? ` · ${formatArtifactBytes(totalBytes)}` : "";
  const overflowHtml = overflow.length
    ? `<div class="tool-result-artifact">${lucideIcon("archive")}<span>完整结果已外置${escapeHtml(size)}，当前展示预览；Agent 可按需读取。</span></div>`
    : "";
  const generatedHtml = generated.length
    ? `<div class="generated-file-list"><div class="generated-file-heading">${lucideIcon("files")}<span>生成文件</span></div>${generated.map(renderGeneratedFileArtifact).join("")}</div>`
    : "";
  return `${overflowHtml}${generatedHtml}`;
}

function renderGeneratedFileArtifact(artifact) {
  const fileId = String(artifact?.file_id || artifact?.artifact_id || "");
  const fileName = String(artifact?.file_name || fileId || "未命名文件");
  const details = [artifact?.mime, formatArtifactBytes(artifact?.byte_size)].filter(Boolean).join(" · ");
  const download = fileId.startsWith("local_")
    ? `<a href="${escapeHtml(window.serviceUrl(`api/v1/local-files/${encodeURIComponent(fileId)}/download`))}" download title="下载文件">${lucideIcon("download")}</a>`
    : "";
  return `<div class="generated-file-item"><span class="generated-file-icon">${lucideIcon("file-output")}</span><span class="generated-file-copy"><strong>${escapeHtml(fileName)}</strong>${details ? `<small>${escapeHtml(details)}</small>` : ""}<code title="${escapeHtml(fileId)}">${escapeHtml(fileId)}</code></span><span class="generated-file-actions">${download}<button type="button" data-copy-file-id="${escapeHtml(fileId)}" title="复制 File ID">${lucideIcon("copy")}</button></span></div>`;
}

function formatArtifactBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${Math.round(bytes)} B`;
}

function formatMessageTime(iso) {
  const date = parseServerTimestamp(iso);
  if (!date) return "—";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(
    date
  );
}

function hasNonEmptyArgs(args) {
  if (args == null) return false;
  if (typeof args === "object" && !Array.isArray(args)) return Object.keys(args).length > 0;
  return true;
}

function setToolBlockExpanded(block, expanded) {
  block.classList.toggle("open", expanded);
  block.querySelector(".tool-head")?.setAttribute("aria-expanded", String(expanded));
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdown(src) {
  return window.DeepAgentMarkdown?.render(src) ?? `<pre class="md-fallback">${escapeHtml(src)}</pre>`;
}

function renderMarkdownBody(src) {
  return window.DeepAgentMarkdown?.renderBody(src) ?? `<div class="markdown-body">${renderMarkdown(src)}</div>`;
}

function renderMarkdownDiagrams(root) {
  return window.DeepAgentMarkdown?.renderDiagrams(root) ?? Promise.resolve();
}

  return { createMessageActions, addMessage, renderMessageContextRefs, renderMessageAttachments, processElapsedSecondsBetween, createAssistantMessage, pretty, renderToolResult, findLocalFileResult, renderLocalFileDownload, renderToolResultArtifacts, renderGeneratedFileArtifact, formatArtifactBytes, formatMessageTime, hasNonEmptyArgs, setToolBlockExpanded, escapeHtml, renderMarkdown, renderMarkdownBody, renderMarkdownDiagrams };
}
