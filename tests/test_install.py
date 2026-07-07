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


def test_install_workflow_only_writes_workflow(tmp_path):
    """Plugin bootstrap: write ~/.claude/workflows/swarm-run.js, touch nothing else."""
    cd = claude_dir(tmp_path)
    install.install_workflow(cd)
    wf = cd / "workflows" / "swarm-run.js"
    assert wf.read_text().startswith("export const meta")
    assert not (cd / "skills").exists()        # skill copy is the pip installer's job
    assert not (tmp_path / "settings.json").exists()  # hooks come from the plugin


def test_install_workflow_is_idempotent(tmp_path):
    """Write-if-changed: a second call with identical content must not rewrite."""
    cd = claude_dir(tmp_path)
    install.install_workflow(cd)
    wf = cd / "workflows" / "swarm-run.js"
    first_mtime = wf.stat().st_mtime_ns
    install.install_workflow(cd)
    assert wf.stat().st_mtime_ns == first_mtime  # unchanged content -> no write


# --- Group 1: settings hardening (findings 3, 6) ---


def test_non_dict_settings_list_refused(tmp_path):
    """A JSON array at the top level must raise SettingsError, not crash later
    or be silently overwritten (finding 3)."""
    sp = tmp_path / "settings.json"
    sp.write_text("[1, 2, 3]")
    before = sp.read_text()
    try:
        install.install(sp, claude_dir(tmp_path))
        assert False, "should have raised"
    except install.SettingsError:
        pass
    assert sp.read_text() == before  # untouched, not wiped


def test_empty_list_settings_refused(tmp_path):
    """`[]` is falsy and used to be coerced to `{}` then overwritten; it must
    raise instead (finding 3)."""
    sp = tmp_path / "settings.json"
    sp.write_text("[]")
    try:
        install.install(sp, claude_dir(tmp_path))
        assert False, "should have raised"
    except install.SettingsError:
        pass
    assert sp.read_text() == "[]"  # untouched, not silently wiped


def test_install_fails_loud_when_assets_missing(tmp_path, monkeypatch):
    """Non-editable pip installs ship only swarm_lib; install() must fail loud
    BEFORE writing settings.json, so no half-install is left behind (finding 6)."""
    fake_root = tmp_path / "fake_repo_root"
    fake_root.mkdir()
    monkeypatch.setattr(install, "repo_root", lambda: fake_root)
    sp = tmp_path / "settings.json"
    try:
        install.install(sp, claude_dir(tmp_path))
        assert False, "should have raised"
    except install.SettingsError as e:
        assert "installation assets not found" in str(e)
    assert not sp.exists()  # settings.json never created -> no half-install


# --- Group 2: uninstall correctness (findings 1, 5, 8) ---


def test_backup_written_once_and_removed_on_uninstall(tmp_path):
    """The .bak-swarm backup must hold the true pre-install settings mid-cycle
    and be deleted on uninstall - never overwritten from swarm-modified state
    (finding 1)."""
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    backup = tmp_path / "settings.json.bak-swarm"

    install.install(sp, cd)
    assert backup.exists()
    # mid-cycle the backup equals the ORIGINAL, not the swarm-modified settings
    assert json.loads(backup.read_text()) == EXISTING

    install.uninstall(sp, cd)
    assert not backup.exists()  # backup cleaned up on uninstall


def test_uninstall_leaves_unrelated_empty_event_untouched(tmp_path):
    """An unrelated event whose value is [] must survive uninstall; the change
    flag is per-event, not shared across the loop (finding 5)."""
    settings = {
        "hooks": {
            "AEvent": [{"hooks": [{"type": "command",
                                   "command": '"/py" -m swarm_lib.hook'}]}],
            "ZEvent": [],
        }
    }
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(settings))
    cd = claude_dir(tmp_path)
    install.uninstall(sp, cd)
    s = json.loads(sp.read_text())
    assert "ZEvent" in s["hooks"]      # unrelated empty event not collateral-deleted
    assert s["hooks"]["ZEvent"] == []
    assert "AEvent" not in s["hooks"]  # swarm-only event correctly removed


def test_plugin_flow_uninstall_without_settings(tmp_path):
    """Plugin-era teardown: install_workflow() then uninstall() with no prior
    install() and no settings.json must not crash, must remove the workflow, and
    must disturb nothing else (finding 8)."""
    cd = claude_dir(tmp_path)
    install.install_workflow(cd)
    wf = cd / "workflows" / "swarm-run.js"
    assert wf.exists()
    sp = tmp_path / "settings.json"  # deliberately never created
    install.uninstall(sp, cd)        # must tolerate a missing settings file
    assert not wf.exists()                                    # workflow removed
    assert not sp.exists()                                    # none conjured up
    assert not (tmp_path / "settings.json.bak-swarm").exists()


# --- Group 3: install hook logic (findings 4, 9) ---


def test_install_refreshes_stale_interpreter_hook(tmp_path):
    """A marker entry baked with an old/broken interpreter path must be rewritten
    to the current hook_command() on reinstall, not left stale (finding 4)."""
    stale = {
        "hooks": {
            "SubagentStop": [
                {"hooks": [{"type": "command",
                            "command": '"/old/broken/python" -m swarm_lib.hook'}]}
            ],
        }
    }
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(stale))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    s = json.loads(sp.read_text())
    cmds = [h["command"] for e in s["hooks"]["SubagentStop"] for h in e["hooks"]]
    assert install.hook_command() in cmds              # refreshed to current interp
    assert "/old/broken/python" not in " ".join(cmds)  # stale command gone
    marker_cmds = [c for c in cmds if "-m swarm_lib.hook" in c]
    assert len(marker_cmds) == 1                        # no duplicate entry appended


def _make_swarm_plugin(cd):
    p = cd / "plugins" / "marketplace-x" / "swarm" / ".claude-plugin"
    p.mkdir(parents=True, exist_ok=True)
    (p / "plugin.json").write_text('{"name": "swarm"}')


def test_install_skips_settings_hooks_when_plugin_present(tmp_path):
    """With the swarm plugin installed (it registers hooks natively), install()
    must NOT append hook entries to settings.json - but must still install the
    skill/workflow/agents (finding 9)."""
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    before = sp.read_text()
    cd = claude_dir(tmp_path)
    _make_swarm_plugin(cd)
    install.install(sp, cd)
    assert sp.read_text() == before  # settings.json untouched -> no double-register
    s = json.loads(sp.read_text())
    for ev in ("SubagentStop", "SessionStart"):
        cmds = [h["command"] for e in s["hooks"][ev] for h in e["hooks"]]
        assert not any("swarm_lib" in c for c in cmds)
    assert (cd / "skills" / "swarm" / "SKILL.md").exists()
    assert (cd / "workflows" / "swarm-run.js").exists()
    assert (cd / "agents" / "swarm-reader.md").exists()


def test_install_registers_hooks_when_no_plugin(tmp_path):
    """Negative case for finding 9: absent a plugin, hooks are registered."""
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    s = json.loads(sp.read_text())
    for ev in ("SubagentStop", "SessionStart"):
        cmds = [h["command"] for e in s["hooks"][ev] for h in e["hooks"]]
        assert any("-m swarm_lib.hook" in c for c in cmds)
