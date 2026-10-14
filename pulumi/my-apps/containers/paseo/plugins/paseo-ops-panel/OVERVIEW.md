# Repo Ops

A workspace panel for Paseo that runs dangerous-command-guard approvals and common repository
commands without leaving the app.

The panel has two groups of buttons, a free-text command box, and an output pane.

Guard approvals run `~/Projects/dangerous-command-guard/cline-approve-dangerous` to grant, list,
or clear approvals for the guarded commands. Override the path with CLINE_GUARD_BIN. These run
inside a real Paseo terminal, which is a PTY, so the approval CLI's interactive-terminal check
passes; a plain subprocess would be refused.

Repo buttons run git status, git fetch, git pull, git push, and `pulumi preview` for the my-apps
and infra-apps stacks. The previews run as `pulumi preview --non-interactive -s dev`: these commands
run in a PTY, and the pod has no stack selected (PKO runs the stack against the project directory and
never writes a `.pulumi/` workspace), so without both flags Pulumi stops at the "Please choose a
stack" selector instead of previewing. `dev` is the stack PKO deploys. The free-text box runs any
shell command in the workspace.

Every command runs in a Paseo terminal (a PTY) in the workspace directory, so the working
directory, environment, and credentials are the daemon user's; the panel reads output by
capturing the terminal. Commands run this way are not seen by dangerous-command-guard, which only
inspects Cline tool calls.

Settings: CLINE_GUARD_BIN overrides the approval CLI path (default
`$HOME/Projects/dangerous-command-guard/cline-approve-dangerous`).
