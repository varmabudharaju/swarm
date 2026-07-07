"""Regression coverage for the plugin packaging artifacts (previously untested).

These are the files a plugin install runs from the plugin cache dir; a broken
manifest or shim ships silently without these checks.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from swarm_lib import cli as cli_mod
from swarm_lib import install as install_mod

REPO = Path(__file__).resolve().parents[1]


def _load(rel):
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def test_plugin_manifest_parses_and_aligns_with_readme_instructions():
    plugin = _load(".claude-plugin/plugin.json")
    market = _load(".claude-plugin/marketplace.json")
    # README instructs: /plugin install swarm@swarm  -> plugin name @ marketplace name
    assert plugin["name"] == "swarm"
    assert market["name"] == "swarm"
    assert [p["name"] for p in market["plugins"]] == ["swarm"]
    # plugin.json points at a hooks file that actually ships
    hooks_rel = plugin["hooks"]
    assert (REPO / hooks_rel).is_file(), f"plugin.json hooks target missing: {hooks_rel}"
    # marketplace source must resolve within the repo
    assert (REPO / market["plugins"][0]["source"]).is_dir()


def test_hooks_json_declares_both_hooks_with_command_entries():
    hooks = _load("hooks/hooks.json")["hooks"]
    assert set(hooks) >= {"SubagentStop", "SessionStart"}
    for event, entries in hooks.items():
        for entry in entries:
            for h in entry["hooks"]:
                assert h["type"] == "command"
                assert isinstance(h["command"], str) and h["command"].strip()


def test_hooks_json_invokes_same_hook_module_as_pip_install():
    """Plugin hooks and pip-install hooks must run the same checkpointer module."""
    hooks = _load("hooks/hooks.json")["hooks"]
    stop_cmds = [h["command"] for e in hooks["SubagentStop"] for h in e["hooks"]]
    assert any("swarm_lib.hook" in c for c in stop_cmds)
    assert "swarm_lib.hook" in install_mod.hook_command()
    # SessionStart must bootstrap the workflow (plugins cannot ship one natively)
    start_cmds = [h["command"] for e in hooks["SessionStart"] for h in e["hooks"]]
    assert any("install-workflow" in c for c in start_cmds)


def test_bin_swarm_shim_has_valid_shell_syntax():
    shim = REPO / "bin" / "swarm"
    assert shim.is_file()
    proc = subprocess.run(["sh", "-n", str(shim)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    text = shim.read_text(encoding="utf-8")
    # shim must fall back to plugin-root resolution when CLAUDE_PLUGIN_ROOT is unset
    assert "CLAUDE_PLUGIN_ROOT" in text and "swarm_lib.cli" in text


def test_bin_swarm_shim_falls_back_from_python3_to_python():
    text = (REPO / "bin" / "swarm").read_text(encoding="utf-8")
    # shim must not hardcode a bare `python3` interpreter call: it must probe for
    # python3 first, fall back to `python`, and refuse clearly if neither exists
    assert "command -v python3" in text
    assert "command -v python" in text
    assert "PY=python" in text and "PY=python3" in text
    assert '"$PY" -m swarm_lib.cli' in text
    # must not fall straight through to an unconditional python3 exec anymore
    assert "exec python3 -m swarm_lib.cli" not in text


def test_bin_swarm_shim_runs_end_to_end():
    """Real subprocess invocation of the shim, not just a syntax check."""
    shim = REPO / "bin" / "swarm"
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
    proc = subprocess.run(
        [str(shim), "validate", "--help"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout.lower()
    assert "validate" in proc.stdout


def _cli_subcommands_referenced_in_hooks():
    hooks = _load("hooks/hooks.json")["hooks"]
    cmds = set()
    for entries in hooks.values():
        for entry in entries:
            for h in entry["hooks"]:
                m = re.search(r"-m\s+swarm_lib\.cli\s+(\S+)", h["command"])
                if m:
                    cmds.add(m.group(1))
    return cmds


def test_hooks_json_cli_subcommands_are_registered_in_swarm_lib_cli():
    """Every `python3 -m swarm_lib.cli <subcommand>` in hooks.json must be a real
    subparser, or the hook silently no-ops (argparse exits 2) at session start."""
    subcommands = _cli_subcommands_referenced_in_hooks()
    assert subcommands, "expected at least one swarm_lib.cli invocation in hooks.json"
    for name in subcommands:
        with pytest.raises(SystemExit) as exc:
            cli_mod.main([name, "--help"])
        assert exc.value.code == 0, f"{name!r} is not a registered swarm_lib.cli subcommand"
