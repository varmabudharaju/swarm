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
    try:
        return json.loads(sp.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError as e:
        raise SettingsError(
            f"{sp} exists but is not valid JSON ({e}). Fix it or restore "
            f"{sp.name}.bak-swarm before running swarm install/uninstall."
        ) from e


def _has_marker(entries) -> bool:
    return any(HOOK_MARKER in (h.get("command") or "")
               for e in entries for h in (e.get("hooks") or []))


def _write_settings(sp: Path, settings: dict) -> None:
    backup = sp.with_name(sp.name + ".bak-swarm")
    mode = None
    if sp.exists():
        mode = sp.stat().st_mode
        backup.write_text(sp.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(backup, mode)
    tmp = sp.with_name(f"{sp.name}.{os.getpid()}.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(sp)
    if mode is not None:
        os.chmod(sp, mode)


def install(settings_path, claude_dir) -> None:
    sp = Path(settings_path).resolve()
    cd = Path(claude_dir)
    settings = _load_settings(sp)
    hooks = settings.setdefault("hooks", {})
    for ev in HOOK_EVENTS:
        entries = hooks.setdefault(ev, [])
        if not _has_marker(entries):
            entries.append({"hooks": [{"type": "command", "command": hook_command()}]})
    _write_settings(sp, settings)

    (cd / "workflows").mkdir(parents=True, exist_ok=True)
    (cd / "workflows" / "swarm-run.js").write_text(generate_workflow(), encoding="utf-8")
    skill_dst = cd / "skills" / "swarm"
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
    shutil.copytree(repo_root() / "skill", skill_dst)
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
        filtered = [e for e in hooks[ev] if not _has_marker([e])]
        if len(filtered) != len(hooks[ev]):
            changed = True
            hooks[ev] = filtered
            if not hooks[ev]:
                del hooks[ev]
    if changed:
        _write_settings(sp, settings)
    (cd / "workflows" / "swarm-run.js").unlink(missing_ok=True)
    shutil.rmtree(cd / "skills" / "swarm", ignore_errors=True)
    for name in AGENT_FILES:
        (cd / "agents" / name).unlink(missing_ok=True)
