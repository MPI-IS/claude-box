#!/usr/bin/env python3
"""Install shared skills/MCP registrations and adapt them to each agent CLI."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit


def name(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", value):
        raise ValueError("names must contain only letters, numbers, underscores, and hyphens")
    return value


def env_name(value):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", value):
        raise ValueError("invalid environment variable name")
    return value


def validate_tool(tool):
    if not isinstance(tool, dict) or set(tool) - {
        "command", "args", "env_vars", "url", "bearer_token_env_var"
    }:
        raise ValueError("invalid tool registration")
    if ("command" in tool) == ("url" in tool):
        raise ValueError("a tool needs either a stdio command or an HTTP URL")
    if "url" in tool:
        url = urlsplit(tool["url"])
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("tool URL must be HTTP(S) without embedded credentials")
        if "args" in tool or tool.get("env_vars"):
            raise ValueError("HTTP tools use --bearer-env, not command arguments or --env")
    else:
        if not isinstance(tool["command"], str) or not tool["command"] or "\0" in tool["command"]:
            raise ValueError("invalid tool command")
        if "bearer_token_env_var" in tool:
            raise ValueError("--bearer-env requires an HTTP tool")
        if not isinstance(tool.get("args", []), list) or any(
            not isinstance(arg, str) or "\0" in arg for arg in tool.get("args", [])
        ):
            raise ValueError("tool arguments must be strings")
    if not isinstance(tool.get("env_vars", []), list):
        raise ValueError("env_vars must be a list")
    for variable in tool.get("env_vars", []):
        env_name(variable)
    if "bearer_token_env_var" in tool:
        env_name(tool["bearer_token_env_var"])
    return tool


def read_tools(directory):
    return {
        name(path.stem): validate_tool(json.loads(path.read_text()))
        for path in sorted(directory.glob("*.json"))
    }


def environment(tools):
    return sorted({
        variable for tool in tools.values()
        for variable in tool.get("env_vars", []) + (
            [tool["bearer_token_env_var"]] if "bearer_token_env_var" in tool else []
        )
    })


def toml(value):
    # The registration schema contains only strings, arrays, and tables.
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + toml(v) for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(toml(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def agent_arguments(agent, tools, environ):
    missing = [variable for variable in environment(tools) if not environ.get(variable)]
    if missing:
        raise ValueError("export required tool environment variables before launching: " + ", ".join(missing))
    if agent == "codex":
        args = []
        for key, tool in tools.items():
            args.extend(["-c", "mcp_servers." + key + "=" + toml(tool)])
        return args
    if agent != "claude":
        raise ValueError("unsupported agent")
    servers = {}
    for key, tool in tools.items():
        if "url" in tool:
            server = {"type": "http", "url": tool["url"]}
            if "bearer_token_env_var" in tool:
                server["headers"] = {"Authorization": "Bearer " + environ[tool["bearer_token_env_var"]]}
        else:
            server = {"type": "stdio", "command": tool["command"], "args": tool.get("args", [])}
            if tool.get("env_vars"):
                server["env"] = {variable: environ[variable] for variable in tool["env_vars"]}
        servers[key] = server
    return ["--mcp-config", json.dumps({"mcpServers": servers})] if servers else []


def main():
    # Internal launcher operations. Credentials are expanded only inside the box.
    if len(sys.argv) >= 3 and sys.argv[1] in ("_env", "_run"):
        tools = read_tools(Path(sys.argv[2]))
        if sys.argv[1] == "_env":
            print("\n".join(environment(tools)))
            return
        agent, *args = sys.argv[3:]
        extra = agent_arguments(agent, tools, os.environ)
        # Claude's --mcp-config is variadic. Put it last (before any explicit
        # end-of-options marker) so it cannot swallow the user's prompt.
        if agent == "claude":
            position = args.index("--") if "--" in args else len(args)
            args[position:position] = extra
        else:
            args = extra + args
        os.execvp(agent, [agent, *args])

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help=argparse.SUPPRESS)
    kinds = parser.add_subparsers(dest="kind", required=True)
    for kind in ("skill", "tool"):
        actions = kinds.add_parser(kind).add_subparsers(dest="action", required=True)
        actions.add_parser("list")
        actions.add_parser("remove").add_argument("name", type=name)
        install = actions.add_parser("install")
        if kind == "skill":
            install.add_argument("source", type=Path, help="directory containing SKILL.md")
        else:
            install.add_argument("name", type=name)
            install.add_argument("--url")
            install.add_argument("--env", action="append", default=[], type=env_name, metavar="VARIABLE")
            install.add_argument("--bearer-env", type=env_name, metavar="VARIABLE")
    # Everything after -- in a tool installation is an exact command argv.
    argv = sys.argv[1:]
    command = []
    if "--" in argv:
        position = argv.index("--")
        command, argv = argv[position + 1:], argv[:position]
    args = parser.parse_args(argv)
    if command and (args.kind, args.action) != ("tool", "install"):
        parser.error("a command is only valid with tool install")
    directory = args.root / (args.kind + "s")
    if args.action == "list":
        entries = sorted(directory.glob("*/SKILL.md")) if args.kind == "skill" else sorted(directory.glob("*.json"))
        for entry in entries:
            print(entry.parent.name if args.kind == "skill" else entry.stem)
        return
    if args.kind == "skill" and args.action == "install":
        source = args.source.expanduser().resolve()
        key = name(source.name)
        if not (source / "SKILL.md").is_file():
            raise ValueError("skill directory must contain SKILL.md")
        if any(path.is_symlink() for path in source.rglob("*")):
            raise ValueError("skill contents must not contain symlinks; copy their targets into the skill first")
    else:
        key = args.name
    target = directory / (key + ".json" if args.kind == "tool" else key)
    if args.action == "remove":
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
        print("Removed " + args.kind + " " + key)
        return
    if target.exists() or target.is_symlink():
        raise ValueError("already installed; remove it before installing a replacement")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.kind == "skill":
        with tempfile.TemporaryDirectory(prefix=".install-", dir=directory) as staging:
            copied = Path(staging) / key
            shutil.copytree(source, copied)
            copied.rename(target)
    else:
        if bool(args.url) == bool(command):
            raise ValueError("specify either --url URL or -- COMMAND [ARGS...]")
        tool = {"url": args.url} if args.url else {"command": command[0], "args": command[1:]}
        if args.env:
            tool["env_vars"] = args.env
        if args.bearer_env:
            tool["bearer_token_env_var"] = args.bearer_env
        validate_tool(tool)
        with target.open("x") as output:
            json.dump(tool, output, indent=2)
            output.write("\n")
    print("Installed " + args.kind + " " + key + " for Claude and Codex; restart sessions to load it.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        print("agent-box: " + str(error), file=sys.stderr)
        sys.exit(1)
