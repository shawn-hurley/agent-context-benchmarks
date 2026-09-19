import type { Plugin } from "@opencode-ai/plugin";

const CavemanPlugin: Plugin = async () => {
  const recovery = new Set<string>();
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool === "bash" && /\bacb-recall\b/.test(String(output.args.command ?? ""))) {
        recovery.add(input.sessionID + ":" + input.callID);
      }
    },
    "tool.execute.after": async (input, output) => {
      const key = input.sessionID + ":" + input.callID;
      if (recovery.delete(key) || input.tool !== "bash" || output.metadata?.exit !== 0) return;
      const text = output.output;
      if (typeof text !== "string") return;
      const lines = text.split("\n").filter(line => line.trim());
      if (text.length < 2048 || lines.length < 8 ||
          /(error|exception|fail(?:ed|ure)?|traceback|panic|fatal)/i.test(text) ||
          !lines.every(line => /^(?:\d{4}-\d\d-\d\d[T ][\d:.+Z-]+\s+)?\[?INFO\]?\s/.test(line))) return;
      output.output = JSON.stringify({acb_caveman: 1, exit_code: 0, stdout: text});
    },
  };
};
export default CavemanPlugin;
