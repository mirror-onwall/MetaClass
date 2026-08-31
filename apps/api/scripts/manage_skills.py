"""Install and verify deployment-managed MetaClass Skills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from metaclass.core.config import settings
from metaclass.deployment.skill_manager import SkillDeploymentManager

DEFAULT_LOCK = Path(__file__).resolve().parents[3] / "config" / "skills.lock.json"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    result.add_argument("--root", type=Path, default=settings.skill_root)
    result.add_argument("--json", action="store_true")
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list")
    check = subcommands.add_parser("check")
    check.add_argument("name", nargs="?")
    check.add_argument("--required-only", action="store_true")
    install = subcommands.add_parser("install")
    install.add_argument("name", nargs="?", help="Omit to install every enabled entry")
    install.add_argument("--required-only", action="store_true")
    install.add_argument("--force", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    manager = SkillDeploymentManager(args.lock, args.root)
    if args.command == "list":
        statuses = manager.list()
        ok = True
    elif args.command == "check" and args.name:
        statuses = [manager.check(args.name)]
        ok = statuses[0].state == "ready"
    elif args.command == "check":
        ok, statuses = manager.doctor(required_only=args.required_only)
    elif args.name:
        result = manager.install(args.name, force=args.force)
        statuses = [result.status]
        ok = result.status.state == "ready"
    else:
        results = manager.install_all(required_only=args.required_only, force=args.force)
        statuses = [item.status for item in results]
        ok = all(item.state == "ready" for item in statuses if item.required)
    payload = {"ok": ok, "skills": [item.model_dump(mode="json") for item in statuses]}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in statuses:
            problems = f" — {'; '.join(item.problems)}" if item.problems else ""
            print(f"{item.name}: {item.state}{problems}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
