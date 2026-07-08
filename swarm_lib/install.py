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


def packaged_assets_root() -> Path:
    return Path(__file__).resolve().parent / "_assets"


def _has_assets(root: Path) -> bool:
    required = [
        root / "workflows" / "swarm-run.header.js",
        root / "workflows" / "run_graph.mjs",
        root / "workflows" / "swarm-run.footer.js",
        root / "skills" / "swarm",
        root / "agents",
    ]
    return all(p.exists() for p in required)


def asset_root() -> Path:
    """Install assets live at the repo root in a checkout/editable install and
    under swarm_lib/_assets inside a built wheel (hatchling force-include).
    Prefer the checkout so dev edits win; fall back to the packaged copy."""
    root = repo_root()
    if _has_assets(root):
        return root
    return packaged_assets_root()


def hook_command() -> str:
    return f'"{sys.executable}" {HOOK_MARKER}'


def generate_workflow() -> str:
    wf = asset_root() / "workflows"
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
    """Fail loud before touching settings if install assets are missing from
    BOTH the repo checkout and the packaged swarm_lib/_assets fallback (a
    corrupted or partial installation). Checked before any settings mutation
    so a failure never leaves a half-registered hook behind."""
    root = asset_root()
    if not _has_assets(root):
        raise SettingsError(
            "swarm installation assets not found at "
            f"{root} - reinstall (pip install agent-swarm or pip install -e .) "
            "or use the Claude Code plugin"
        )


def _plugin_installed(cd: Path) -> bool:
    """True if the swarm Claude Code plugin is installed under <claude_dir>/plugins.
    The plugin ships the checkpoint/nag hooks natively via hooks.json (which the
    settings.json marker scan never sees), so settings.json must not also register
    them or they would double-fire."""
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


def _swap_skill(cd: Path) -> None:
    """Replace <claude_dir>/skills/swarm atomically: stage the fresh copy in a
    temp sibling, then rename it into place. A failure mid-copy leaves the
    previously installed skill dir untouched instead of a destroyed half-install
    (the old rmtree-then-copytree wiped it before the copy could fail)."""
    src = asset_root() / "skills" / "swarm"
    dst = cd / "skills" / "swarm"
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging = cd / "skills" / f".swarm.stage.{os.getpid()}"
    old = cd / "skills" / f".swarm.old.{os.getpid()}"
    for scratch in (staging, old):
        if scratch.exists():
            shutil.rmtree(scratch)
    try:
        shutil.copytree(src, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise  # dst never touched -> previous skill dir survives
    if dst.exists():
        dst.rename(old)
    try:
        staging.rename(dst)
    except BaseException:
        if old.exists() and not dst.exists():
            old.rename(dst)  # roll back to the previous skill dir
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


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
    _swap_skill(cd)
    (cd / "agents").mkdir(parents=True, exist_ok=True)
    for name in AGENT_FILES:
        shutil.copy2(asset_root() / "agents" / name, cd / "agents" / name)


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
