// Keep one pristine, non-interactive copy of the actual workspace markup.
// Never clone the live chat DOM: it contains another session's state and widgets.
let workspaceSource = null;

export function registerSessionViewTemplate(source) {
  const chat = source.querySelector(".app > .chat");
  if (!chat) throw new Error("工作台会话结构不完整");
  workspaceSource = chat.cloneNode(true);
  workspaceSource.querySelector(".composer")?.remove();
}

export function createReadonlySessionShell() {
  if (!workspaceSource) throw new Error("工作台会话结构尚未加载，请刷新重试。");
  const chat = workspaceSource.cloneNode(true);
  // Reuse all classes and structural elements, but scope IDs to this host.
  chat.querySelectorAll("[id]").forEach((element) => {
    element.dataset.sessionRole = element.id;
    element.removeAttribute("id");
  });
  chat.querySelectorAll("[aria-controls], [aria-labelledby]").forEach((element) => {
    element.removeAttribute("aria-controls");
    element.removeAttribute("aria-labelledby");
  });
  chat.querySelector(".messages").replaceChildren();
  chat.querySelector(".trajectory-list").replaceChildren();
  const back = chat.querySelector('[data-session-role="toggleSidebar"]');
  back.dataset.detailBack = "";
  back.title = "返回会话";
  back.setAttribute("aria-label", "返回会话");
  back.innerHTML = '<i data-lucide="arrow-left" aria-hidden="true"></i>';
  const refresh = chat.querySelector("[data-open-context]");
  refresh.removeAttribute("data-open-context");
  refresh.dataset.detailRefresh = "";
  refresh.title = "刷新会话";
  refresh.setAttribute("aria-label", "刷新会话");
  refresh.innerHTML = '<i data-lucide="refresh-cw" aria-hidden="true"></i>';
  const copy = chat.querySelector('[data-session-role="copySessionIdBtn"]');
  copy.dataset.detailCopySession = "";
  copy.hidden = false;
  chat.querySelector('[data-session-role="toggleInspectorPaneBtn"]').dataset.detailInspector = "";
  chat.querySelector('[data-session-role="trajectorySearch"]').setAttribute("aria-label", "搜索轨迹");
  chat.querySelector(".trajectory-view").setAttribute("aria-label", "会话轨迹");
  return chat;
}
