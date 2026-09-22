// Provider-normalized input already includes cache reads and writes.
// Keep this projection aligned with agent/cache_usage.py; raw Usage is untouched.
const count = value => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;

function cacheCount(message, field, flag) {
  const usage = message?.usage || {};
  const value = count(usage[field]);
  if (usage[field] != null && value === null) return null;
  const provenance = message?.metadata?.deepagent_cache_usage;
  if (provenance?.version === 1 && provenance[flag] === false) return null;
  if (value !== null && value > 0) return value;
  // Only established provider fields establish a reported zero in old records.
  const details = usage.details || {};
  const aliases = message?.provider_name === "deepseek" && field === "cache_read_tokens"
    ? ["prompt_cache_hit_tokens"]
    : message?.provider_name === "anthropic"
      ? [field === "cache_read_tokens" ? "cache_read_input_tokens" : "cache_creation_input_tokens"]
      : [];
  for (const key of aliases) {
    const reported = count(details[key]);
    if (reported !== null) return reported;
  }
  return provenance?.version === 1 && provenance[flag] === true ? value : null;
}

export function cacheSummary(messages = []) {
  let input = 0, reads = 0, writes = 0, inputKnown = 0, readKnown = 0, writeKnown = 0;
  let total = 0, validRatios = true;
  const seen = new Set();
  for (const message of messages) {
    if (message?.kind !== "response") continue;
    if (message.run_id && message.provider_response_id) {
      const key = JSON.stringify([message.run_id, message.provider_name, message.model_name, message.provider_response_id]);
      if (seen.has(key)) continue;
      seen.add(key);
    }
    total++;
    let n = count(message?.usage?.input_tokens);
    if (n === 0 && !(count(message?.usage?.output_tokens) || 0)) n = null;
    const r = cacheCount(message, "cache_read_tokens", "read_reported");
    const w = cacheCount(message, "cache_write_tokens", "write_reported");
    if (n !== null) { input += n; inputKnown++; }
    if (r !== null) { reads += r; readKnown++; }
    if (w !== null) { writes += w; writeKnown++; }
    if (!n || r === null || r > n) validRatios = false;
  }
  const readComplete = total > 0 && readKnown === total;
  const writeComplete = total > 0 && writeKnown === total;
  const inputComplete = total > 0 && inputKnown === total;
  return {
    response_count: total,
    input_tokens: inputKnown ? input : null,
    cache_read_tokens: readKnown ? reads : null,
    cache_write_tokens: writeKnown ? writes : null,
    read_complete: readComplete,
    write_complete: writeComplete,
    hit_ratio: readComplete && inputComplete && validRatios && input > 0 ? reads / input : null,
  };
}

export function cacheMessageSummary(message) {
  return cacheSummary(message ? [message] : []);
}

// Trajectory has already grouped persisted parts into unique model responses.
export function cacheTrajectorySummary(rows = [], runId = null) {
  return cacheSummary(rows.filter(row => row.kind === "assistant" && row.native
    && (!runId || row.run_id === runId)).map(row => row.native));
}
