const NAIVE_SERVER_ISO = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?$/;

/**
 * Parse API timestamps through one compatibility boundary.
 *
 * New API values carry an explicit offset. Legacy SQLite/API values without
 * one are UTC values and must not be interpreted as browser-local wall time.
 */
export function parseServerTimestamp(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : new Date(value.getTime());
  }
  if (value == null || value === "") return null;

  let candidate = value;
  if (typeof value === "string") {
    const trimmed = value.trim();
    candidate = NAIVE_SERVER_ISO.test(trimmed)
      ? `${trimmed.replace(" ", "T")}Z`
      : trimmed;
  }
  const parsed = new Date(candidate);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function serverTimestampMilliseconds(value) {
  return parseServerTimestamp(value)?.getTime() ?? 0;
}

if (typeof globalThis !== "undefined") {
  globalThis.DeepAgentTime = {
    parseServerTimestamp,
    serverTimestampMilliseconds,
  };
}
