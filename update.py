#!/usr/bin/env python3
"""
Discord Pals standalone updater.

This bootstrap updater intentionally avoids importing Discord Pals runtime modules
so it can be downloaded into older installs and run with `python update.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path


BOOTSTRAP_VERSION = "1.0.0"
GIT_TIMEOUT = 180
PIP_TIMEOUT = 300
IMPORT_CHECK_TIMEOUT = 120
# main imports dashboard only when the bots start, so the check imports both.
IMPORT_CHECK_MODULES = ("main", "dashboard")
BACKUP_RETENTION = 3
# Each of the BACKUP_RETENTION snapshots would hold a full copy, so a larger database stays out.
PROJECT_DATABASE_SNAPSHOT_MAX_BYTES = 256 * 1024 * 1024
BACKUP_FILES = (".env", "bots.json", "providers.json")
BACKUP_DIRS = ("bot_data", "characters", "prompts")
# Rotated logs live under bot_data/logs and are already size-capped; copying them
# into every snapshot multiplied retained backups by the whole log budget.
BACKUP_DIR_IGNORES = {"bot_data": ("logs", "project_automation.sqlite3*")}
# (file in the Git directory, operation, command that abandons it)
UNFINISHED_OPERATIONS = (
    ("MERGE_HEAD", "merge", "git merge --abort"),
    ("rebase-merge", "rebase", "git rebase --abort"),
    ("rebase-apply", "rebase", "git rebase --abort"),
    ("CHERRY_PICK_HEAD", "cherry-pick", "git cherry-pick --abort"),
    ("REVERT_HEAD", "revert", "git revert --abort"),
)


class UpdateRefused(RuntimeError):
    """The checkout is in a state that an update must not touch. Nothing was changed."""


def info(message: str) -> None:
    print(f"[update] {message}")


def fail(message: str) -> int:
    print(f"[update] ERROR: {message}", file=sys.stderr)
    return 1


def run(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        details = (result.stderr or result.stdout or str(result.returncode)).strip()
        raise RuntimeError(f"{' '.join(args)} failed: {details}")
    return result


def command_output(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip()).strip()


def fetch_all_with_tag_recovery(repo: Path, remote: str) -> subprocess.CompletedProcess:
    result = run(["git", "fetch", "--all", "--tags", "--prune"], repo, timeout=GIT_TIMEOUT)
    if result.returncode == 0:
        return result

    output = command_output(result).lower()
    if "would clobber existing tag" not in output:
        return result

    retry = run(
        [
            "git",
            "fetch",
            "--prune",
            "--force",
            remote,
            f"+refs/heads/*:refs/remotes/{remote}/*",
            "+refs/tags/*:refs/tags/*",
        ],
        repo,
        timeout=GIT_TIMEOUT,
    )
    if retry.returncode == 0:
        retry.stdout = "\n".join(
            part for part in [
                command_output(result),
                command_output(retry),
                "Recovered from stale local release tags by forcing tag refresh.",
            ] if part
        )
        retry.stderr = ""
    return retry


def repo_root(start: Path) -> Path:
    result = run(["git", "rev-parse", "--show-toplevel"], start, timeout=30, check=True)
    return Path(result.stdout.strip() or start).resolve()


def read_file_version(repo: Path) -> str | None:
    version_file = repo / "version.py"
    if not version_file.exists():
        return None
    match = re.search(
        r'__version__\s*=\s*["\']([^"\']+)["\']',
        version_file.read_text(encoding="utf-8", errors="replace"),
    )
    return match.group(1) if match else None


def version_key(version: str) -> tuple[int, int, int] | None:
    parts = str(version or "").strip().lstrip("v").split(".")
    if len(parts) < 3 or not all(part.isdigit() for part in parts[:3]):
        return None
    return tuple(int(part) for part in parts[:3])


def newest_remote_tag(repo: Path, remote: str) -> str | None:
    result = run(["git", "ls-remote", "--tags", remote], repo, timeout=30)
    if result.returncode != 0:
        return None

    versions = set()
    for line in result.stdout.splitlines():
        _sha, _sep, ref = line.partition("\t")
        tag_name = ref.removeprefix("refs/tags/").removesuffix("^{}")
        clean = tag_name.lstrip("v")
        if version_key(clean) is not None:
            versions.add(clean)

    return max(versions, key=version_key) if versions else None


def select_remote(repo: Path) -> str:
    result = run(["git", "remote"], repo, timeout=30, check=True)
    remotes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not remotes:
        raise RuntimeError("No Git remotes are configured")
    return "origin" if "origin" in remotes else remotes[0]


def ref_exists(repo: Path, ref: str) -> bool:
    return run(["git", "rev-parse", "--verify", "--quiet", ref], repo, timeout=30).returncode == 0


def ref_sha(repo: Path, ref: str) -> str:
    result = run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], repo, timeout=30, check=True)
    return result.stdout.strip()


def tag_contains_version(repo: Path, ref: str, version: str) -> bool:
    result = run(["git", "show", f"{ref}:version.py"], repo, timeout=30)
    if result.returncode != 0:
        return False
    return re.search(r'__version__\s*=\s*["\']' + re.escape(version) + r'["\']', result.stdout) is not None


def version_tag_ref(repo: Path, version: str) -> str:
    for ref in (f"refs/tags/v{version}", f"refs/tags/{version}"):
        if ref_exists(repo, ref) and tag_contains_version(repo, ref, version):
            return ref
    raise RuntimeError(f"Could not find a fetched tag whose version.py contains {version}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = run(["git", "merge-base", "--is-ancestor", ancestor, descendant], repo, timeout=30)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError(command_output(result) or "merge-base failed")


def copy_backup_item(source: Path, destination: Path, extra_ignores: tuple[str, ...] = ()) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "update_backups", *extra_ignores),
        )
    else:
        shutil.copy2(source, destination)


def prune_backups(root: Path) -> None:
    if not root.exists():
        return
    backups = sorted(
        [path for path in root.iterdir() if path.is_dir() and path.name.startswith("pre-update-")],
        key=lambda path: path.name,
        reverse=True,
    )
    for backup in backups[BACKUP_RETENTION:]:
        shutil.rmtree(backup, ignore_errors=True)


def backup_project_database(source: Path, destination: Path) -> str | None:
    """Snapshot committed SQLite/WAL state without copying live journal files.

    Return a note for the update log when the database is over PROJECT_DATABASE_SNAPSHOT_MAX_BYTES
    and stays out of the snapshot, otherwise None.
    """
    if not source.is_file():
        return None
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        size = original.execute("PRAGMA page_count").fetchone()[0] * original.execute("PRAGMA page_size").fetchone()[0]
        if size > PROJECT_DATABASE_SNAPSHOT_MAX_BYTES:
            return (f"The update snapshot left out bot_data/project_automation.sqlite3 because it is larger than "
                    f"{PROJECT_DATABASE_SNAPSHOT_MAX_BYTES // (1024 * 1024)} MB. Back it up separately.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(destination))) as snapshot:
            # One step: SQLite restarts a step-wise copy after every write through another
            # connection, so with the worker writing a copy in steps might never finish:
            # https://www.sqlite.org/c3ref/backup_finish.html
            original.backup(snapshot)
    return None


def create_state_backup(repo: Path, warnings: list | None = None) -> Path | None:
    """Snapshot local state before an update; a note about a left-out database goes into warnings."""
    backup_root = repo / "bot_data" / "update_backups"
    backup_dir = backup_root / f"pre-update-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    copied = False

    for name in BACKUP_FILES:
        source = repo / name
        if source.exists():
            copy_backup_item(source, backup_dir / name)
            copied = True

    for name in BACKUP_DIRS:
        source = repo / name
        if source.exists():
            copy_backup_item(source, backup_dir / name, BACKUP_DIR_IGNORES.get(name, ()))
            copied = True

    if not copied:
        return None

    note = backup_project_database(repo / "bot_data" / "project_automation.sqlite3", backup_dir / "bot_data" / "project_automation.sqlite3")
    if note and warnings is not None:
        warnings.append(note)
    prune_backups(backup_root)
    return backup_dir


def preflight(repo: Path) -> None:
    """Raise UpdateRefused when the checkout holds unresolved conflicts or an unfinished operation.

    A stash fails on unmerged paths, and the reset fallback would discard a half-finished
    merge or rebase, so the update stops before it changes anything.
    """
    unmerged = run(["git", "ls-files", "--unmerged", "-z"], repo, timeout=30, check=True).stdout
    paths = sorted({entry.split("\t", 1)[1] for entry in unmerged.split("\0") if "\t" in entry})
    if paths:
        raise UpdateRefused(
            f"the checkout has unresolved conflicts in {', '.join(paths)}. Resolve them with Git, "
            "or discard the conflicted version of a file with `git checkout HEAD -- <file>`, then update again."
        )
    git_dir = Path(run(["git", "rev-parse", "--absolute-git-dir"], repo, timeout=30, check=True).stdout.strip())
    for name, operation, abort in UNFINISHED_OPERATIONS:
        if (git_dir / name).exists():
            raise UpdateRefused(
                f"a Git {operation} is in progress in the checkout. Finish it or run `{abort}`, then update again."
            )


def refuse_overwriting_untracked(repo: Path, head: str, target: str) -> None:
    """Raise UpdateRefused when target adds a path that exists in the worktree but not in the index.

    Such a file is untracked or ignored (the repository ignores characters/*.md). A
    fast-forward and a reset overwrite an ignored file without asking, and a rollback then
    deletes it, because the old commit does not track it.
    """
    added = run(["git", "diff", "--name-only", "--no-renames", "--diff-filter=A", "-z", head, target],
                repo, timeout=60, check=True).stdout
    indexed = set(run(["git", "ls-files", "-z"], repo, timeout=60, check=True).stdout.split("\0"))
    in_the_way = set()
    for path in filter(None, added.split("\0")):
        parts = path.split("/")
        for depth in range(1, len(parts) + 1):
            candidate = "/".join(parts[:depth])
            local = repo / candidate
            is_leaf = depth == len(parts)
            if candidate not in indexed and os.path.lexists(local) and (is_leaf or not local.is_dir()):
                in_the_way.add(candidate)
                break
    if not in_the_way:
        return
    paths = sorted(in_the_way)
    if len(paths) == 1:
        detail = f"{paths[0]} exists in the checkout but is not tracked by Git, and the update would overwrite it. Move or rename it"
    else:
        detail = (f"{', '.join(paths)} exist in the checkout but are not tracked by Git, and the update would "
                  "overwrite them. Move or rename them")
    raise UpdateRefused(f"{detail}, then try again.")


def restore_checkout(repo: Path, commit: str, stash_sha: str | None) -> None:
    """Put the checkout back on commit and reapply the stashed local changes.

    The stash was taken on commit, so reapplying it there cannot conflict. Its entry
    stays in the stash list as a second copy of the changes.
    """
    run(["git", "reset", "--hard", commit], repo, timeout=120, check=True)
    if stash_sha:
        run(["git", "stash", "apply", "--index", stash_sha], repo, timeout=120, check=True)


def check_imports(repo: Path, python: str, timeout: int = IMPORT_CHECK_TIMEOUT) -> str | None:
    """Import the app from repo in a new interpreter; return the last error line, or None.

    The interpreter runs in an empty temporary directory. Importing the app creates and
    rewrites state files under relative paths such as bot_data/, and in the checkout that
    would race with the running process over those files.
    """
    imports = "; ".join(f"import {name}" for name in IMPORT_CHECK_MODULES)
    with tempfile.TemporaryDirectory(prefix="discord-pals-import-check-") as scratch:
        try:
            result = subprocess.run(
                [python, "-c", f"import sys; sys.path.insert(0, sys.argv[1]); {imports}", str(repo)],
                cwd=scratch,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"importing the code took longer than {timeout} seconds"
        except OSError as e:
            return f"could not run {python}: {e}"
    if result.returncode == 0:
        return None
    lines = [line.strip() for line in command_output(result).splitlines() if line.strip()]
    return lines[-1] if lines else f"{python} exited with code {result.returncode}"


def tracked_status(repo: Path) -> str:
    result = run(["git", "status", "--porcelain", "--untracked-files=no"], repo, timeout=30, check=True)
    return result.stdout.strip()


def stash_tracked_changes(repo: Path) -> str | None:
    if not tracked_status(repo):
        return None
    message = f"discord-pals-bootstrap-update-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    run(["git", "stash", "push", "-m", message], repo, timeout=120, check=True)
    result = run(["git", "stash", "list", "--format=%gd%x00%s", "-n", "1"], repo, timeout=30)
    if result.returncode == 0 and result.stdout.strip():
        ref, _sep, subject = result.stdout.strip().partition("\x00")
        if message in subject and ref:
            return ref
    return "stash@{0}"


def restore_stash(repo: Path, stash_ref: str | None) -> str | None:
    if not stash_ref:
        return None
    result = run(["git", "stash", "pop", "--index", stash_ref], repo, timeout=120)
    if result.returncode == 0:
        return None
    return command_output(result) or f"Could not reapply {stash_ref}"


def backup_branch(repo: Path) -> str:
    name = f"dashboard-update-backup-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    run(["git", "branch", name, "HEAD"], repo, timeout=30, check=True)
    return name


def install_dependencies(repo: Path) -> dict:
    requirements = repo / "requirements.txt"
    if not requirements.exists():
        return {"status": "skipped", "warning": None}

    candidates = []
    if sys.executable:
        candidates.append([sys.executable, "-m", "pip"])
    candidates.extend([
        [str(repo / "venv" / "Scripts" / "pip.exe")],
        [str(repo / "venv" / "bin" / "pip")],
        ["python", "-m", "pip"],
        ["python3", "-m", "pip"],
        ["pip"],
    ])

    errors = []
    seen = set()
    for base in candidates:
        key = tuple(base)
        if key in seen:
            continue
        seen.add(key)
        command = base + ["install", "--disable-pip-version-check", "-r", str(requirements), "-q"]
        try:
            result = run(command, repo, timeout=PIP_TIMEOUT)
        except FileNotFoundError:
            errors.append(f"{' '.join(base)} was not found")
            continue
        if result.returncode == 0:
            return {"status": "ok", "warning": None}
        errors.append(f"{' '.join(base)}: {command_output(result) or result.returncode}")

    return {"status": "warning", "warning": "Dependency install failed: " + " | ".join(errors)}


def append_update_log(repo: Path, event: dict) -> None:
    log_path = repo / "bot_data" / "update_log.json"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        if log_path.exists():
            data = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries = data
        entries.append({"timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z", **event})
        log_path.write_text(json.dumps(entries[-50:], indent=2), encoding="utf-8")
    except Exception:
        pass


def log_update_failure(repo: Path, error: str, **fields) -> None:
    append_update_log(repo, {"status": "error", "error": error, **fields})


def perform_update(repo: Path, target_version: str) -> int:
    before_version = read_file_version(repo)
    target_ref = version_tag_ref(repo, target_version)
    before_head = ref_sha(repo, "HEAD")
    target_head = ref_sha(repo, target_ref)
    warnings = []
    # A run that leaves HEAD in place takes no snapshot, so it cannot rotate the last real
    # one out of the BACKUP_RETENTION kept.
    backup_path = create_state_backup(repo, warnings) if before_head != target_head else None
    stash_ref = stash_tracked_changes(repo)

    try:
        if before_head == target_head:
            info(f"Already at v{target_version}.")
        elif is_ancestor(repo, before_head, target_head):
            result = run(["git", "merge", "--ff-only", target_ref], repo, timeout=120)
            if result.returncode != 0:
                branch = backup_branch(repo)
                run(["git", "reset", "--hard", target_ref], repo, timeout=120, check=True)
                warnings.append(f"Fast-forward failed; previous HEAD saved as {branch}.")
        else:
            branch = backup_branch(repo)
            run(["git", "reset", "--hard", target_ref], repo, timeout=120, check=True)
            warnings.append(f"Previous HEAD saved as {branch}.")
    finally:
        restore_warning = restore_stash(repo, stash_ref)
        if restore_warning:
            warnings.append(restore_warning)

    dependency_result = install_dependencies(repo)
    if dependency_result.get("warning"):
        warnings.append(dependency_result["warning"])

    after_version = read_file_version(repo)
    after_head = ref_sha(repo, "HEAD")
    status = "ok" if after_version == target_version else "verification_failed"
    append_update_log(repo, {
        "status": status,
        "from_version": before_version,
        "file_version": after_version,
        "expected_version": target_version,
        "target_ref": target_ref,
        "updated": before_head != after_head,
        "before_head": before_head,
        "after_head": after_head,
        "dependency_status": dependency_result["status"],
        "warnings": warnings,
    })

    if backup_path:
        info(f"State backup: {backup_path}")
    for warning in warnings:
        info(f"Warning: {warning}")
    if after_version != target_version:
        return fail(f"Update verification failed. version.py is {after_version}, expected {target_version}.")

    info(f"Update complete. Restart Discord Pals to apply v{target_version}.")
    return 0


def main() -> int:
    repo = Path.cwd().resolve()
    current_version = None
    target_version = None
    try:
        repo = repo_root(repo)
        remote = select_remote(repo)
        info(f"Standalone updater {BOOTSTRAP_VERSION}")
        info(f"Repository: {repo}")
        info("Fetching updates and tags...")
        fetch = fetch_all_with_tag_recovery(repo, remote)
        if fetch.returncode != 0:
            details = command_output(fetch) or f"git fetch exited with code {fetch.returncode}"
            raise RuntimeError(f"Fetching updates failed: {details}")
        target_version = newest_remote_tag(repo, remote)
        if not target_version:
            log_update_failure(repo, "no_release_tag", from_version=current_version)
            return fail("Could not discover a semantic release tag from the Git remote.")
        current_version = read_file_version(repo)
        info(f"Current file version: {current_version or 'unknown'}")
        info(f"Latest release tag: v{target_version}")
        if current_version == target_version:
            append_update_log(repo, {
                "status": "ok",
                "from_version": current_version,
                "file_version": current_version,
                "expected_version": target_version,
                "target_ref": f"refs/tags/v{target_version}",
                "updated": False,
                "before_head": None,
                "after_head": None,
                "backup_path": None,
                "dependency_status": "skipped",
                "warnings": [],
            })
            info("Already up to date. Restart only if the dashboard shows an older running version.")
            return 0
        return perform_update(repo, target_version)
    except subprocess.TimeoutExpired:
        log_update_failure(repo, "timeout", from_version=current_version, expected_version=target_version)
        return fail("Command timed out during update.")
    except FileNotFoundError as e:
        log_update_failure(repo, "missing_command", command=e.filename, from_version=current_version, expected_version=target_version)
        return fail(f"Required command was not found: {e.filename}")
    except Exception as e:
        log_update_failure(repo, type(e).__name__, message=str(e), from_version=current_version, expected_version=target_version)
        return fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
