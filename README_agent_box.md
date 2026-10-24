# agent-box

`agent-box` is an extended version of `claude-box`. It runs either
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) or the
[OpenAI Codex CLI](https://github.com/openai/codex) inside the same rootless
Podman sandbox, and adds three features:

- **Agent selection:** `agent-box claude` or `agent-box codex`.
- **Read-only Git metadata** by default.
- **Shared skills and MCP tools**, installed once and loaded by both agents in
  every project.

`claude-box` itself is unchanged. Both launchers use the same image,
configuration file and state directory, so you can use either one.
Everything in [README.md](README.md) about the sandbox, firewall, mounts and
SAIA/Bifrost still applies. This document covers only what is different.

## What changed

| File | Change |
|---|---|
| `agent-box` | New launcher, based on `claude-box`, with the features below. |
| `agent-box-extensions.py` | New host-side Python helper for installing skills and MCP tools and for adapting them to each agent at launch. |
| `Containerfile` | Also installs `nodejs`, `npm` and `@openai/codex`. |
| `claude-box` | Unchanged. |

### Differences from claude-box

| | `claude-box` | `agent-box` |
|---|---|---|
| Agents | Claude | Claude (default) or Codex |
| `.git` in the project | Writable | Read-only by default (`GIT_MODE`) |
| Global instructions | `shared/CLAUDE.md` for Claude | Also `shared/AGENTS.md` for Codex |
| Shared skills | Claude only | Claude and Codex |
| Shared MCP tools | Not supported | Claude and Codex |
| Host requirements | Podman | Podman, plus Python 3 for skills and tools |

## Installation

Rebuild the image so it contains Codex:

```bash
podman build -t claude-box:latest -f Containerfile .
```

To pin the Codex version instead of using the latest release:

```bash
podman build --build-arg CODEX_VERSION=<version> -t claude-box:latest -f Containerfile .
```

Install the launcher. The helper **must be in the same directory**, because
`agent-box` looks for it next to itself:

```bash
mkdir -p ~/.local/bin
install -m 0755 agent-box ~/.local/bin/agent-box
install -m 0644 agent-box-extensions.py ~/.local/bin/agent-box-extensions.py
```

## Usage

```text
agent-box [claude|codex] [wrapper options] [agent arguments]
agent-box skill install|list|remove ...
agent-box tool  install|list|remove ...
```

The agent name comes first, then wrapper options, then arguments passed to the
agent. If you omit the agent name, Claude is used.

```bash
agent-box                        # Claude, same as claude-box
agent-box claude --box-info
agent-box codex
agent-box codex --box-shell
agent-box codex exec "Explain this project"
agent-box claude --git-mode readwrite
```

Wrapper options:

| Option | Meaning |
|---|---|
| `--box-shell` | Start a shell in the box instead of the agent. |
| `--box-info` | Print the resolved configuration and the `podman run` command, then exit. |
| `--git-mode readonly\|readwrite` | Override `GIT_MODE` for this session. |
| `--saia`, `--saia-models`, `--bifrost`, `--bifrost-models`, `--bifrost-quota` | As in `claude-box`. **Claude only.** Codex rejects them. |

## Codex

### Authentication

Codex authentication works differently from Claude's. You log in *inside* the
box, and Codex writes the credentials to a file.

With a ChatGPT account (device login must be enabled for your account or
workspace):

```bash
agent-box codex login --device-auth
```

With an API key, without putting the key on the command line:

```bash
printf '%s' "$OPENAI_API_KEY" | agent-box codex login --with-api-key
```

Log out with `agent-box codex logout`. Stop running Codex boxes before logging
in or out, then restart them.

How it works:

- The login is stored in `~/.config/claude-box/codex/auth.json`, or under
  `$XDG_CONFIG_HOME`. Set `CLAUDE_BOX_CODEX_AUTH_DIR` to use another directory.
- That one file is mounted into **every** Codex box. It is writable, because
  Codex has to save refreshed tokens.
- For `login` and `logout`, the whole directory is mounted instead, so Codex
  can create or delete the file.
- Your Mac's `~/.codex` is never mounted, and your existing Codex login is not
  imported.
- `OPENAI_API_KEY` and `CODEX_CA_CERTIFICATE` are forwarded when set.
  Anthropic credentials are never passed to Codex.

> **Security note:** Claude's token reaches the box as an environment variable
> and stays read-only. Codex's `auth.json` is a writable file shared by all
> projects. An agent in any project can read, replace or delete the shared
> Codex login.

### Settings and state

- Each project gets its own Codex state directory: the project's usual
  `claude-box` state path with a `-codex` suffix. It is mounted at `/claude`,
  and Codex keeps its settings and history in `/claude/.codex`.
- On first use, `config.toml` is created with:

  ```toml
  approval_policy = "never"
  sandbox_mode = "danger-full-access"
  cli_auth_credentials_store = "file"
  ```

  As with Claude's `bypassPermissions` default, the container is the outer
  boundary. Existing settings are never overwritten. Edit the file with
  `agent-box codex --box-shell`, or pass Codex options on the command line.
- The launcher always passes `-c cli_auth_credentials_store="file"`, so the
  shared `auth.json` is used.

### Global instructions (AGENTS.md)

Just as `claude-box` mounts `shared/CLAUDE.md` as Claude's global
`~/.claude/CLAUDE.md`, `agent-box` mounts
`~/.local/share/claude-box/shared/AGENTS.md`, if it exists, read-only as
Codex's global `~/.codex/AGENTS.md`. Codex reads it in every project, in
addition to any `AGENTS.md` in the project itself.

The two files are separate, so each one can use its own agent's tool names. If
you want identical instructions, make one a symlink to the other, for example
`ln -s CLAUDE.md ~/.local/share/claude-box/shared/AGENTS.md`. The file is not
mounted for `codex login` and `codex logout`.

## Read-only Git metadata

By default (`GIT_MODE=readonly`), the project's top-level `.git` directory is
mounted read-only on top of the writable project. This applies to both agents.

- `git status`, `diff`, `log`, `show` and similar commands still work.
  `GIT_OPTIONAL_LOCKS=0` is set so that they don't try to refresh the index.
- Commits, branch changes, staging and edits to the repository configuration
  fail. This holds even if the agent uses another Git binary or writes the
  files directly.
- Source files stay writable. `git restore` and `git clean` can still change
  them.
- Pushing existing commits to a remote is not prevented.
- If `.git` is a file or a symlink (linked worktrees, submodules), the
  launcher refuses to start rather than leave it writable. Use `readwrite` for
  such projects.
- Nested repositories are not protected separately.

To allow Git changes, set `GIT_MODE=readwrite` in
`~/.config/claude-box/config`, or use `--git-mode readwrite` for one session.

## Shared skills and MCP tools

Skills and tools are installed once and loaded by new Claude and Codex sessions
in every project. The management commands need Python 3 on the host. They
don't need Podman, a running VM or agent authentication. Restart running
agents after installing or removing something.

### Skills

```bash
agent-box skill install /path/to/my-skill
agent-box skill list
agent-box skill remove my-skill
```

- The source directory must contain `SKILL.md`. Its name becomes the skill's
  name.
- The whole directory is copied into
  `~/.local/share/claude-box/shared/skills`. The source is not changed.
- Symlinks inside a skill are rejected.
- To update a skill, remove it and install it again.
- The collection is mounted read-only at `~/.claude/skills` for Claude and at
  `~/.agents/skills` for Codex.
- Skills are not rewritten. Their instructions and scripts must work for the
  chosen agent and inside the Linux container.

Note: `claude-box` mounts the same `shared/skills` directory for Claude, so
installed skills also show up there.

### MCP tools

Register a command-based (stdio) server or a remote HTTP server:

```bash
agent-box tool install clock -- uv tool run mcp-server-time
agent-box tool install example --url https://example.org/mcp
agent-box tool list
agent-box tool remove example
```

- The command after `--` runs **inside the container**, never on the host. It
  must be in the image, come from a package runner such as `uv tool run` or
  `npx`, or be reachable through an existing mount. Pin package versions in
  the command if you need reproducibility.
- Registrations are stored as JSON in
  `~/.local/share/claude-box/shared/tools`.
- At launch, they are translated into `--mcp-config` for Claude and into
  `-c mcp_servers.<name>=...` for Codex. Each agent's own MCP settings are
  left untouched.
- Registering a tool does not test the connection and does not open firewall
  exceptions.

Credentials are recorded by **variable name**, never by value:

```bash
agent-box tool install example --url https://example.org/mcp --bearer-env EXAMPLE_TOKEN
agent-box tool install local-example --env EXAMPLE_TOKEN -- my-mcp-server
```

Export the variables in your host shell before starting an agent. If one is
missing, startup fails and names it. Neither the registry files nor
`--box-info` contain the values.

> **Security note:** forwarded tool credentials are available to everything
> inside the box, including shell commands the agent runs. For Claude, a
> bearer token is placed directly in the `--mcp-config` argument, so it
> appears in the process list inside the container. Don't give the box
> credentials for privileged Git or cluster access. Use a separately secured
> service with its own authorization instead.

`claude-box` ignores registered MCP tools.

## Configuration

`agent-box` reads the same `~/.config/claude-box/config` as `claude-box`. New
settings:

```bash
GIT_MODE=readonly                 # or readwrite
CLAUDE_BOX_CODEX_AUTH_DIR=...     # default: ~/.config/claude-box/codex
```

The existing `CLAUDE_BOX_*` variables, the image tag `claude-box:latest` and
the state directory are shared by both launchers and both agents.
`CLAUDE_BOX_STATE` also moves the shared skills and tools.

Use `--box-info` to check what a session will get. It shows the agent, the Git
mode, the installed skills and MCP tools, the authentication source and the
full `podman run` command.

## Limitations

- Only Claude and Codex are supported.
- SAIA and Bifrost work with Claude only. The proxy translates Anthropic API
  requests, and Codex uses its own provider configuration.
- HTTP MCP tools support no authentication or a bearer token only. OAuth
  logins for MCP servers have to be set up separately in each agent.
- Codex is installed from npm with only npm's own checksums. Claude comes
  from a signed apt repository with a pinned key.
- The image uses Ubuntu's Node.js 18 package.
