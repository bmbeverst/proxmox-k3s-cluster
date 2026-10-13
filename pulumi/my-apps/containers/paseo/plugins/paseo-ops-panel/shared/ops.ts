import { defineRpc } from "@getpaseo/plugin";
import { z } from "zod";

/** Guard ids understood by the dangerous-command-guard plugin. */
export const GUARDS = ["git-push", "pulumi-up", "pyinfra", "kubectl-edit"] as const;

/** Whitelisted repo commands the panel can run. */
export const COMMAND_IDS = [
  "git-status",
  "git-fetch",
  "git-pull",
  "git-push",
  "preview-my-apps",
  "preview-infra-apps",
] as const;

/** How long a guard grant should last. */
export const WINDOWS = ["once", "10m", "1h"] as const;

const runResult = z.object({
  command: z.string(),
  exitCode: z.number().nullable(),
  output: z.string(),
});

export type RunResult = z.infer<typeof runResult>;

/** Run one of the whitelisted repo commands in the workspace. */
export const runCommandRpc = defineRpc({
  name: "ops.run-command",
  input: z.object({
    workspaceId: z.string(),
    id: z.enum(COMMAND_IDS),
  }),
  output: runResult,
});

/** Run a free-text shell command in the workspace. */
export const runRawRpc = defineRpc({
  name: "ops.run-raw",
  input: z.object({
    workspaceId: z.string(),
    command: z.string().min(1),
  }),
  output: runResult,
});

/** Approve, list, or clear dangerous-command-guard grants. */
export const guardRpc = defineRpc({
  name: "ops.guard",
  input: z.object({
    workspaceId: z.string(),
    action: z.enum(["list", "clear", "approve"]),
    guard: z.enum(GUARDS).optional(),
    window: z.enum(WINDOWS).default("once"),
  }),
  output: runResult,
});
