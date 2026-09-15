// Shared native adapter protocol. No runtime npm dependencies or tool-output logging.
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const hash = (value) => createHash("sha256").update(value).digest("hex");

export class RTKDenied extends Error {}

function record(event) {
  appendFileSync(process.env.ACB_RTK_DECISIONS, JSON.stringify({ ...event, time: new Date().toISOString() }) + "\n");
}

function failure(error) {
  const message = error instanceof Error ? error.message : String(error);
  writeFileSync(process.env.ACB_RTK_FAILURE, JSON.stringify({ error: message }) + "\n");
  return new Error(`Required RTK integration failed: ${message}`);
}

function run(args, { cwd, signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new Error("RTK subprocess cancelled"));
    const child = spawn(process.env.ACB_RTK_BINARY, args, {
      cwd, detached: true, stdio: ["ignore", "pipe", "pipe"],
    });
    const kill = () => {
      try { process.kill(-child.pid, "SIGKILL"); }
      catch { child.kill("SIGKILL"); }
    };
    let cancelled = false;
    const cancel = () => { cancelled = true; kill(); };
    const timer = setTimeout(cancel, 2000);
    signal?.addEventListener("abort", cancel, { once: true });
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener("abort", cancel); };
    let stdout = "";
    let bytes = 0;
    let overflow = false;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      bytes += Buffer.byteLength(chunk);
      if (bytes > 1024 * 1024) {
        overflow = true;
        kill();
      } else {
        stdout += chunk;
      }
    });
    // Drain stderr, keeping diagnostics out of the model's tool result.
    child.stderr.resume();
    child.on("error", (error) => { cleanup(); reject(error); });
    child.on("close", (code, killedBy) => {
      cleanup();
      if (overflow || cancelled || killedBy || code === null) reject(new Error("RTK subprocess cancelled, timed out, or exceeded output limit"));
      else resolve({ code, stdout });
    });
  });
}

export async function initialize(harness) {
  try {
    for (const name of ["ACB_RTK_BINARY", "ACB_RTK_VERSION", "ACB_RTK_DECISIONS", "ACB_RTK_FAILURE", "ACB_RTK_LOADED", "RTK_DB_PATH"]) {
      if (!process.env[name]) throw new Error(`Missing ${name}`);
    }
    if (process.env.RTK_DISABLED === "1" || process.env.ACB_RTK_DISABLED === "1") {
      throw new Error("RTK cannot be disabled in a required native treatment");
    }
    const version = await run(["--version"]);
    if (version.code !== 0 || version.stdout.trim() !== `rtk ${process.env.ACB_RTK_VERSION}`) {
      throw new Error("RTK executable version mismatch");
    }
    process.env.PATH = `${dirname(process.env.ACB_RTK_BINARY)}:${process.env.PATH || ""}`;
    writeFileSync(process.env.ACB_RTK_LOADED, JSON.stringify({ harness, version: process.env.ACB_RTK_VERSION }) + "\n");
  } catch (error) {
    throw failure(error);
  }
}

export async function rewrite(command, toolCallId, options = {}) {
  const event = { phase: "decision", tool_call_id: toolCallId, original_sha256: typeof command === "string" ? hash(command) : null };
  try {
    if (typeof command !== "string" || !command.trim()) throw new Error("Shell command must be a nonempty string");
    if (/^\s*rtk(?:\s|$)/.test(command) || command.trimStart().startsWith(process.env.ACB_RTK_BINARY + " ")) {
      record({ ...event, decision: "already_rtk" });
      return command;
    }
    const result = await run(["rewrite", command], options);
    event.rewrite_status = result.code;
    if (result.code === 1 && !result.stdout.trim()) {
      record({ ...event, decision: "passthrough" });
      return command;
    }
    if (result.code === 2) {
      record({ ...event, decision: "deny" });
      throw new RTKDenied("RTK permission rule denied the command");
    }
    const selected = result.stdout.trim();
    if (![0, 3].includes(result.code) || !selected) throw new Error(`Invalid RTK rewrite response (status ${result.code})`);
    // Status 3 retains native host authorization; no adapter permission auto-allow.
    record({ ...event, decision: "rewrite", rewritten_sha256: hash(selected) });
    return selected;
  } catch (error) {
    if (error instanceof RTKDenied) throw error;
    record({ ...event, decision: "error" });
    throw failure(error);
  }
}

export function recordResult(toolCallId, content, isError = false) {
  try {
    const text = typeof content === "string" ? content : JSON.stringify(content);
    record({ phase: "result", tool_call_id: toolCallId, output_sha256: hash(text), output_bytes: Buffer.byteLength(text), is_error: isError });
  } catch (error) {
    throw failure(error);
  }
}
