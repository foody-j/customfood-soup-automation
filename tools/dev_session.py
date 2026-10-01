"""Start a role-scoped Claude session from fresh origin/main, without changing runtime files.

Python standard library only. No pull/reset/rebase in an existing checkout, no push or deployment.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SCOPES = {
    "pi": ("pi-server/", "docs/pi/", "notes/pi/"),
    "jetson": ("jetson/", "cam-adaptor/", "docs/jetson/", "notes/jetson/"),
}
SHARED_PREFIXES = ("shared/",)
SHARED_FILES = ("dashboard/src/data/schema.js", "notes/dev-log.md", "notes/decisions.md")


def shared(path: str) -> bool:
    return (
        path in SHARED_FILES
        or any(path.startswith(prefix) for prefix in SHARED_PREFIXES)
        or (path.startswith("docs/") and not path.startswith(("docs/pi/", "docs/jetson/")))
    )


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=90,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git failed")
    return result.stdout.strip() if "-z" not in args else result.stdout


def repo_root(cwd: Path) -> Path:
    return Path(git(cwd, "rev-parse", "--show-toplevel")).resolve()


def allowed(role: str, path: str) -> bool:
    # Paths come from git --no-renames; both sides of a rename must be in scope.
    return any(path.startswith(prefix) for prefix in SCOPES[role]) or shared(path)


def check(root: Path, role: str) -> list[str]:
    branch = git(root, "branch", "--show-current")
    if not branch.startswith(role + "/"):
        raise RuntimeError(f"Expected {role}/<task> branch; found {branch or 'detached HEAD'}")
    base = git(root, "merge-base", "HEAD", "refs/remotes/origin/main")
    paths: set[str] = set()
    for args in (
        ("diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--"),
        ("diff", "--cached", "--name-only", "--no-renames", "-z", "--"),
        ("diff", "--name-only", "--no-renames", "-z", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        paths.update(p for p in git(root, *args).split("\0") if p)
    return sorted(path for path in paths if not allowed(role, path))


def prepare(root: Path, role: str, task: str) -> Path:
    if os.environ.get("AI_AGENT_RUNNER") == "1":
        raise RuntimeError("Interactive launcher only. Keep the existing AI_AGENT_RUNNER workflow.")
    if role not in SCOPES or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", task):
        raise RuntimeError("Task must be 1-60 lowercase letters, digits or hyphens.")
    # Always locate new trees next to the primary checkout, even when invoked from a worktree.
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    primary = common.parent
    target = primary.parent / (primary.name + "-worktrees") / f"{role}-{task}"
    if target.exists():
        raise RuntimeError(f"Existing task is preserved: {target}. Continue there or choose a new task name.")
    branch = f"{role}/{task}"
    if git(root, "branch", "--list", branch):
        raise RuntimeError(f"Branch already exists: {branch}. No branch is reset or reused automatically.")
    # Explicit refspec works even if this clone has a restricted fetch configuration.
    git(root, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
    target.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "-b", branch, str(target), "refs/remotes/origin/main")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Fetch main, create isolated worktree, launch Claude")
    start.add_argument("role", choices=SCOPES)
    start.add_argument("task", help="New task name, e.g. thermal-card")
    start.add_argument("--prepare-only", action="store_true", help="Create worktree without launching Claude")
    audit = sub.add_parser("check", help="Check committed, staged, unstaged and untracked task paths")
    audit.add_argument("role", choices=SCOPES)
    args = parser.parse_args()
    root = repo_root(Path.cwd())
    if args.command == "check":
        blocked = check(root, args.role)
        if blocked:
            print("Outside role scope (do not commit/push; separate these changes):\n" + "\n".join(blocked))
            return 1
        print(f"Scope OK: {args.role}")
        return 0
    claude = shutil.which("claude")
    if not args.prepare_only and not claude:
        raise RuntimeError("claude not found on PATH. Install/locate it or use --prepare-only.")
    target = prepare(root, args.role, args.task)
    print(f"Development worktree: {target}\nBranch: {args.role}/{args.task}", flush=True)
    if args.prepare_only:
        return 0
    prompt = (
        f"This checkout's role is {args.role}. Read CLAUDE.md and docs/development-workflow.md first. "
        f"Device-owned paths: {', '.join(SCOPES[args.role])}. Record work in notes/{args.role}/{args.task}.md. "
        "Either role may also edit shared/, common docs/ (excluding the other role's docs), "
        "dashboard/src/data/schema.js, notes/dev-log.md and notes/decisions.md. "
        "For shared contract work, coordinate one owning branch, preserve compatibility where possible, "
        "and record payload examples, peer-device changes, tests and rollout order in the task note. "
        "The Fedora integration server is not a required gate. Do not edit the other device's implementation. "
        f"Run python3 tools/dev_session.py check {args.role} before each commit. "
        "Preserve existing permission restrictions. Do not change or restart the runtime checkout. "
        "Ask the user for this task's objective if it has not been provided."
    )
    result = subprocess.run([claude, "--append-system-prompt", prompt], cwd=target)
    blocked = check(target, args.role)
    if blocked:
        print("Role scope violations after session:\n" + "\n".join(blocked), file=sys.stderr)
        return 1
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"dev-session: {exc}", file=sys.stderr)
        raise SystemExit(1)
