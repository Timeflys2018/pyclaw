"""PyClaw Skill CLI — install, list, search, check skills.

Usage:
    pyclaw-skill list [--workspace PATH]
    pyclaw-skill search QUERY
    pyclaw-skill install SLUG [--version VERSION] [--workspace PATH]
    pyclaw-skill check [--workspace PATH]
    pyclaw-skill curator list [--auto | --stale | --archived]
    pyclaw-skill curator restore ENTRY_ID
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path


def _resolve_workspace(args: argparse.Namespace) -> Path:
    ws = getattr(args, "workspace", None)
    if ws:
        return Path(ws).resolve()
    return Path.cwd()


def cmd_list(args: argparse.Namespace) -> None:
    """List discovered skills in the workspace."""
    from pyclaw.infra.settings import SkillSettings
    from pyclaw.skills.discovery import discover_skills
    from pyclaw.skills.eligibility import filter_eligible

    workspace = _resolve_workspace(args)
    settings = SkillSettings()
    skills = discover_skills(workspace, settings)
    eligible = filter_eligible(skills)

    ineligible_names = {s.name for s in skills} - {s.name for s in eligible}

    if not skills:
        print(f"No skills found in workspace: {workspace}")
        print(f"  Scanned: {workspace / settings.workspace_skills_dir}")
        print(f"  Scanned: {Path(settings.managed_skills_dir).expanduser()}")
        return

    print(f"Skills ({len(eligible)} eligible, {len(ineligible_names)} filtered)\n")
    for skill in skills:
        status = "✅" if skill.name not in ineligible_names else "❌"
        emoji = skill.emoji or " "
        desc = skill.description[:60] if skill.description else "(no description)"
        location = skill.file_path
        print(f"  {status} {emoji} {skill.name:<20} {desc}")
        print(f"     └─ {location}")
    print()


def cmd_search(args: argparse.Namespace) -> None:
    """Search ClawHub for skills."""
    from pyclaw.skills.clawhub_client import ClawHubClient

    async def _search() -> None:
        client = ClawHubClient()
        try:
            results = await client.search(args.query)
            if not results:
                print(f"No skills found for: {args.query}")
                return

            print(f"Found {len(results)} skill(s):\n")
            for r in results:
                print(f"  📦 {r.slug} (v{r.version})")
                print(f"     {r.description[:80]}")
                print()
        finally:
            await client.close()

    asyncio.run(_search())


def cmd_install(args: argparse.Namespace) -> None:
    """Install a skill from ClawHub."""
    from pyclaw.skills.clawhub_client import ClawHubClient
    from pyclaw.skills.installer import install_skill

    workspace = _resolve_workspace(args)
    install_dir = workspace / "skills"
    install_dir.mkdir(parents=True, exist_ok=True)

    async def _install() -> None:
        client = ClawHubClient()
        try:
            print(f"Installing {args.slug}...")
            version = getattr(args, "version", None)
            dest = await install_skill(client, args.slug, version, install_dir)
            print(f"✅ Installed to: {dest}")
            print(f"   SKILL.md: {dest / 'SKILL.md'}")

            from pyclaw.skills.parser import parse_skill_file
            manifest = parse_skill_file(dest / "SKILL.md")
            print(f"   Name: {manifest.name}")
            print(f"   Description: {manifest.description[:80]}")
            if manifest.requirements.bins:
                print(f"   Requires bins: {', '.join(manifest.requirements.bins)}")
            if manifest.requirements.env:
                print(f"   Requires env: {', '.join(manifest.requirements.env)}")
            print(f"\n   Run `pyclaw-skill list` to verify discovery.")
        finally:
            await client.close()

    asyncio.run(_install())


def cmd_check(args: argparse.Namespace) -> None:
    """Check skill eligibility (which pass/fail and why)."""
    import os
    import shutil

    from pyclaw.infra.settings import SkillSettings
    from pyclaw.skills.discovery import discover_skills
    from pyclaw.skills.eligibility import check_any_bins, check_bins, check_env, check_os

    workspace = _resolve_workspace(args)
    settings = SkillSettings()
    skills = discover_skills(workspace, settings)

    if not skills:
        print("No skills found.")
        return

    print(f"Checking {len(skills)} skill(s)...\n")
    for skill in skills:
        reqs = skill.requirements
        issues: list[str] = []

        if reqs.os and not check_os(reqs.os):
            issues.append(f"OS: need {reqs.os}, have {sys.platform}")
        if not skill.always:
            if reqs.bins and not check_bins(reqs.bins):
                missing = [b for b in reqs.bins if shutil.which(b) is None]
                issues.append(f"bins missing: {', '.join(missing)}")
            if reqs.any_bins and not check_any_bins(reqs.any_bins):
                issues.append(f"anyBins: none of {reqs.any_bins} found")
            if reqs.env and not check_env(reqs.env):
                missing_env = [e for e in reqs.env if not os.environ.get(e)]
                issues.append(f"env missing: {', '.join(missing_env)}")

        if skill.disable_model_invocation:
            issues.append("disable-model-invocation: true (hidden from prompt)")

        status = "✅" if not issues else "❌"
        print(f"  {status} {skill.name}")
        for issue in issues:
            print(f"     └─ {issue}")

    print()


def _get_memory_dbs() -> list[Path]:
    from pyclaw.infra.settings import load_settings

    settings = load_settings()
    base_dir = Path(settings.memory.base_dir).expanduser()
    if not base_dir.is_dir():
        return []
    return sorted(base_dir.glob("*.db"))


def _open_db(path: Path):
    import apsw

    from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

    conn = apsw.Connection(str(path))
    register_jieba_tokenizer(conn)
    return conn


def _format_table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    if not rows:
        return "  (no entries)"
    lines: list[str] = []
    lines.append("  " + " | ".join(headers))
    lines.append("  " + "-+-".join("-" * len(h) for h in headers))
    for row in rows:
        lines.append("  " + " | ".join(str(c) for c in row))
    return "\n".join(lines)


def cmd_curator_list(args: argparse.Namespace) -> None:
    from pyclaw.infra.settings import load_settings

    settings = load_settings()
    db_paths = _get_memory_dbs()
    if not db_paths:
        print("No memory databases found.")
        return

    if args.auto:
        sql = (
            "SELECT id, content, use_count, last_used_at "
            "FROM procedures WHERE type='auto_sop' AND status='active'"
        )
        headers = ("id      ", "content                                                     ", "use_cnt", "last_used_at    ")
    elif args.stale:
        stale_days = settings.evolution.curator.stale_after_days
        threshold = int(time.time()) - stale_days * 86400
        sql = (
            "SELECT id, content, use_count, last_used_at "
            "FROM procedures WHERE status='active' "
            f"AND COALESCE(last_used_at, created_at) < {threshold}"
        )
        headers = ("id      ", "content                                                     ", "use_cnt", "last_used_at    ")
    elif args.archived:
        sql = (
            "SELECT id, content, archived_at, archive_reason "
            "FROM procedures WHERE status='archived'"
        )
        headers = ("id      ", "content                                                     ", "archived_at     ", "reason          ")
    else:
        print("Specify one of: --auto, --stale, --archived")
        sys.exit(1)

    for db_path in db_paths:
        conn = _open_db(db_path)
        try:
            cursor = conn.execute(sql)
            rows_raw = cursor.fetchall()
        except Exception:
            continue
        finally:
            conn.close()

        if not rows_raw:
            continue

        rows: list[tuple[str, ...]] = []
        for row in rows_raw:
            entry_id = str(row[0])[:8]
            content = str(row[1] or "")[:60]
            col3 = str(row[2]) if row[2] is not None else ""
            col4 = str(row[3]) if row[3] is not None else ""
            rows.append((entry_id, content, col3, col4))

        print(f"\n[{db_path.stem}]")
        print(_format_table(rows, headers))

    print()


def cmd_curator_restore(args: argparse.Namespace) -> None:
    entry_id = args.entry_id
    db_paths = _get_memory_dbs()
    if not db_paths:
        print("No memory databases found.")
        return

    pattern = f"{entry_id}%"
    restored = False

    for db_path in db_paths:
        conn = _open_db(db_path)
        try:
            conn.execute(
                "UPDATE procedures SET status='active', archived_at=NULL, archive_reason=NULL "
                "WHERE id LIKE ? AND status='archived'",
                (pattern,),
            )
            changes = conn.changes()
            if changes > 0:
                print(f"Restored {changes} entry(ies) from [{db_path.stem}]")
                restored = True
        except Exception:
            continue
        finally:
            conn.close()

    if not restored:
        print(f"No archived entry found matching '{entry_id}'.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pyclaw-skill",
        description="PyClaw Skill Hub CLI — manage ClawHub-compatible skills",
    )
    subparsers = parser.add_subparsers(dest="command")

    p_list = subparsers.add_parser("list", help="List discovered skills")
    p_list.add_argument("--workspace", "-w", help="Workspace path (default: cwd)")

    p_search = subparsers.add_parser("search", help="Search ClawHub for skills")
    p_search.add_argument("query", help="Search query")

    p_install = subparsers.add_parser("install", help="Install a skill from ClawHub")
    p_install.add_argument("slug", help="Skill slug (e.g., 'github')")
    p_install.add_argument("--version", "-v", help="Specific version (default: latest)")
    p_install.add_argument("--workspace", "-w", help="Workspace path (default: cwd)")

    p_check = subparsers.add_parser("check", help="Check skill eligibility")
    p_check.add_argument("--workspace", "-w", help="Workspace path (default: cwd)")

    p_curator = subparsers.add_parser("curator", help="Manage memory procedures (SOPs)")
    curator_sub = p_curator.add_subparsers(dest="curator_command")

    p_cur_list = curator_sub.add_parser("list", help="List procedures")
    p_cur_list.add_argument("--auto", action="store_true", help="Show active auto-generated SOPs")
    p_cur_list.add_argument("--stale", action="store_true", help="Show stale entries")
    p_cur_list.add_argument("--archived", action="store_true", help="Show archived entries")

    p_cur_restore = curator_sub.add_parser("restore", help="Restore an archived procedure")
    p_cur_restore.add_argument("entry_id", help="Entry ID (or prefix) to restore")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "curator":
        if getattr(args, "curator_command", None) is None:
            p_curator.print_help()
            sys.exit(1)
        curator_commands = {
            "list": cmd_curator_list,
            "restore": cmd_curator_restore,
        }
        try:
            curator_commands[args.curator_command](args)
        except KeyboardInterrupt:
            sys.exit(130)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    commands = {
        "list": cmd_list,
        "search": cmd_search,
        "install": cmd_install,
        "check": cmd_check,
    }

    try:
        commands[args.command](args)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
