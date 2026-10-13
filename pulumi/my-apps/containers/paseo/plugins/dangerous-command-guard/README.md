# dangerous-command-guard

A **global Cline plugin** that stops Cline from running a small set of
high-impact shell commands without an explicit human decision - on every
project, in every mode (interactive TUI, headless, yolo, sub-agent runs) - while
leaving every other command exactly as it was.

Guarded commands:

| id | match |
|----|-------|
| `git-push` | `git ... push` (any flags/args, `sudo`, absolute paths, `cd x && git push`, `bash -c "git push ..."`, `$(git push)`) |
| `pulumi-up` | `pulumi ... up` (any flags/args, `-C dir`, `-s stack`) |
| `pyinfra` | any `pyinfra ...` invocation (bare, absolute path, `sudo pyinfra ...`, `uv run --group infra pyinfra ...`) |
| `kubectl-edit` | `kubectl` with a mutating subcommand (`apply`, `create`, `delete`, `edit`, `patch`, `replace`, `scale`, `set`, `rollout restart/undo/pause/resume`, `drain`, `cordon`, `taint`, `annotate`, `label`, ...). Read-only calls (`get`, `describe`, `logs`, `top`, `version`, `rollout status`, `config view`, `auth can-i`, `diff`, ...) and `exec` (used for read-through in-pod queries such as scraping metrics for KRR) pass through untouched. |

Matching looks for a command that could *run*, not just the words: quoted
strings and pipelines are left intact, a heredoc body counts as code only when
its consumer is an interpreter, and a segment led by a text/path command
(`echo`, `printf`, `cat`, `tee`, `grep`, `sed`, `ls`, `cd`, `rm`, ...) never
gates. Writing the words "git push" into a file is therefore allowed, while
running `git push` is not.

## Why a plugin (and not a hook or a setting)

* Cline settings only expose a single global switch (`toolAutoApprove`). There
  is no per-command allow/deny list, and `--auto-approve false` gates *every*
  tool, not just the guarded commands.
* `~/Documents/Cline/Hooks/PreToolUse` scripts can only `cancel` a task (which
  aborts the whole run) or inject context. They **cannot** ask for approval.
* The only in-band mechanism is a runtime `beforeTool` hook, which this plugin
  installs. It can either return `skip` (block one tool call, with a message) or
  `policy.autoApprove = false` (trigger Cline's approval request).

Verified against the installed code (`@cline/core`, `@cline/agents`,
`@cline/cli-linux-x64` 3.0.61): `prepareToolExecution` merges hook policy over
the session policy, and a hook `skip` produces an error tool-result carrying the
hook's message.

## Modes

Config file: `~/.cline/dangerous-command-guard.json`

```json
{ "mode": "block" }
```

* **`block`** (default) - the command is not executed; the model is told to stop
  and ask you. Guaranteed, because Cline's interactive client silently grants
  `autoApprove: false` requests while "auto-approve all" is enabled (verified:
  with the default settings a `git push` request was auto-approved in the TUI,
  which is why this mode is the default).
* **`prompt`** - use Cline's native approval request. You get the
  `Approve "run_commands" ... [y/N]` prompt **only when auto-approve is off**
  (`--auto-approve false`, or the TUI toggle). With auto-approve on, the request
  is approved silently, so this mode is not a guarantee.

## Letting a command through (grants)

From **your own terminal** (a TTY is required, so an agent with a shell tool
cannot approve itself):

```bash
cline-approve-dangerous git-push --once        # next matching command only
cline-approve-dangerous pulumi-up --for 10m    # time-boxed (30s, 10m, 2h, 1d)
cline-approve-dangerous pyinfra --once         # e.g. one `uv run ... pyinfra ...`
cline-approve-dangerous kubectl-edit --once    # e.g. next mutating kubectl command
cline-approve-dangerous --list                 # show current grants
cline-approve-dangerous --clear                # remove all grants
```

Grants live in `~/.cline/approvals/dangerous-commands.json` (mode 0600) and are
consumed on use. Then tell Cline it may retry.

Helper location: `~/.local/bin/cline-approve-dangerous` (source:
`cline-approve-dangerous` in this directory). Add `~/.local/bin` to `PATH` if it
is not already there, or call it with its full path.

## Install / uninstall

The plugin must live in each Cline *config directory* you use, because plugin
discovery is per config dir:

```bash
# master copy (this directory)
~/Projects/dangerous-command-guard

# default config (~/.cline) - every project, plain `cline ...`, Zed ACP runs
cline plugin install ~/Projects/dangerous-command-guard --force

# local-LLM stack config (~/.cline-omniroute, used by bin/run-cline.sh)
cline --config ~/.cline-omniroute plugin install \
  ~/Projects/dangerous-command-guard --force

# verify
ls ~/.cline/plugins/_installed/local/dangerous-command-guard-*/package/index.js
ls ~/.cline-omniroute/plugins/_installed/local/dangerous-command-guard-*/package/index.js

# disable (per config)
cline plugin uninstall dangerous-command-guard
cline --config ~/.cline-omniroute plugin uninstall dangerous-command-guard
```

Installed state on this machine:

| Cline config | Plugin path |
|---|---|
| `~/.cline` (default) | `~/.cline/plugins/_installed/local/dangerous-command-guard-*/package/` |
| `~/.cline-omniroute` (local LLM / `run-cline.sh`) | `~/.cline-omniroute/plugins/_installed/local/dangerous-command-guard-*/package/` |

Global plugin search paths Cline uses: `<config>/plugins`,
`~/Documents/Cline/Plugins`, `<repo>/.cline/plugins`.

> **Restart needed:** plugin hooks are attached when a session starts, so a
> Cline session that was already running keeps its old (unguarded) runtime. Start
> a new session (or restart the TUI) after installing or updating.

Grants (`~/.cline/approvals/dangerous-commands.json`) and the diagnostics log
(`~/.cline/logs/dangerous-command-guard.jsonl`) are shared by every config dir,
so one grant works for both setups.

## Diagnostics

Every decision is appended to `~/.cline/logs/dangerous-command-guard.jsonl`
(override with `CLINE_GUARD_LOG`):

```json
{"ts":"...","plugin":"dangerous-command-guard","event":"blocked","match":"git-push","mode":"block","command":"git push origin main"}
{"ts":"...","plugin":"dangerous-command-guard","event":"granted","match":"git-push","command":"..."}
{"ts":"...","plugin":"dangerous-command-guard","event":"loaded","version":"1.3.0"}
```

`event` is one of `loaded`, `blocked`, `gated` (prompt mode), `granted`,
`gated-fallback` (matcher failed, crude substring fallback), `error`.

## Tests

```bash
# matcher + hook contract + modes + grants (no Cline required)
node test.mjs

# live check: restart Cline so the plugin reloads, then confirm a fresh
# "loaded" line appears in the diagnostics log above.
```

## Matching: known limits

Text matching on the command string. Not caught (gate can be bypassed by hand):
aliases (`pulumi u`, `git p`), wrapper scripts/Make targets that call `git push`,
`pyinfra` or `kubectl` internally (e.g. `make install`), or a program that shells
out to `git push` itself.

The matcher only gates a guarded word when it could actually run:

* quoted separators/pipelines stay intact (`grep -E 'pulumi up|git-push' f` and
  `git commit -m 'wip|git push later'` do not gate);
* a heredoc body is treated as data, unless its consumer is an interpreter
  (`cat > plan.md <<EOF ... git push ... EOF` does not gate, but
  `bash <<EOF ... git push ... EOF` does);
* a segment led by a text/path command (`echo`, `printf`, `cat`, `tee`, `sed`,
  `awk`, `grep`, `ls`, `cd`, `rm`, ...) never gates.

Known residual false *positives*: guarded tokens as arguments of commands not in
that list, e.g. `ssh host 'git push ...'` stays unmatched while a bare
`pyinfra` mentioned as a path argument to some other tool may still gate - it is
a safety net, not a precise shell parser.

## Compatibility note

This uses Cline's plugin API (an `index.js` exporting
`{ name, manifest: { capabilities: ["hooks"] }, hooks: { beforeTool } }`, plus
`setup()` returning the same hooks for in-process hosts). The API is not
publicly documented and was read from the installed build (Cline CLI 3.0.61);
a future Cline update could change it. Failure mode is safe: if the plugin stops
loading, Cline simply behaves as before (auto-approve per your settings), and
the diagnostics log stops growing - check `~/.cline/logs/cline.log` for plugin
load errors.

## Alternative considered

A `~/Documents/Cline/Hooks/PreToolUse` script that returns
`{"cancel":true,"errorMessage":"..."}` also stops the command, but it aborts the
entire Cline task (the run ends with status `aborted`) and cannot ask, so it is
not used here.
