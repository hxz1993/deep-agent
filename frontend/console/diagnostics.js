export function initDiagnostics() {
  const page = document.getElementById("page-diagnostics");
  if (!page) return;
  page.querySelectorAll("button[disabled]").forEach((button) => {
    button.setAttribute("aria-disabled", "true");
    button.title = "诊断后端具备真实链路数据后开放";
  });
}
