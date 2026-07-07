"""Install swarm into ~/.claude: hooks (parse-or-refuse settings merge, tend-hardened),
workflow generation, skill + agent copies. Fully reversible."""
import json
import os
import shutil
import sys
from pathlib import Path

HOOK_EVENTS = ["SubagentStop", "SessionStart"]
HOOK_MARKER = "-m swarm_lib.hook"
AGENT_FILES = ["swarm-reader.md", "swarm-verifier.md", "swarm-implementer.md"]


class SettingsError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def hook_command() -> str:
    return f'"{sys.executable}" {HOOK_MARKER}'


def generate_workflow() -> str:
    wf = repo_root() / "workflows"
    header = (wf / "swarm-run.header.js").read_text(encoding="utf-8")
    body = (wf / "run_graph.mjs").read_text(encoding="utf-8")
    footer = (wf / "swarm-run.footer.js").read_text(encoding="utf-8")
    body = (body.replace("export async function", "async function")
                .replace("export function", "function")
                .replace("export const", "const"))
    return "\n".join([header.strip(), "", body.strip(), "", footer.strip(), ""])


def _load_settings(sp: Path) -> dict:
    if not sp.exists():
        return {}
    text = sp.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError(
            f"{sp} exists but is not valid JSON ({e}). Fix it or restore "
            f"{sp.name}.bak-swarm before running swarm install/uninstall."
        ) from e
    if not isinstance(data, dict):
        # A valid-JSON but non-object top level ([], null, 0, "", [1,2,3]) is
        # not a settings document. Refuse loudly instead of coercing it to {}
        # (which would silently wipe the file) or crashing later on .setdefault.
        raise SettingsError(
            f"{sp} must contain a JSON object at the top level, got "
            f"{type(data).__name__}. Fix it or restore {sp.name}.bak-swarm "
            f"before running swarm install/uninstall."
        )
    return data


def _require_assets() -> None:
    """Fail loud before touching settings if the packaged install assets are
    missing. A non-editable `pip install` ships only swarm_lib (not workflows/,
    skills/, agents/), so repo_root()-relative reads would explode mid-install
    and leave a half-registered hook behind."""
    root = repo_root()
    required = [
        root / "workflows" / "swarm-run.header.js",
        root / "workflows" / "run_graph.mjs",
        root / "workflows" / "swarm-run.footer.js",
        root / "skills" / "swarm",
        root / "agents",
    ]
    if any(not p.exists() for p in required):
        raise SettingsError(
            "swarm must be installed editable (pip install -e .) or as the "
            f"Claude Code plugin - installation assets not found at {root}"
        )


def _has_marker(entries) -> bool:
    return any(HOOK_MARKER in (h.get("command") or "")
               for e in entries for h in (e.get("hooks") or []))


def _plugin_installed(cd: Path) -> bool:
    """True if the swarm Claude Code plugin is installed under <claude_dir>/plugins.
    The plugin ships the checkpoint/nag hooks natively via hooks.json (which
    _has_marker never scans), so settings.json must not also register them or
    they would double-fire."""
    plugins = cd / "plugins"
    if not plugins.is_dir():
        return False
    return any(plugins.glob("**/swarm/.claude-plugin/plugin.json"))


def _write_settings(sp: Path, settings: dict) -> None:
    backup = sp.with_name(sp.name + ".bak-swarm")
    mode = None
    if sp.exists():
        mode = sp.stat().st_mode
        if not backup.exists():
            # Capture the true pre-swarm state exactly once. Writing on every
            # call would clobber the original with already-swarm-modified
            # content on the second write (e.g. install then uninstall).
            backup.write_text(sp.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(backup, mode)
    tmp = sp.with_name(f"{sp.name}.{os.getpid()}.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(sp)
    if mode is not None:
        os.chmod(sp, mode)


def install_workflow(claude_dir) -> None:
    """Generate ~/.claude/workflows/swarm-run.js (write-if-changed). This is the
    only piece a plugin install needs from settings.json's neighbourhood: plugins
    ship skills/agents/hooks natively but cannot ship a workflow, so the plugin's
    SessionStart hook calls this to bootstrap the workflow file."""
    cd = Path(claude_dir)
    wf = cd / "workflows" / "swarm-run.js"
    wf.parent.mkdir(parents=True, exist_ok=True)
    content = generate_workflow()
    if not wf.exists() or wf.read_text(encoding="utf-8") != content:
        wf.write_text(content, encoding="utf-8")


def install(settings_path, claude_dir) -> None:
    _require_assets()  # fail loud before any settings mutation / half-install
    sp = Path(settings_path).resolve()
    cd = Path(claude_dir)
    if _plugin_installed(cd):
        # The plugin owns the hooks (via hooks.json). Registering them in
        # settings.json too would double-fire the checkpoint/nag hooks.
        print("swarm plugin detected - skipping settings.json hook registration "
              "(the plugin ships these hooks natively)")
    else:
        settings = _load_settings(sp)
        hooks = settings.setdefault("hooks", {})
        current = hook_command()
        for ev in HOOK_EVENTS:
            entries = hooks.setdefault(ev, [])
            found = False
            for entry in entries:
                for h in (entry.get("hooks") or []):
                    if HOOK_MARKER in (h.get("command") or ""):
                        found = True
                        if h.get("command") != current:
                            # Entry baked by an old/broken interpreter: refresh it
                            # instead of leaving a stale command that never runs.
                            h["command"] = current
            if not found:
                entries.append({"hooks": [{"type": "command", "command": current}]})
        _write_settings(sp, settings)

    install_workflow(cd)
    skill_dst = cd / "skills" / "swarm"
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
    shutil.copytree(repo_root() / "skills" / "swarm", skill_dst)
    (cd / "agents").mkdir(parents=True, exist_ok=True)
    for name in AGENT_FILES:
        shutil.copy2(repo_root() / "agents" / name, cd / "agents" / name)


def uninstall(settings_path, claude_dir) -> None:
    sp = Path(settings_path).resolve()
    cd = Path(claude_dir)
    settings = _load_settings(sp)
    hooks = settings.get("hooks", {})
    changed = False
    for ev in list(hooks):
        ev_changed = False  # per-event: an unrelated event must not be touched
        new_entries = []
        for entry in hooks[ev]:
            original_hooks = entry.get("hooks") or []
            pruned_hooks = [
                h for h in original_hooks
                if HOOK_MARKER not in (h.get("command") or "")
            ]
            if len(pruned_hooks) == len(original_hooks):
                # No swarm commands in this entry — keep it unchanged.
                new_entries.append(entry)
            elif pruned_hooks:
                # Swarm commands removed but other commands remain — keep trimmed entry.
                ev_changed = True
                new_entries.append({**entry, "hooks": pruned_hooks})
            else:
                # All commands in this entry were swarm commands — drop the entry.
                ev_changed = True
        if ev_changed:
            changed = True
            if new_entries:
                hooks[ev] = new_entries
            else:
                del hooks[ev]
    if changed:
        _write_settings(sp, settings)
    # Remove swarm's own backup of the user's pre-install settings.
    sp.with_name(sp.name + ".bak-swarm").unlink(missing_ok=True)
    (cd / "workflows" / "swarm-run.js").unlink(missing_ok=True)
    shutil.rmtree(cd / "skills" / "swarm", ignore_errors=True)
    for name in AGENT_FILES:
        (cd / "agents" / name).unlink(missing_ok=True)
