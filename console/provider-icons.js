const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
export const COMPOSER_PROVIDER_BRANDS = {
  deepseek: "./assets/provider-logos/deepseek.ico",
  moonshotai: "./assets/provider-logos/kimi.ico",
  alibaba: "./assets/provider-logos/qwen.png",
  zai: "./assets/provider-logos/zai.svg",
  openrouter: "./assets/provider-logos/openrouter.png",
  openai: "./assets/provider-logos/openai.svg?v=20260926-centered-2",
  openai_codex: "./assets/provider-logos/openai.svg?v=20260926-centered-2",
  anthropic: "./assets/provider-logos/anthropic.png",
  anthropic_compatible: "./assets/provider-logos/anthropic.png",
  minimax: "./assets/provider-logos/minimax.ico",
  stepfun: "./assets/provider-logos/stepfun.svg",
  google: "./assets/provider-logos/google-gemini.png",
  openai_compatible: "./assets/provider-logos/openai-compatible.svg",
};

function composerProviderType(provider) {
  const configured = String(provider?.provider_type || "").trim().toLowerCase();
  if (COMPOSER_PROVIDER_BRANDS[configured]) return configured;
  const hint = `${provider?.name || ""} ${provider?.id || ""}`.toLowerCase();
  if (hint.includes("deepseek")) return "deepseek";
  if (hint.includes("moonshot") || hint.includes("kimi")) return "moonshotai";
  if (hint.includes("qwen") || hint.includes("alibaba")) return "alibaba";
  if (hint.includes("zhipu") || hint.includes("zai")) return "zai";
  if (hint.includes("openrouter")) return "openrouter";
  if (hint.includes("anthropic") || hint.includes("claude")) return "anthropic";
  if (hint.includes("google") || hint.includes("gemini")) return "google";
  if (hint.includes("openai")) return "openai";
  return "openai_compatible";
}

export function composerProviderIcon(provider) {
  const providerType = composerProviderType(provider);
  const src = COMPOSER_PROVIDER_BRANDS[providerType];
  return `<img src="${escapeHtml(src)}" alt="" data-provider-type="${escapeHtml(providerType)}" />`;
}
