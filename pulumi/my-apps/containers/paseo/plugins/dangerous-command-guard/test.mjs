#!/usr/bin/env node
/**
 * Unit tests for the dangerous-command-guard plugin.
 * Run: node test.mjs   (from this directory)
 */
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const scratch = mkdtempSync(join(tmpdir(), "guard-test-"));
process.env.CLINE_GUARD_CONFIG = join(scratch, "config.json");
process.env.CLINE_GUARD_GRANTS = join(scratch, "grants.json");
process.env.CLINE_GUARD_LOG = join(scratch, "log.jsonl");

const module_ = await import("./index.js");
const plugin = module_.default;
const { evaluate, matchSegment, beforeTool } = module_.__test__;

const GATED = [
	"git push",
	"git push origin main",
	"git push --force origin main",
	"git push --force-with-lease",
	"git -C /srv/repo push",
	"git --git-dir=/srv/repo/.git push",
	"/usr/bin/git push origin main",
	"sudo git push",
	"sudo -u root git push origin main",
	"env GIT_SSH_COMMAND=ssh git push",
	"cd /srv/repo && git push",
	"git fetch && git push",
	"git push; echo done",
	'bash -c "git push origin main"',
	"sh -lc 'cd /srv/repo && git push'",
	"$(git push)",
	"pulumi up",
	"pulumi up --yes",
	"pulumi -C infra up",
	"pulumi -s prod up",
	"pulumi stack select prod && pulumi up",
	"sudo pulumi up --yes",
	// pyinfra (binary alone, absolute path, via `uv run`, via sudo) - the Makefile form
	"pyinfra --serial inventory.py install_k3s.py",
	"pyinfra --debug inventory.py install_k3s.py -vvv",
	"/usr/local/bin/pyinfra --serial inventory.py install_k3s.py",
	"uv run --group infra pyinfra --debug inventory.py install_k3s.py -vvv",
	"uv run --group infra pyinfra --serial inventory.py install_k3s.py",
	"sudo pyinfra --serial inventory.py install_k3s.py",
	"cd pyinfra && pyinfra --serial inventory.py install_k3s.py",
	// a redirected real command is still a real command
	"git push origin main > /tmp/out.log",
	// heredocs consumed by an interpreter are code, not data
	"bash <<'EOF'\ngit push\nEOF",
	"sh <<EOF\npulumi up\nEOF",
	// kubectl edits (mutating verbs) must be approved
	"kubectl apply -f deploy.yaml",
	"kubectl delete pod foo -n default",
	"kubectl -n kube-system patch deploy coredns -p '{}'",
	"kubectl --context prod scale deploy/web --replicas 3",
	"kubectl edit svc kubernetes",
	"kubectl rollout restart deploy/web",
	"kubectl set image deploy/web web=nginx:latest",
	"kubectl drain node1 --ignore-daemonsets",
	"kubectl cordon node2",
	"kubectl taint node node3 k=v:NoSchedule",
	"sudo kubectl delete ns foo",
	"kubectl auth reconcile -f x.yaml",
	"kubectl config set-context foo",
	"kubectl config use-context prod",
	"kubectl annotate pod p a=b",
	"kubectl label node n k=v",
];

const ALLOWED = [
	"git status",
	"git pull",
	"git fetch",
	'git commit -m "fix push bug"',
	"git log --grep=push",
	"git pushd /tmp",
	"npm run push",
	"pulumi preview",
	"pulumi preview --stack infra-apps --non-interactive --diff",
	"pulumi preview -s prod -m up", // an `up` argument must not gate a preview
	"pulumi preview --stack infra-apps --non-interactive 2>&1 | tail -60",
	"pulumi stack ls",
	"ls -la",
	'echo "pulumi up docs"',
	// data contexts: the guarded words are text being written/read, never executed
	"cat > /tmp/plan.md <<'PLAN_EOF'\n# plan\ngit push\npulumi up\npyinfra --serial inventory.py install_k3s.py\nPLAN_EOF",
	"cat > /tmp/handoff.md <<EOF\nbefore git push / pulumi up / pyinfra\nEOF",
	"echo git push > /tmp/notes.txt",
	"printf '%s\\n' git push >> /tmp/notes.txt",
	"grep -n -i -E 'pulumi up|git-push' /tmp/plan.md",
	"grep -rn 'pulumi up' docs/",
	// a quoted `|` is data, not a pipeline boundary
	"git commit -m 'wip|git push later'",
	"echo pyinfra docs",
	"which pyinfra",
	"ls pyinfra",
	// kubectl reads pass through untouched
	"kubectl get pods -A",
	"kubectl get pods -n kube-system",
	"kubectl -n kube-system get deploy coredns -o yaml",
	"kubectl --context prod get nodes",
	"kubectl describe node node1",
	"kubectl logs -f deploy/vints-server",
	"kubectl top nodes",
	"kubectl explain pod.spec",
	"kubectl version --client",
	"kubectl cluster-info",
	"kubectl api-resources",
	"kubectl rollout status deploy/web",
	"kubectl rollout history deploy/web",
	"kubectl auth can-i get pods",
	"kubectl config view",
	"kubectl config current-context",
	"kubectl diff -f deploy.yaml",
	// kubectl exec is allowed: read-through in-pod queries (KRR / metrics)
	"kubectl exec -it pod -- sh",
	"kubectl exec -n monitoring vmsingle-0 -- curl -s http://localhost:8429/query",
	"kubectl exec pod -- ls -la",
];

let failures = 0;

function check(condition, message) {
	if (!condition) {
		failures += 1;
		console.error(`FAIL ${message}`);
	}
}

for (const command of GATED) check(Boolean(evaluate({ command })), `should gate: ${command}`);
for (const command of ALLOWED) check(!evaluate({ command }), `should allow: ${command}`);

const shapeCases = [
	[{ commands: ["npm ci", "git push origin main"] }, "git-push"],
	[{ commands: [{ command: "git", args: ["push", "origin"] }] }, "git-push"],
	[[{ command: "pulumi", args: ["up", "--yes"] }], "pulumi-up"],
	[{ cmd: "pulumi up" }, "pulumi-up"],
	[["git push"], "git-push"],
	[{ command: "git status" }, undefined],
];
for (const [input, expected] of shapeCases) {
	const actual = evaluate(input)?.id;
	check(actual === expected, `shape ${JSON.stringify(input)} -> ${actual} (expected ${expected})`);
}

const runCommands = (input) => ({ tool: { name: "run_commands" }, input });

/* ---------- hook contract: default mode is "block" ---------- */
check(plugin.hooks.beforeTool === beforeTool, "top-level hooks.beforeTool exposed (sandbox hosts read this)");
check(plugin.setup({}, {}).hooks.beforeTool === beforeTool, "setup() exposes beforeTool (in-process hosts)");
check(plugin.manifest.capabilities.includes("hooks"), "manifest declares the hooks capability");
check((await beforeTool({ tool: { name: "read_files" }, input: { path: "/tmp" } })) === undefined, "other tools untouched");
check((await beforeTool(runCommands({ command: "ls" }))) === undefined, "safe command untouched");

const blocked = await beforeTool(runCommands({ command: "git push origin main" }));
check(blocked?.skip === true, "block mode returns skip");
check(/requires your explicit approval/.test(blocked?.reason ?? ""), "block reason asks the user for approval");
check(/git push origin main/.test(blocked?.reason ?? ""), "block reason echoes the command");
check((await beforeTool(runCommands({ command: "pulumi up --yes" })))?.skip === true, "pulumi up blocked too");
check((await beforeTool({ input: { command: "git push" } }))?.skip === true, "gates when tool name is unavailable");
check(matchSegment("git push") !== undefined, "matchSegment exported for debugging");

/* ---------- mode "prompt" uses Cline's native approval request ---------- */
writeFileSync(process.env.CLINE_GUARD_CONFIG, JSON.stringify({ mode: "prompt" }));
const prompted = await beforeTool(runCommands({ command: "git push origin main" }));
check(prompted?.policy?.autoApprove === false, "prompt mode requests approval");
check((await beforeTool(runCommands({ command: "ls -la" }))) === undefined, "prompt mode leaves other commands alone");
writeFileSync(process.env.CLINE_GUARD_CONFIG, JSON.stringify({ mode: "block" }));
check((await beforeTool(runCommands({ command: "git push" })))?.skip === true, "switching back to block mode takes effect");

/* ---------- one-shot / time-boxed grants (written by the TTY-only helper) ---------- */
writeFileSync(process.env.CLINE_GUARD_GRANTS, JSON.stringify({ "git-push": { remaining: 1 } }));
const granted = await beforeTool(runCommands({ command: "git push origin main" }));
check(granted?.policy?.autoApprove === true, "granted call is allowed through");
const afterGrant = JSON.parse(readFileSync(process.env.CLINE_GUARD_GRANTS, "utf8"));
check(afterGrant["git-push"] === undefined, "one-shot grant is consumed");
check((await beforeTool(runCommands({ command: "git push origin main" })))?.skip === true, "second call is blocked again");

writeFileSync(process.env.CLINE_GUARD_GRANTS, JSON.stringify({ "pulumi-up": { expiresAt: Date.now() - 1000 } }));
check((await beforeTool(runCommands({ command: "pulumi up" })))?.skip === true, "expired grant does not apply");

writeFileSync(process.env.CLINE_GUARD_GRANTS, JSON.stringify({ "pulumi-up": { expiresAt: Date.now() + 60000 } }));
check((await beforeTool(runCommands({ command: "pulumi up" })))?.policy?.autoApprove === true, "time-boxed grant allows");
check((await beforeTool(runCommands({ command: "git push" })))?.skip === true, "grant does not leak to the other guard");

if (failures > 0) {
	console.error(`\n${failures} check(s) failed`);
	process.exit(1);
}
// let the plugin's async diagnostics writes finish before exiting
await new Promise((resolve) => setTimeout(resolve, 300));
const logEvents = readFileSync(process.env.CLINE_GUARD_LOG, "utf8").trim().split("\n").length;
console.log(`All checks passed (diagnostic events: ${logEvents})`);
