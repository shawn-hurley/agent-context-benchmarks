import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// OpenAI tool messages omit Pi's isError flag. Carry explicit success only
// for eligible shell logs; never infer success from their textual contents.
export default function (pi: ExtensionAPI) {
  pi.on("tool_result", async (event) => {
    if (event.toolName !== "bash" || event.isError !== false) return;
    if (!event.content?.length || event.content.some(block => block.type !== "text")) return;
    if (/\bacb-recall\b/.test(String(event.input?.command ?? ""))) return;
    const text = event.content.map(block => block.text).join("\n");
    const lines = text.split("\n").filter(line => line.trim());
    if (text.length < 2048 || lines.length < 8 ||
        /(error|exception|fail(?:ed|ure)?|traceback|panic|fatal)/i.test(text) ||
        !lines.every(line => /^(?:\d{4}-\d\d-\d\d[T ][\d:.+Z-]+\s+)?\[?INFO\]?\s/.test(line))) return;
    return { content: [{ type: "text", text: JSON.stringify({
      acb_caveman: 1, exit_code: 0, stdout: text
    }) }] };
  });
}
