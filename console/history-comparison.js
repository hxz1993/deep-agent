/** Compare parsed JSON/projected History without allocating a serialized copy. */
export function sameHistoryValue(left, right) {
  const pending = [left, right];
  while (pending.length) {
    const b = pending.pop();
    const a = pending.pop();
    if (a === b) continue;
    if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    if (Array.isArray(a) && a.length !== b.length) return false;
    const keys = Object.keys(a);
    if (keys.length !== Object.keys(b).length) return false;
    for (const key of keys) {
      if (!Object.prototype.hasOwnProperty.call(b, key)) return false;
      pending.push(a[key], b[key]);
    }
  }
  return true;
}
