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
import time
from contextlib import closing, contextmanager
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
LOCK_RETRIES = 4
LOCK_RETRY_DELAY = 1.0
# An index.lock older than this is taken to be left by a Git that crashed or was killed;
# the dashboard's own Git runner uses the same age.
STALE_LOCK_SECONDS = GIT_TIMEOUT + 60


class UpdateRefused(RuntimeError):
    """The checkout is in a state that an update must not touch. Nothing was changed."""


class LocalChangesConflict(Exception):
    """The stashed local changes did not apply on the new commit."""

    def __init__(self, paths: list[str], details: str):
        super().__init__(details)
        self.paths = paths


class LocalChangesNotReapplied(Exception):
    """Git could not reapply the stashed local changes for a reason other than a conflict."""


class ImportCheckFailed(Exception):
    """The new code failed its import check; the message is the error line."""


class UpdateRolledBack(Exception):
    """The update failed, and the checkout is back on the commit it started from."""

    def __init__(self, message: str, kind: str, warnings: list[str], conflicts: list[str]):
        super().__init__(message)
        self.kind = kind
        self.warnings = warnings
        self.conflicts = conflicts


class RollbackFailed(Exception):
    """The update failed, and the checkout could not be put back; the message says how to do it by hand."""


def info(message: str) -> None:
    print(f"[update] {message}")


def fail(message: str) -> int:
    print(f"[update] ERROR: {message}", file=sys.stderr)
    return 1


def run(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT, check: bool = False) -> subprocess.CompletedProcess:
    # Git writes file names in UTF-8 on Windows and as their own bytes elsewhere, but text=True
    # decodes output with the locale's code page. surrogateescape keeps a name that is not
    # UTF-8 as the same bytes for the file system calls that use it.
    decoding = {"encoding": "utf-8", "errors": "surrogateescape"} if args[0] == "git" else {"text": True}
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        timeout=timeout,
        **decoding,
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
            "or discard the conflicted version of a file with `git checkout HEAD -- <file>`, then try again."
        )
    git_dir = Path(run(["git", "rev-parse", "--absolute-git-dir"], repo, timeout=30, check=True).stdout.strip())
    for name, operation, abort in UNFINISHED_OPERATIONS:
        if (git_dir / name).exists():
            raise UpdateRefused(
                f"a Git {operation} is in progress in the checkout. Finish it or run `{abort}`, then try again."
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
    # On a case-insensitive file system a path that differs from a tracked path only in case,
    # such as the target of an upstream rename from Luna.md to luna.md, is the tracked file.
    folded = {path.casefold(): path for path in indexed if path}
    in_the_way = set()
    for path in filter(None, added.split("\0")):
        parts = path.split("/")
        for depth in range(1, len(parts) + 1):
            candidate = "/".join(parts[:depth])
            local = repo / candidate
            is_leaf = depth == len(parts)
            tracked = folded.get(candidate.casefold())
            if (candidate not in indexed and os.path.lexists(local) and (is_leaf or not local.is_dir())
                    and not (tracked and same_file(local, repo / tracked))):
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


def same_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def git_until_unlocked(repo: Path, args: list[str], timeout: int = 120, own_lock: bool = False) -> None:
    """Run a git command that must succeed, waiting up to LOCK_RETRIES times for an index.lock to go.

    A lock is removed only when own_lock says that this update killed the Git command that
    left it, or when it is older than STALE_LOCK_SECONDS. A Git that is still running, such
    as `git commit` with its editor open, keeps its lock, and the command then fails.
    """
    for attempt in range(LOCK_RETRIES + 1):
        result = run(["git", *args], repo, timeout=timeout)
        if result.returncode == 0:
            return
        if "index.lock" not in command_output(result) or attempt == LOCK_RETRIES:
            break
        if remove_dead_lock(repo, own_lock):
            own_lock = False
        else:
            time.sleep(LOCK_RETRY_DELAY)
    raise RuntimeError(f"git {' '.join(args)} failed: {command_output(result) or result.returncode}")


def remove_dead_lock(repo: Path, own_lock: bool) -> bool:
    """Remove index.lock when own_lock is set or the lock is older than STALE_LOCK_SECONDS.

    Return True when no lock is left, so that the command can run again at once.
    """
    lock = repo / run(["git", "rev-parse", "--git-path", "index.lock"], repo, timeout=30).stdout.strip()
    try:
        if not own_lock and time.time() - lock.stat().st_mtime <= STALE_LOCK_SECONDS:
            return False
        lock.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def stash_tip(repo: Path) -> str | None:
    """Return the commit of the newest stash entry, or None when the stash is empty."""
    result = run(["git", "rev-parse", "--verify", "--quiet", "refs/stash"], repo, timeout=30)
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise RuntimeError(f"Reading refs/stash failed: {command_output(result) or result.returncode}")
    return result.stdout.strip()


def drop_stash(repo: Path, sha: str) -> None:
    """Drop the stash entry whose commit is sha, wherever later entries moved it in the list."""
    listed = run(["git", "stash", "list", "--format=%gd %H"], repo, timeout=30, check=True).stdout
    for line in listed.splitlines():
        ref, _, entry_sha = line.partition(" ")
        if entry_sha.strip() == sha:
            run(["git", "stash", "drop", ref], repo, timeout=30, check=True)
            return


def requirements_changed(repo: Path, before: str, after: str) -> bool:
    """True when requirements.txt differs between the two commits, or when Git cannot tell."""
    return run(["git", "diff", "--quiet", before, after, "--", "requirements.txt"], repo, timeout=30).returncode != 0


def failure_report(error: BaseException) -> tuple[str, str, str]:
    """Return the log kind, the cause and the advice for an error that ended an update attempt."""
    if isinstance(error, LocalChangesConflict):
        changes = f"your local changes to {', '.join(error.paths)}" if error.paths else "your local changes"
        return ("local_changes_conflict", f"{changes} conflict with the new version",
                "Discard or move those changes, then update again.")
    if isinstance(error, LocalChangesNotReapplied):
        return ("reapply_failed", f"your local changes could not be reapplied on the new version ({error})",
                "Try the update again.")
    if isinstance(error, ImportCheckFailed):
        return ("import_failed", f"the new version failed its import check ({error})",
                "The bot keeps running the current version.")
    if isinstance(error, subprocess.TimeoutExpired):
        command = error.cmd if isinstance(error.cmd, str) else " ".join(map(str, error.cmd))
        return ("timeout", f"a step timed out ({command})", "Try the update again later.")
    return ("error", f"the update stopped with an error ({str(error) or type(error).__name__})",
            "Check the log, then try again.")


class UpdateAttempt:
    """What one update did to a checkout, so that it can be kept or undone as a whole.

    The caller records the snapshot, the stash and the backup branch as it creates them,
    sets moved before it moves HEAD, and ends the attempt with keep() or roll_back().
    """

    def __init__(self, repo: Path, before_head: str, target_head: str):
        self.repo = repo
        self.before_head = before_head
        self.target_head = target_head
        self.snapshot: str | None = None
        self.stash: dict | None = None
        self.backup_branch: str | None = None
        self.moved = False
        self.dependencies_installed = False

    @property
    def touched(self) -> bool:
        return bool(self.moved or self.stash or self.snapshot or self.backup_branch)

    @contextmanager
    def recording_stash(self):
        """Record the stash entry that the enclosed `git stash push` saved, also when the push fails.

        A push can save its entry and then fail to reset a file that another process holds
        open, so the entry holds changes that are no longer all in the worktree.
        """
        before = stash_tip(self.repo)
        try:
            yield
        finally:
            after = stash_tip(self.repo)
            if after and after != before:
                self.stash = {"sha": after}

    def reapply(self) -> None:
        """Apply the stashed local changes on the new commit.

        Raise LocalChangesConflict when they conflict with it, and LocalChangesNotReapplied
        when Git fails for another reason, such as a file that another process holds open.
        """
        if not self.stash:
            return
        try:
            git_until_unlocked(self.repo, ["stash", "apply", "--index", self.stash["sha"]])
        except RuntimeError as error:
            unmerged = run(["git", "diff", "--name-only", "--diff-filter=U", "-z"], self.repo, timeout=30).stdout
            paths = sorted(path for path in unmerged.split("\0") if path)
            # Staged changes that do not apply leave no unmerged path; Git reports "conflicts in index".
            if paths or "conflict" in str(error).lower():
                raise LocalChangesConflict(paths, str(error)) from error
            raise LocalChangesNotReapplied(str(error)) from error

    def keep(self) -> list[str]:
        """Drop the stash entry of the reapplied changes and prune older snapshots; return warnings."""
        warnings = []
        if self.stash:
            try:
                drop_stash(self.repo, self.stash["sha"])
            except Exception as e:
                warnings.append(f"The stash entry {self.stash['sha']} of your local changes could not be dropped: {e}")
        if self.snapshot:
            prune_backups(Path(self.snapshot).parent)
        return warnings

    def roll_back(self, error: BaseException, reinstall=None):
        """Put the checkout back on before_head with the local changes, then raise UpdateRolledBack.

        The stash was taken on before_head, so reapplying it there cannot conflict. When HEAD
        or the worktree cannot be restored, this raises RollbackFailed instead and keeps the
        stash entry, the backup branch and the snapshot for a restore by hand.
        """
        kind, cause, advice = failure_report(error)
        # A Git command that timed out was killed by this update and can leave its index.lock
        # behind. The reset is the first command to meet that lock, so only the reset may remove it.
        own_lock = (isinstance(error, subprocess.TimeoutExpired) and isinstance(error.cmd, list)
                    and error.cmd[:1] == ["git"])
        try:
            if self.moved or self.stash:
                git_until_unlocked(self.repo, ["reset", "--hard", self.before_head], own_lock=own_lock)
            if self.stash:
                git_until_unlocked(self.repo, ["stash", "apply", "--index", self.stash["sha"]])
        except Exception as rollback_error:
            raise RollbackFailed(
                f"{cause}, and the checkout could not be restored ({rollback_error}). {self.recovery_steps()}"
            ) from error
        warnings = self._discard(reinstall)
        place = " with your local changes in place" if self.stash else ""
        raise UpdateRolledBack(
            f"{cause}. The checkout is back at {self.before_head[:7]}{place}. {advice}",
            kind, warnings, getattr(error, "paths", []),
        ) from error

    def recovery_steps(self) -> str:
        commands = [f"git reset --hard {self.before_head}"]
        if self.stash:
            commands.append(f"git stash apply --index {self.stash['sha']}")
        return "To restore it, run in the Discord Pals folder " + ", then ".join(f"`{c}`" for c in commands) + "."

    def _discard(self, reinstall) -> list[str]:
        """Remove what this attempt created once the checkout is restored; return warnings."""
        warnings = []
        cleanups = []
        if self.stash:
            cleanups.append((f"the stash entry {self.stash['sha']}", lambda: drop_stash(self.repo, self.stash["sha"])))
        if self.backup_branch:
            cleanups.append((f"the branch {self.backup_branch}",
                             lambda: run(["git", "branch", "-D", self.backup_branch], self.repo, timeout=30, check=True)))
        for label, cleanup in cleanups:
            try:
                cleanup()
            except Exception as e:
                warnings.append(f"Could not remove {label}: {e}")
        if self.snapshot:
            shutil.rmtree(self.snapshot, ignore_errors=True)
        if reinstall and self.dependencies_installed:
            problem = None
            try:
                if requirements_changed(self.repo, self.before_head, self.target_head):
                    problem = reinstall().get("warning")
            except Exception as e:
                problem = str(e)
            if problem:
                warnings.append(f"The requirements of the restored version were not fully reinstalled ({problem}). "
                                "A reinstall restores only versions pinned with ==, so the installed packages may be "
                                "newer than the code expects.")
        return warnings


def check_imports(repo: Path, python: str, timeout: int = IMPORT_CHECK_TIMEOUT) -> str | None:
    """Import the app from repo in a new interpreter; return the last error line, or None.

    The interpreter runs in an empty temporary directory. Importing the app creates and
    rewrites state files under relative paths such as bot_data/, and in the checkout that
    would race with the running process over those files. A check that cannot run returns
    its reason like an import error.
    """
    imports = "; ".join(f"import {name}" for name in IMPORT_CHECK_MODULES)
    try:
        with tempfile.TemporaryDirectory(prefix="discord-pals-import-check-", ignore_cleanup_errors=True) as scratch:
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
        return f"the import check could not run: {e}"
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
