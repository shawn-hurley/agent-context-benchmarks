import type { Plugin } from "@opencode-ai/plugin";
import { initialize, rewrite, recordResult } from "./common.mjs";

const RTKPlugin: Plugin = async ({ directory }) => {
  await initialize("opencode");
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash") return;
      output.args.command = await rewrite(output.args.command, `${input.sessionID}:${input.callID}`, { cwd: directory });
    },
    "tool.execute.after": async (input, output) => {
      if (input.tool === "bash") recordResult(`${input.sessionID}:${input.callID}`, output.output, typeof output.metadata?.exit === "number" && output.metadata.exit !== 0);
    },
  };
};

export default RTKPlugin;
