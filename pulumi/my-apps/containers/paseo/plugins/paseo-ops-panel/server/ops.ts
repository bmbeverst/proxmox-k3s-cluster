import type { PaseoApi } from "@getpaseo/client";
import type { RpcInput } from "@getpaseo/plugin";
import type { PluginHandlerContext } from "@getpaseo/plugin/server";
import { guardRpc, runCommandRpc, runRawRpc, type RunResult } from "../shared/ops";

/** Absolute path to the guard approval CLI; override with CLINE_GUARD_BIN. */
const GUARD_BIN =
  process.env.CLINE_GUARD_BIN ??
  `${process.env.HOME ?? "/home"}/Projects/dangerous-command-guard/cline-approve-dangerous`;

/** Printed by the wrapper after the command so capture knows it finished. */
const EXIT_MARK = "__OPS_EXIT_";
const EXIT_RE = new RegExp(`${EXIT_MARK}(\\d+)__`);

/** How long to wait for the exit sentinel before giving up on a command. */
const CAPTURE_TIMEOUT_MS = 5 * 60 * 1000;
/** Poll quickly at first so git/guard commands feel instant, then back off for long ones. */
const FAST_POLL_MS = 250;
const SLOW_POLL_MS = 1000;
const FAST_POLL_WINDOW_MS = 1000;

type CommandSpec = { label: string; shell: string };

const COMMANDS: Record<string, CommandSpec> = {
  "git-status": { label: "git status -sb", shell: "git status -sb" },
  "git-fetch": { label: "git fetch --prune", shell: "git fetch --prune" },
  "git-pull": { label: "git pull --ff-only", shell: "git pull --ff-only" },
  "git-push": { label: "git push", shell: "git push" },
  // `-s dev` and `--non-interactive` are both load-bearing. Nothing selects a stack in the pod
  // (PKO runs the stack with the project dir as its repoDir and never writes a `.pulumi/`
  // workspace), so a bare `pulumi preview` blocks on the "Please choose a stack" selector: these
  // commands run in a PTY, which Pulumi treats as interactive. `--non-interactive` fails fast
  // instead of prompting for anything else the CLI may want (e.g. a missing provider plugin).
  // `dev` is the stack PKO deploys (bmbeverst/my-apps/dev, bmbeverst/infra-apps/dev).
  "preview-my-apps": {
    label: "pulumi preview (my-apps)",
    shell: "cd pulumi/my-apps && pulumi preview --non-interactive -s dev",
  },
  "preview-infra-apps": {
    label: "pulumi preview (infra-apps)",
    shell: "cd pulumi/infra-apps && pulumi preview --non-interactive -s dev",
  },
};

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Run a command in a Paseo PTY (the guard needs isatty), capture sentinel-terminated output. */
async function runInTerminal(paseo: PaseoApi, workspaceId: string, shell: string): Promise<RunResult> {
  const script = `${shell}; printf '\\n${EXIT_MARK}%s__\\n' "$?"; sleep 30`;
  const terminal = await paseo.terminals.create({
    workspaceId,
    name: "ops",
    command: "bash",
    args: ["-lc", script],
  });

  let lines: string[] = [];
  try {
    const deadline = Date.now() + CAPTURE_TIMEOUT_MS;
    let nextPoll = FAST_POLL_MS;
    while (Date.now() < deadline) {
      await delay(nextPoll);
      const captured = await terminal.capture({ stripAnsi: true });
      lines = captured.lines;
      if (lines.some((line) => EXIT_RE.test(line))) break;
      // A preview refreshes every resource and takes minutes; a git command answers in
      // milliseconds. Poll fast enough for the latter without hammering the daemon for the former.
      if (Date.now() - (deadline - CAPTURE_TIMEOUT_MS) > FAST_POLL_WINDOW_MS) nextPoll = SLOW_POLL_MS;
    }
  } finally {
    // Also on a capture error: the wrapper's trailing `sleep` would otherwise hold the PTY open.
    await terminal.kill();
  }

  const exitIndex = lines.findIndex((line) => EXIT_RE.test(line));
  const match = exitIndex >= 0 ? EXIT_RE.exec(lines[exitIndex]) : null;
  const output = (exitIndex >= 0 ? lines.slice(0, exitIndex) : lines).join("\n").trim();
  return {
    command: shell,
    exitCode: match ? Number(match[1]) : null,
    output,
  };
}

export function runCommand(input: RpcInput<typeof runCommandRpc>, context: PluginHandlerContext) {
  const spec = COMMANDS[input.id];
  if (!spec) throw new Error(`unknown command: ${input.id}`);
  return runInTerminal(context.paseo, input.workspaceId, spec.shell);
}

export function runRaw(input: RpcInput<typeof runRawRpc>, context: PluginHandlerContext) {
  return runInTerminal(context.paseo, input.workspaceId, input.command);
}

export function runGuard(input: RpcInput<typeof guardRpc>, context: PluginHandlerContext) {
  const bin = JSON.stringify(GUARD_BIN);
  let shell: string;
  if (input.action === "list") {
    shell = `${bin} --list`;
  } else if (input.action === "clear") {
    shell = `${bin} --clear`;
  } else {
    if (!input.guard) throw new Error("approve needs a guard id");
    const flag = input.window === "once" ? "--once" : `--for ${input.window}`;
    shell = `${bin} ${input.guard} ${flag}`;
  }
  return runInTerminal(context.paseo, input.workspaceId, shell);
}
