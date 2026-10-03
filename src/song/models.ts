// Is the parent Hermes's model locally hosted? Same rule as the plugin's models.py:
// a local runtime provider, or an endpoint on this machine. Never judged by model name.
const LOCAL_PROVIDERS = new Set(["ollama", "llamacpp", "llama.cpp", "llama-cpp", "lmstudio", "lm-studio", "vllm", "local", "mlx"]);

export function isLoopback(url?: string | null) {
  if (!url) return false;
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase().replace(/^\[|\]$/g, "");
  } catch {
    return false;
  }
  return host === "localhost" || host.endsWith(".local") || host.endsWith(".localhost") || /^127\./.test(host) || host === "::1" || host === "0.0.0.0";
}

export function isLocalModel(provider?: string | null, baseUrl?: string | null) {
  return LOCAL_PROVIDERS.has((provider ?? "").toLowerCase()) || isLoopback(baseUrl);
}

export async function parentModel(home: string, provider?: string) {
  const text = await Bun.file(`${home}/config.yaml`).text().catch(() => "");
  const cfg = (text ? Bun.YAML.parse(text) : {}) as { model?: { provider?: string; base_url?: string; default?: string } };
  const configured = cfg.model ?? {};
  const effective = provider ?? configured.provider;
  // The configured endpoint describes the run only when it uses the configured provider.
  const baseUrl = effective === configured.provider ? configured.base_url : undefined;
  return { provider: effective, model: configured.default, baseUrl, local: isLocalModel(effective, baseUrl) };
}
