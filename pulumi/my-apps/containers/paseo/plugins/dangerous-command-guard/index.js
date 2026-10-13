/**
 * dangerous-command-guard
 *
 * Global Cline plugin that stops Cline from running a small set of
 * high-impact shell commands (`git push`, `pulumi up`, `pyinfra`, editing
 * `kubectl`) without an explicit human decision - on every project, in every
 * mode (TUI, headless, yolo, sub-agents), while leaving all other commands
 * exactly as they were.
 *
 * Matching is deliberately not a plain substring search: a guarded word only
 * counts when it could really run. Quoted strings/pipelines stay intact, a
 * heredoc body is code only when its interpreter consumes it, and commands
 * that merely take text or paths (`echo`, `grep`, `cat`, `ls`, ...) never gate.
 * So writing the words "git push" into a file is not blocked, but running
 * `git push` is.
 *
 * Why a plugin and not a hook/setting:
 *   - Cline's settings only expose a single global `toolAutoApprove` switch, so
 *     they cannot express "stop for this command but not for that one".
 *   - `~/Documents/Cline/Hooks/PreToolUse` scripts can only `cancel` a task
 *     (which aborts the whole run) or inject context; they cannot ask.
 *   - A runtime `beforeTool` hook can (a) return `skip` to block a single tool
 *     call with an explanatory message, or (b) return
 *     `policy.autoApprove = false` to trigger Cline's approval prompt.
 *
 * Modes (`~/.cline/dangerous-command-guard.json`):
 *   "block"  (default) - the command is never executed; the model is told to
 *                        ask you for approval. Enforced unconditionally,
 *                        because Cline's interactive client silently approves
 *                        `autoApprove:false` requests while auto-approve is on.
 *   "prompt"           - use Cline's native approval request. Gives you the
 *                        "[y/N]" prompt when auto-approve is off; silently
 *                        approved by the TUI when auto-approve is on.
 *
 * To let one specific command through, run the companion helper from your own
 * terminal (never from inside Cline):
 *   cline-approve-dangerous git-push --once
 *   cline-approve-dangerous pulumi-up --for 10m
 *
 * Disable globally: `cline plugin uninstall dangerous-command-guard`
 * (or delete ~/.cline/plugins/dangerous-command-guard).
 */

const PLUGIN_NAME = "dangerous-command-guard";
const PLUGIN_VERSION = "1.3.0";
const RUN_COMMANDS_TOOL = "run_commands";
const MAX_NESTED_DEPTH = 3;

/** Commands that must always be approved by the user. */
const GUARDED = [
	{ id: "git-push", bin: "git", subcommand: "push", anywhereToken: null },
	// `up` is always the pulumi command word, so match the subcommand only.
	// (An `anywhereToken: "up"` also fired when "up" was merely an argument,
	// e.g. `pulumi preview -m up`, which must never gate read-only previews.)
	{ id: "pulumi-up", bin: "pulumi", subcommand: "up", anywhereToken: null },
	// pyinfra has no fixed subcommand, so any invocation of the binary counts.
	{ id: "pyinfra", bin: "pyinfra", subcommand: null, anywhereToken: null },
	// kubectl stays usable for reads; only mutating subcommands are gated.
	{ id: "kubectl-edit", bin: "kubectl", match: kubectlEdits },
];

/** kubectl subcommands that change cluster state. */
const KUBECTL_EDIT_VERBS = new Set([
	"apply", "create", "delete", "edit", "patch", "replace", "scale", "autoscale",
	"expose", "run", "set", "annotate", "label", "taint", "cordon", "uncordon",
	// NOTE: `exec` is intentionally NOT listed. It is used for read-through
	// in-pod queries (e.g. scraping VictoriaMetrics for KRR), which must pass.
	// Add it back here if you want to gate `kubectl exec` shells again.
	"drain", "attach", "cp", "debug", "certificate",
]);

/** Compound kubectl verbs whose second word can still be read-only. */
const KUBECTL_READ_SECOND = {
	rollout: new Set(["status", "history"]),
	auth: new Set(["can-i", "whoami"]),
	config: new Set(["view", "get-contexts", "current-context", "get"]),
};

/**
 * Commands whose arguments are text or paths and are never run as commands.
 * A segment led by one of these never gates (it can only write or read the
 * guarded words), which is what lets `echo git push > f` and `grep 'pulumi up'`
 * through without opening a hole for the real binaries.
 */
const DATA_COMMANDS = new Set([
	"echo", "printf", "cat", "tee", "sed", "awk", "gawk", "mawk", "grep", "egrep",
	"fgrep", "rg", "ag", "head", "tail", "sort", "uniq", "cut", "tr", "wc", "nl",
	"od", "xxd", "strings", "base64", "jq", "yq", "diff", "comm", "join", "paste",
	"fold", "fmt", "column", "expand", "rev", "tac", "shuf", "man", "which", "type",
	"ls", "cd", "pushd", "popd", "stat", "file", "readlink", "realpath", "dirname",
	"basename", "mkdir", "rmdir", "touch", "rm", "mv", "cp", "chmod", "chown", "ln",
	"du", "df",
]);

/** Interpreters whose heredoc body is executed rather than treated as data. */
const HEREDOC_INTERPRETERS = new Set([
	"sh", "bash", "zsh", "dash", "ksh", "ash", "mksh", "ssh", "python", "python3",
	"perl", "ruby", "node", "php", "pwsh",
]);

/** Global flags that consume the next token, per binary. */
const FLAGS_WITH_VALUE = {
	git: new Set([
		"-C",
		"-c",
		"--git-dir",
		"--work-tree",
		"--namespace",
		"--exec-path",
		"--config-env",
		"--super-prefix",
		"--attr-source",
	]),
	pulumi: new Set([
		"-C",
		"--cwd",
		"-s",
		"--stack",
		"--color",
		"--config-file",
		"--env",
		"--policy-pack",
		"--policy-pack-config",
	]),
	kubectl: new Set([
		"-n",
		"--namespace",
		"-s",
		"--server",
		"--kubeconfig",
		"--context",
		"--cluster",
		"--user",
		"--token",
		"--certificate-authority",
		"--client-certificate",
		"--client-key",
		"--as",
		"--as-group",
		"--request-timeout",
		"--cache-dir",
		"--tls-server-name",
		"--v",
		"--vmodule",
	]),
};

const TOKEN_PATTERN = /"[^"]*"|'[^']*'|[^\s]+/gu;
/** Last-resort check used only if the structured matcher itself throws. */
const CRUDE_PATTERN = /(?:\bgit\b[^\n"]{0,240}?\bpush\b)|(?:\bpulumi\b[^\n"]{0,240}?\bup\b)|(?:\bpyinfra\b)/iu;

/**
 * Split a shell string into command segments on unquoted separators
 * (`&&`, `||`, `;`, `|`, `&`, newline). Separators inside quotes are data, so a
 * pattern like 'pulumi up|git-push' is kept whole instead of being torn into a
 * bogus `pulumi up` segment.
 */
function splitSegments(command) {
	const segments = [];
	const text = String(command);
	let current = "";
	let quote = null;
	for (let index = 0; index < text.length; index += 1) {
		const char = text[index];
		if (quote === "'") {
			current += char;
			if (char === "'") quote = null;
			continue;
		}
		if (quote === '"') {
			if (char === "\\") {
				current += char + (text[index + 1] ?? "");
				index += 1;
				continue;
			}
			current += char;
			if (char === '"') quote = null;
			continue;
		}
		if (char === "\\") {
			current += char + (text[index + 1] ?? "");
			index += 1;
			continue;
		}
		if (char === "'" || char === '"') {
			quote = char;
			current += char;
			continue;
		}
		if (char === "\n" || char === ";" || char === "|" || char === "&") {
			segments.push(current);
			current = "";
			if ((char === "|" || char === "&") && text[index + 1] === char) index += 1;
			continue;
		}
		current += char;
	}
	segments.push(current);
	return segments;
}

/** Heredoc declarations (`<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`, `<<\EOF`) on one line. */
function heredocDeclarations(line) {
	const declarations = [];
	const pattern = /<<(-?)\s*(?:'([^']+)'|"([^"]+)"|\\([A-Za-z0-9_]+)|([A-Za-z0-9_][A-Za-z0-9_.-]*))/gu;
	for (const match of line.matchAll(pattern)) {
		const delimiter = match[2] ?? match[3] ?? match[4] ?? match[5];
		if (!delimiter) continue;
		const before = line.slice(0, match.index);
		const executed = tokenize(before).some((token) => HEREDOC_INTERPRETERS.has(binName(token)));
		declarations.push({ delimiter, stripTabs: match[1] === "-", executed });
	}
	return declarations;
}

/**
 * Drop heredoc body lines, because they are data handed to a program rather
 * than commands. The exception: when the heredoc is consumed by an interpreter
 * (`bash`, `sh`, `ssh`, `python`, ...) the body is code and stays in scope.
 */
function stripHeredocBodies(command) {
	const lines = String(command).split("\n");
	const out = [];
	const pending = [];
	for (const line of lines) {
		const current = pending[0];
		if (current) {
			const compare = current.stripTabs ? line.replace(/^\t+/u, "") : line;
			if (compare === current.delimiter) {
				pending.shift();
				out.push(line);
				continue;
			}
			if (current.executed) out.push(line);
			continue;
		}
		out.push(line);
		for (const declaration of heredocDeclarations(line)) pending.push(declaration);
	}
	return out.join("\n");
}

function unquote(token) {
	return String(token).replace(/^["']/u, "").replace(/["']$/u, "");
}

/** `sudo`, `/usr/bin/git.exe` -> `git` */
function binName(token) {
	const normalised = unquote(token).replace(/\\/gu, "/");
	const base = normalised.slice(normalised.lastIndexOf("/") + 1);
	return base.replace(/\.(?:exe|cmd|bat)$/iu, "").toLowerCase();
}

function tokenize(segment) {
	return segment.match(TOKEN_PATTERN) ?? [];
}

function isAssignment(token) {
	return /^[A-Za-z_][A-Za-z0-9_]*=/u.test(unquote(token));
}

function isFlag(token) {
	const value = unquote(token);
	return value.startsWith("-") && value !== "-" && value !== "--";
}

/** The command word of a segment: first token that is not a `VAR=value` assignment. */
function commandWord(tokens) {
	for (const token of tokens) {
		if (isAssignment(token)) continue;
		return binName(token);
	}
	return undefined;
}

/** Nth non-flag token after the binary, skipping global flags and their values. */
function positionalAfter(tokens, binIndex, nth, bin) {
	const flagsWithValue = FLAGS_WITH_VALUE[bin] ?? new Set();
	let index = binIndex + 1;
	let seen = 0;
	while (index < tokens.length) {
		const token = tokens[index];
		if (isAssignment(token)) {
			index += 1;
			continue;
		}
		if (isFlag(token)) {
			const flag = unquote(token);
			const hasInlineValue = flag.includes("=");
			index += !hasInlineValue && flagsWithValue.has(flag) ? 2 : 1;
			continue;
		}
		seen += 1;
		if (seen === nth) return unquote(token).toLowerCase();
		index += 1;
	}
	return undefined;
}

/** First non-flag token after the binary (the subcommand for most CLIs). */
function subcommandAfter(tokens, binIndex, bin) {
	return positionalAfter(tokens, binIndex, 1, bin);
}

/** True when a kubectl invocation mutates cluster state (reads return false). */
function kubectlEdits(tokens, binIndex) {
	const verb = positionalAfter(tokens, binIndex, 1, "kubectl");
	if (verb === undefined) return false;
	if (KUBECTL_EDIT_VERBS.has(verb)) return true;
	const readSecond = KUBECTL_READ_SECOND[verb];
	if (readSecond) {
		const second = positionalAfter(tokens, binIndex, 2, "kubectl");
		return !readSecond.has(second);
	}
	return false;
}

/** Quoted payloads of `sh -c "..."`, `$( ... )` and backticks. */
function nestedScripts(segment) {
	const scripts = [];
	const patterns = [
		/(?:^|\s)-[A-Za-z]*c\s+"([^"]+)"/gu,
		/(?:^|\s)-[A-Za-z]*c\s+'([^']+)'/gu,
		/\$\(([^()]+)\)/gu,
		/`([^`]+)`/gu,
	];
	for (const pattern of patterns) {
		for (const match of segment.matchAll(pattern)) {
			const script = match[1];
			if (typeof script === "string" && script.trim()) scripts.push(script);
		}
	}
	return scripts;
}

/** True when a guarded-binary token at `index` is the guarded invocation. */
function matchesGuard(entry, tokens, index, bin) {
	if (entry.match) return entry.match(tokens, index);
	if (entry.subcommand === null) return true;
	if (subcommandAfter(tokens, index, bin) === entry.subcommand) return true;
	const rest = tokens.slice(index + 1).map((token) => unquote(token).toLowerCase());
	return Boolean(entry.anywhereToken && rest.includes(entry.anywhereToken));
}

/** Returns `{ id, command }` when a single shell segment is guarded. */
function matchSegment(segment, depth = 0) {
	const trimmed = String(segment).trim();
	if (!trimmed) return undefined;
	const tokens = tokenize(trimmed);
	// A segment led by a text/path command (`echo`, `grep`, `cat`, `ls`, ...)
	// only moves the guarded words around; it never runs them.
	if (DATA_COMMANDS.has(commandWord(tokens))) return undefined;
	for (let index = 0; index < tokens.length; index += 1) {
		const bin = binName(tokens[index]);
		const entry = GUARDED.find((candidate) => candidate.bin === bin);
		if (!entry) continue;
		if (matchesGuard(entry, tokens, index, bin)) return { id: entry.id, command: trimmed };
	}
	if (depth >= MAX_NESTED_DEPTH) return undefined;
	for (const script of nestedScripts(trimmed)) {
		for (const nestedSegment of splitSegments(script)) {
			const hit = matchSegment(nestedSegment, depth + 1);
			if (hit) return hit;
		}
	}
	return undefined;
}

/** Normalises one `run_commands` input entry (argv form included). */
function argvToString(value) {
	if (!value || typeof value !== "object") return undefined;
	const command = value.command;
	if (typeof command !== "string") return undefined;
	const args = Array.isArray(value.args) ? value.args.filter((arg) => typeof arg === "string") : [];
	return [command, ...args].join(" ");
}

/** Mirrors Cline's `run_commands` input union: string, string[], {command|cmd|commands}. */
function collectCommands(input, out = [], depth = 0) {
	if (depth > 4 || input === null || input === undefined) return out;
	if (typeof input === "string") {
		out.push(input);
		return out;
	}
	if (Array.isArray(input)) {
		for (const item of input) collectCommands(item, out, depth + 1);
		return out;
	}
	if (typeof input !== "object") return out;
	const argv = argvToString(input);
	if (argv !== undefined) out.push(argv);
	else if (typeof input.command === "string") out.push(input.command);
	else if (input.command !== undefined) collectCommands(input.command, out, depth + 1);
	if (typeof input.cmd === "string") out.push(input.cmd);
	if (typeof input.commands === "string") out.push(input.commands);
	else if (input.commands !== undefined) collectCommands(input.commands, out, depth + 1);
	if (out.length === 0) {
		for (const value of Object.values(input)) {
			if (typeof value === "string") out.push(value);
		}
	}
	return out;
}

/** `{ id, command }` when any command in the tool input needs approval. */
function evaluate(input) {
	for (const command of collectCommands(input)) {
		for (const segment of splitSegments(stripHeredocBodies(command))) {
			const hit = matchSegment(segment);
			if (hit) return { ...hit, source: String(command) };
		}
	}
	return undefined;
}

function truncate(value, limit = 500) {
	const text = String(value ?? "");
	return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function safeJson(value) {
	try {
		return JSON.stringify(value ?? null);
	} catch {
		return String(value);
	}
}

/* ------------------------------------------------------------------ *
 * Optional configuration + one-shot approval grants
 *
 * Config  : ~/.cline/dangerous-command-guard.json
 *           { "mode": "block" | "prompt" }        (default: "block")
 * Grants  : ~/.cline/approvals/dangerous-commands.json
 *           { "git-push": { "remaining": 1, "expiresAt": 1700000000000 } }
 *           Created by the `cline-approve-dangerous` helper from a real
 *           terminal (the helper refuses to run without a TTY, so the agent
 *           cannot grant approval to itself).
 * Overrides for tests: CLINE_GUARD_CONFIG, CLINE_GUARD_GRANTS, CLINE_GUARD_LOG
 * ------------------------------------------------------------------ */

const DEFAULT_CONFIG = { mode: "block" };
const fileCache = new Map();
let fsModule;
let fsResolved = false;

function homeDir() {
	if (typeof process === "undefined" || !process.env) return undefined;
	return process.env.HOME || process.env.USERPROFILE;
}

function configFilePath() {
	if (process.env?.CLINE_GUARD_CONFIG) return process.env.CLINE_GUARD_CONFIG;
	const home = homeDir();
	return home ? `${home}/.cline/dangerous-command-guard.json` : undefined;
}

function grantsFilePath() {
	if (process.env?.CLINE_GUARD_GRANTS) return process.env.CLINE_GUARD_GRANTS;
	const home = homeDir();
	return home ? `${home}/.cline/approvals/dangerous-commands.json` : undefined;
}

async function getFs() {
	if (!fsResolved) {
		fsResolved = true;
		try {
			fsModule = await import("node:fs");
		} catch {
			fsModule = undefined;
		}
	}
	return fsModule;
}

async function readJsonFile(filePath) {
	if (!filePath) return undefined;
	const fs = await getFs();
	if (!fs) return undefined;
	try {
		const stats = fs.statSync(filePath);
		const cached = fileCache.get(filePath);
		if (cached && cached.mtimeMs === stats.mtimeMs && cached.size === stats.size) return cached.value;
		const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
		fileCache.set(filePath, { mtimeMs: stats.mtimeMs, size: stats.size, value });
		return value;
	} catch {
		fileCache.set(filePath, { mtimeMs: -1, size: -1, value: undefined });
		return undefined;
	}
}

/** "block" (default, always enforced) or "prompt" (native approval prompt). */
async function resolveMode() {
	const config = await readJsonFile(configFilePath());
	const mode = config?.mode;
	if (mode === "prompt" || mode === "block") return mode;
	return DEFAULT_CONFIG.mode;
}

/** Consumes one matching grant entry; returns true when the call is approved. */
async function consumeGrant(matchId) {
	const filePath = grantsFilePath();
	const grants = await readJsonFile(filePath);
	const entry = grants?.[matchId];
	if (!entry || typeof entry !== "object") return false;
	const now = Date.now();
	if (typeof entry.expiresAt === "number" && entry.expiresAt <= now) return false;
	if (typeof entry.remaining === "number" && entry.remaining <= 0) return false;
	const fs = await getFs();
	if (fs) {
		try {
			const next = { ...grants };
			if (typeof entry.remaining === "number") {
				const remaining = entry.remaining - 1;
				if (remaining <= 0) delete next[matchId];
				else next[matchId] = { ...entry, remaining };
			}
			fs.mkdirSync(filePath.slice(0, filePath.lastIndexOf("/")), { recursive: true });
			fs.writeFileSync(filePath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
			fileCache.delete(filePath);
		} catch {
			/* best effort */
		}
	}
	return true;
}

function blockReason(hit, command) {
	return [
		`BLOCKED BY GLOBAL CLINE GUARD (dangerous-command-guard): \`${hit.id}\` requires your explicit approval.`,
		`Requested command: ${truncate(command, 300)}`,
		"It was NOT executed and no interactive approval was available for this session.",
		"Stop and ask the user for explicit approval. Do not retry, and do not work around the gate (no aliases, wrappers, shell scripts, editors, or other tools).",
		"The user either runs the command themselves, or grants approval and then tells you to retry.",
	].join("\n");
}

function gateResult(mode, hit, command) {
	if (mode === "prompt") return { policy: { autoApprove: false } };
	return { skip: true, reason: blockReason(hit, command) };
}

let lastGateKey = "";
let lastGateAt = 0;

/**
 * Best-effort diagnostics log (never throws, never blocks a tool call):
 * ~/.cline/logs/dangerous-command-guard.jsonl
 * Override with CLINE_GUARD_LOG=/path/to/file.
 */
function audit(entry) {
	if (entry?.event === "gated" || entry?.event === "blocked" || entry?.event === "gated-fallback") {
		// The hook can be registered by both the extension and plugin paths;
		// collapse the duplicate audit line for the same tool call.
		const key = `${entry.event}|${entry.match ?? ""}|${entry.command ?? ""}`;
		const now = Date.now();
		if (key === lastGateKey && now - lastGateAt < 2000) return;
		lastGateKey = key;
		lastGateAt = now;
	}
	if (typeof process === "undefined" || !process.env) return;
	const home = process.env.HOME || process.env.USERPROFILE;
	if (!home) return;
	const logPath = process.env.CLINE_GUARD_LOG || `${home}/.cline/logs/dangerous-command-guard.jsonl`;
	const line = `${JSON.stringify({ ts: new Date().toISOString(), plugin: PLUGIN_NAME, ...entry })}\n`;
	try {
		import("node:fs")
			.then((fs) => {
				try {
					const lastSlash = logPath.lastIndexOf("/");
					if (lastSlash > 0) fs.mkdirSync(logPath.slice(0, lastSlash), { recursive: true });
					fs.appendFileSync(logPath, line, "utf8");
				} catch {
					/* logging is best effort */
				}
			})
			.catch(() => {});
	} catch {
		/* logging is best effort */
	}
}

function toolNameOf(context) {
	const candidates = [context?.tool?.name, context?.toolCall?.toolName, context?.tool?.id];
	for (const candidate of candidates) {
		if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
	}
	return undefined;
}

/**
 * `beforeTool` runtime hook.
 *
 * Mode "block" (default): returns `skip`, so the command is never executed and
 * the model receives the block reason - enforced regardless of auto-approve
 * settings, in the TUI, headless, yolo and sub-agent runs alike.
 *
 * Mode "prompt": returns `policy.autoApprove = false`, which makes the agent
 * runtime ask its approval capability (the "[y/N]" prompt). Note that Cline's
 * interactive client auto-approves that request while "auto-approve all" is
 * enabled, so this mode only truly asks when auto-approve is off.
 *
 * An explicit human grant (`cline-approve-dangerous`) short-circuits both modes
 * and lets the single approved call run.
 */
async function beforeTool(context) {
	try {
		const toolName = toolNameOf(context);
		// Some runtimes sanitise the tool descriptor out of hook payloads; when
		// the name is unavailable we still evaluate the input, because only the
		// shell tool carries `command` / `commands` strings.
		if (toolName !== undefined && toolName !== RUN_COMMANDS_TOOL) return undefined;
		const hit = evaluate(context?.input);
		if (!hit) return undefined;
		if (await consumeGrant(hit.id)) {
			audit({ event: "granted", match: hit.id, command: truncate(hit.source) });
			// The user approved this one-shot grant from a real terminal.
			return { policy: { autoApprove: true } };
		}
		const mode = await resolveMode();
		audit({
			event: mode === "prompt" ? "gated" : "blocked",
			match: hit.id,
			mode,
			command: truncate(hit.source),
		});
		return gateResult(mode, hit, hit.source);
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		const raw = safeJson(context?.input);
		let fallback = false;
		try {
			fallback = CRUDE_PATTERN.test(raw);
		} catch {
			fallback = false;
		}
		if (!fallback) {
			audit({ event: "error", error: message });
			return undefined;
		}
		const mode = await safeMode();
		audit({ event: "gated-fallback", mode, error: message, command: truncate(raw) });
		// Fail safe: if our own matching logic breaks, still gate the call when
		// the raw input mentions a guarded command.
		return gateResult(mode, { id: "unsafe-fallback" }, raw);
	}
}

async function safeMode() {
	try {
		return await resolveMode();
	} catch {
		return DEFAULT_CONFIG.mode;
	}
}

let announced = false;

function setup(_api, context) {
	if (!announced) {
		announced = true;
		audit({ event: "loaded", version: PLUGIN_VERSION, cwd: context?.cwd ?? context?.session?.cwd });
	}
	return { hooks: { beforeTool } };
}

const plugin = {
	name: PLUGIN_NAME,
	manifest: {
		capabilities: ["hooks"],
		description: "Require explicit approval before git push, pulumi up, pyinfra and editing kubectl",
	},
	// Sandboxed plugin hosts (the CLI default) read hook functions from the
	// plugin's top level; the in-process runtime reads the object returned by
	// `setup()`. Expose both so the gate works in either mode.
	hooks: { beforeTool },
	setup,
};

export { plugin };
export default plugin;
export const __test__ = {
	evaluate,
	matchSegment,
	collectCommands,
	subcommandAfter,
	positionalAfter,
	kubectlEdits,
	tokenize,
	binName,
	nestedScripts,
	splitSegments,
	stripHeredocBodies,
	heredocDeclarations,
	commandWord,
	beforeTool,
	plugin,
	resolveMode,
	consumeGrant,
	gateResult,
	configFilePath,
	grantsFilePath,
	DEFAULT_CONFIG,
};
