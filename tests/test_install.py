import json
from pathlib import Path

from swarm_lib import install


EXISTING = {
    "hooks": {
        "SubagentStop": [{"hooks": [{"type": "command", "command": "python3 -m agent_pd.hook"}]},
                          {"hooks": [{"type": "command", "command": '"/py" -m tend.hook'}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": '"/py" -m tend.hook'}]}],
    },
    "statusLine": {"type": "command", "command": '"/py" -m tend.statusline'},
    "model": "claude-fable-5[1m]",
}


def claude_dir(tmp_path):
    d = tmp_path / "claude"
    d.mkdir(exist_ok=True)
    return d


def test_install_registers_hooks_and_copies_artifacts(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    s = json.loads(sp.read_text())
    for ev in ("SubagentStop", "SessionStart"):
        cmds = [h["command"] for e in s["hooks"][ev] for h in e["hooks"]]
        assert any("-m swarm_lib.hook" in c for c in cmds)
        assert any("agent_pd" in c or "tend" in c for c in cmds)  # preserved
    assert s["statusLine"]["command"] == '"/py" -m tend.statusline'  # untouched
    wf = (cd / "workflows" / "swarm-run.js").read_text()
    assert wf.startswith("export const meta")
    assert "async function runGraph" in wf
    assert "export function" not in wf and "export async" not in wf  # stripped
    assert "return __out" in wf
    assert (cd / "skills" / "swarm" / "SKILL.md").exists()
    assert (cd / "skills" / "swarm" / "references" / "packet-guide.md").exists()
    for a in ("swarm-reader", "swarm-verifier", "swarm-implementer"):
        assert (cd / "agents" / f"{a}.md").exists()
    assert (tmp_path / "settings.json.bak-swarm").exists()


def test_install_idempotent(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    once = json.loads(sp.read_text())
    install.install(sp, cd)
    assert json.loads(sp.read_text()) == once


def test_uninstall_reverts(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    install.uninstall(sp, cd)
    s = json.loads(sp.read_text())
    for ev in ("SubagentStop", "SessionStart"):
        cmds = [h["command"] for e in s["hooks"].get(ev, []) for h in e["hooks"]]
        assert not any("swarm_lib" in c for c in cmds)
        assert cmds  # tend/agent-pd entries survive
    assert not (cd / "workflows" / "swarm-run.js").exists()
    assert not (cd / "skills" / "swarm").exists()
    assert not (cd / "agents" / "swarm-reader.md").exists()


def test_corrupted_settings_refused(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text('{"hooks": [BROKEN')
    before = sp.read_text()
    try:
        install.install(sp, claude_dir(tmp_path))
        assert False, "should have raised"
    except install.SettingsError:
        pass
    assert sp.read_text() == before


def test_generated_workflow_parses_as_module(tmp_path):
    text = install.generate_workflow()
    assert text.count("export const meta") == 1
    assert "SWARM-TASK run=" in text


def test_install_creates_settings_in_nonexistent_dir(tmp_path):
    sp = tmp_path / "nodir" / "settings.json"
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    assert sp.exists()
    s = json.loads(sp.read_text())
    for ev in ("SubagentStop", "SessionStart"):
        cmds = [h["command"] for e in s["hooks"][ev] for h in e["hooks"]]
        assert any("-m swarm_lib.hook" in c for c in cmds)


def test_uninstall_removes_only_swarm_commands_from_mixed_entry(tmp_path):
    """Uninstall must not delete an entire entry if it also contains non-swarm commands."""
    mixed_settings = {
        "hooks": {
            "SubagentStop": [
                # Mixed entry: one swarm command and one unrelated command.
                {
                    "hooks": [
                        {"type": "command", "command": "python3 -m other.tool"},
                        {"type": "command", "command": '"/py" -m swarm_lib.hook'},
                    ]
                },
                # Pure swarm-only entry — should be fully removed.
                {
                    "hooks": [
                        {"type": "command", "command": '"/py" -m swarm_lib.hook'},
                    ]
                },
            ]
        }
    }
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(mixed_settings))
    cd = claude_dir(tmp_path)
    install.uninstall(sp, cd)
    s = json.loads(sp.read_text())
    entries = s["hooks"]["SubagentStop"]
    # Only the mixed entry should survive.
    assert len(entries) == 1
    cmds = [h["command"] for h in entries[0]["hooks"]]
    # The other.tool command must be preserved.
    assert any("other.tool" in c for c in cmds)
    # The swarm hook must be gone.
    assert not any("swarm_lib" in c for c in cmds)
