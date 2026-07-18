export function parseSseBlock(block) {
  const lines = String(block || "").split(/\r?\n/);
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: { raw } };
  }
}

export function createSseParser(onEvent) {
  let buffer = "";
  return {
    push(chunk) {
      buffer += chunk;
      let boundary;
      while ((boundary = buffer.match(/\r\n\r\n|\n\n/))) {
        const rawBlock = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const event = parseSseBlock(rawBlock);
        if (event) onEvent(event);
      }
    },
    flush() {
      const event = parseSseBlock(buffer);
      buffer = "";
      if (event) onEvent(event);
    },
  };
}

export async function readSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = createSseParser(onEvent);
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true }));
  }
  parser.push(decoder.decode());
  parser.flush();
}

export function reduceToolEvents(events) {
  const calls = new Map();
  for (const event of events) {
    const data = event?.data || {};
    const id = data.tool_call_id;
    if (!id || !["tool_call", "tool_result"].includes(event.event)) continue;
    const current = calls.get(id) || { tool_call_id: id, status: "pending" };
    if (event.event === "tool_call") {
      calls.set(id, { ...current, ...data, status: "pending" });
    } else {
      calls.set(id, { ...current, ...data, status: "complete" });
    }
  }
  return [...calls.values()];
}
