"""Regression coverage for the plugin packaging artifacts (previously untested).

These are the files a plugin install runs from the plugin cache dir; a broken
manifest or shim ships silently without these checks.
"""
import json
import subprocess
from pathlib import Path

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
