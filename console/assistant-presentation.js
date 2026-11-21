export function formatProcessElapsed(elapsedSeconds) {
  if (elapsedSeconds === null || elapsedSeconds === undefined || elapsedSeconds === "") return "";
  const value = Number(elapsedSeconds);
  if (!Number.isFinite(value) || value < 0) return "";
  const totalSeconds = Math.max(0, Math.round(value));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}分${seconds}秒` : `${seconds}秒`;
}

export function processDisclosureLabel({ pending = false, running = false, elapsedSeconds = null } = {}) {
  if (pending) return "等待操作";
  const elapsed = formatProcessElapsed(elapsedSeconds);
  if (running) return elapsed ? `处理中 · ${elapsed}` : "处理中";
  return elapsed ? `已处理 · 用时 ${elapsed}` : "已处理";
}

export const ASSISTANT_PRESENTATION_ACTIONS = Object.freeze({
  THINKING: "thinking",
  FINALIZE_THINKING: "finalize_thinking",
  TEXT: "text",
  PROCESS_TEXT: "process_text",
  FINALIZE_TEXT: "finalize_text",
  TOOL_CALL: "tool_call",
  TOOL_RESULT: "tool_result",
  TOOL_REMOVE: "tool_remove",
  DEFERRED: "deferred",
  COMPLETE: "complete",
  STRUCTURED_OUTPUT: "structured_output",
  OUTPUT_FILES: "output_files",
  INTERRUPT: "interrupt",
});

export const PLANNING_TOOL_NAMES = new Set(["write_plan"]);
const REMOTE_AGENT_TOOL_NAMES = new Set(["delegate_task", "list_tasks", "answer_task", "cancel_task", "message_task", "remote_task_result"]);

export function isRemoteAgentToolEvent(event = {}) {
  return Boolean(event.metadata?.remote_agent)
    || (REMOTE_AGENT_TOOL_NAMES.has(event.tool) && !["ui", "manual", "mcp"].includes(event.source));
}

export function isPlanningToolEvent(event = {}) {
  return PLANNING_TOOL_NAMES.has(event.tool) && !["ui", "manual", "mcp"].includes(event.source);
}

export function toolResultPresentation(result = {}) {
  if (result.outcome === "skipped") {
    return { status: "interrupted", label: "已跳过", className: "tool-status-complete" };
  }
  const values = [result.outcome, result.status];
  if (values.some((value) => ['interrupted', 'cancelled', 'canceled'].includes(value))) {
    return { status: 'interrupted', label: '已中断', className: 'tool-status-interrupted' };
  }
  if (values.some((value) => ['failed', 'error'].includes(value))) {
    return { status: 'failed', label: '失败', className: 'tool-status-error' };
  }
  return { status: 'completed', label: '完成', className: 'tool-status-complete' };
}

function toolCallFromConversationItem(item = {}) {
  return {
    tool: item.tool_name,
    display_name: item.display_name,
    args: item.input,
    tool_call_id: item.call_id,
    source: item.mode === "tool_search" ? "tool_search" : item.mode === "deferred" ? "ui" : null,
    widget: item.widget,
    metadata: item.metadata,
  };
}

function structuredAction(name, output, outputId, publicationProtocol) {
  return {
    type: ASSISTANT_PRESENTATION_ACTIONS.STRUCTURED_OUTPUT, name, output, outputId,
    ...(publicationProtocol ? { publicationProtocol } : {}),
  };
}

export function assistantActionsFromHistoryItems(items = []) {
  const actions = [];
  let interrupted = false;

  for (const item of items) {
    if (item?.kind === "assistant_message") {
      if (item.thinking) {
        actions.push({
          type: ASSISTANT_PRESENTATION_ACTIONS.THINKING,
          mode: "replace",
          content: item.thinking,
        });
      }
      if (item.text) {
        actions.push({
          type: item.phase === "final"
            ? ASSISTANT_PRESENTATION_ACTIONS.TEXT
            : ASSISTANT_PRESENTATION_ACTIONS.PROCESS_TEXT,
          mode: "replace",
          content: item.text,
        });
      }
      interrupted ||= item.status === "interrupted";
      continue;
    }

    if (item?.kind === "structured_output" && item.status === "completed") {
      actions.push(structuredAction(item.name, item.output, item.item_id, item.publication_protocol));
      continue;
    }
    if (item?.kind !== "tool" || item.status === "removed") continue;
    const call = toolCallFromConversationItem(item);
    actions.push({ type: ASSISTANT_PRESENTATION_ACTIONS.TOOL_CALL, call });
    if (!["pending", "awaiting_input"].includes(item.status)) {
      actions.push({
        type: ASSISTANT_PRESENTATION_ACTIONS.TOOL_RESULT,
        result: {
          tool: item.tool_name,
          display_name: item.display_name,
          result: item.output,
          metadata: item.metadata,
          tool_call_id: item.call_id,
          source: call.source,
          artifacts: item.artifacts,
          outcome: item.outcome,
          status: item.status,
        },
      });
    } else if (item.status === "awaiting_input") {
      actions.push({ type: ASSISTANT_PRESENTATION_ACTIONS.DEFERRED, call });
    }
    interrupted ||= item.status === "interrupted" && item.outcome !== "skipped";
  }

  actions.push(
    { type: ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_THINKING },
    { type: ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_TEXT },
  );
  const delivery = items.findLast((item) => item.status === "completed" && item.output_files?.length);
  if (delivery) actions.push({ type: ASSISTANT_PRESENTATION_ACTIONS.OUTPUT_FILES, files: delivery.output_files });
  if (interrupted) actions.push({ type: ASSISTANT_PRESENTATION_ACTIONS.INTERRUPT });
  return actions;
}

export function assistantActionsFromSse(type, data = {}) {
  switch (type) {
    case "thinking":
      return [{
        type: ASSISTANT_PRESENTATION_ACTIONS.THINKING,
        mode: "append",
        content: data.delta || "",
      }];
    case "text":
      return [
        { type: ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_THINKING },
        {
          type: ASSISTANT_PRESENTATION_ACTIONS.TEXT,
          mode: "append",
          content: data.delta || "",
        },
      ];
    case "tool_call":
      return [{ type: ASSISTANT_PRESENTATION_ACTIONS.TOOL_CALL, call: data }];
    case "tool_result":
      return [{ type: ASSISTANT_PRESENTATION_ACTIONS.TOOL_RESULT, result: data }];
    case "tool_call_removed":
      return [{ type: ASSISTANT_PRESENTATION_ACTIONS.TOOL_REMOVE, call: data }];
    case "output":
      return data.accepted === true && data.status === "completed" && Object.hasOwn(data, "output")
        ? [structuredAction(data.name, data.output, data.output_id, data.publication_protocol)]
        : [];
    case "final":
      return [
        ...(data.output_info && Object.hasOwn(data, "output") ? [structuredAction(data.output_info.name, data.output, data.output_info.output_id, data.output_info.publication_protocol)] : []),
        { type: ASSISTANT_PRESENTATION_ACTIONS.COMPLETE, output: data.output_info ? undefined : data.output },
        ...(data.status === "completed" && data.output_files?.length
          ? [{ type: ASSISTANT_PRESENTATION_ACTIONS.OUTPUT_FILES, files: data.output_files }] : []),
      ];
    case "error":
      return [
        { type: ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_THINKING },
        { type: ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_TEXT },
      ];
    case "cancelled":
      return [
        { type: ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_THINKING },
        { type: ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_TEXT },
        { type: ASSISTANT_PRESENTATION_ACTIONS.INTERRUPT },
      ];
    default:
      return [];
  }
}

export function assistantActionsFromDeferred(deferred = {}) {
  return [
    ...(Array.isArray(deferred.calls) ? deferred.calls : []),
    ...(Array.isArray(deferred.approvals) ? deferred.approvals : []),
  ].map((call) => ({ type: ASSISTANT_PRESENTATION_ACTIONS.DEFERRED, call }));
}

export function applyAssistantPresentationActions(
  presenter,
  actions = [],
  { prepareDeferredCall = (call) => call } = {},
) {
  if (!presenter) return;
  for (const action of actions) {
    switch (action?.type) {
      case ASSISTANT_PRESENTATION_ACTIONS.THINKING:
        if (action.mode === "append") presenter.appendThinking(action.content || "");
        else presenter.addThinkingBlock(action.content || "");
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_THINKING:
        presenter.finalizeThinking();
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.TEXT:
        if (action.mode === "append") presenter.appendText(action.content || "");
        else presenter.setFinalText(action.content || "");
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.PROCESS_TEXT:
        presenter.addProcessText(action.content || "");
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.FINALIZE_TEXT:
        presenter.finalizeText();
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.TOOL_CALL:
        presenter.addToolCall(action.call || {});
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.TOOL_RESULT:
        presenter.addToolResult(action.result || {});
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.TOOL_REMOVE:
        presenter.removeToolCall(action.call || {});
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.DEFERRED:
        presenter.attachHitlWidget(prepareDeferredCall(action.call || {}));
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.STRUCTURED_OUTPUT:
        presenter.setStructuredOutput?.({
          name: action.name, output: action.output, id: action.outputId,
          ...(action.publicationProtocol ? { publicationProtocol: action.publicationProtocol } : {}),
        });
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.OUTPUT_FILES:
        presenter.setOutputFiles(action.files);
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.COMPLETE:
        presenter.complete(action.output);
        break;
      case ASSISTANT_PRESENTATION_ACTIONS.INTERRUPT:
        presenter.markInterrupted();
        break;
      default:
        break;
    }
  }
}

export function normalizePlanSnapshot(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.items)) return null;
  const items = snapshot.items
    .filter((item) => item && typeof item === "object")
    .map((item, index) => ({
      id: String(item.id || `plan-item-${index + 1}`),
      content: String(item.content || "未命名任务"),
      active_form: item.active_form ? String(item.active_form) : null,
      status: String(item.status || "pending"),
    }));
  if (!items.length) return null;
  const completed = items.filter((item) => item.status === "completed").length;
  return { items, total: items.length, completed };
}

export function planPanelState(snapshot) {
  const plan = normalizePlanSnapshot(snapshot);
  if (!plan) return null;
  return {
    plan,
    label: `${plan.completed}/${plan.total}`,
  };
}
