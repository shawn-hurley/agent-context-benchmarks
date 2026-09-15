import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { initialize, rewrite, recordResult, RTKDenied } from "./common.mjs";

export default async function (pi: ExtensionAPI) {
  await initialize("pi");
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "bash") return;
    try {
      event.input.command = await rewrite(event.input.command, event.toolCallId, { cwd: ctx.cwd, signal: ctx.signal });
    } catch (error) {
      return { block: true, terminate: !(error instanceof RTKDenied), reason: String(error) };
    }
  });
  pi.on("tool_result", async (event) => {
    if (event.toolName === "bash") recordResult(event.toolCallId, event.content, event.isError);
  });
}
