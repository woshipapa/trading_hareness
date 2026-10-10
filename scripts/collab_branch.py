#!/usr/bin/env python3
"""与 owner 方共用的协作分支 collab/owner-peer：试合并检查、合并进 main、把 main 同步过去。

双方都往 ``collab/owner-peer`` 提交，发布只从 ``main`` 出（RELEASE_SYNC_47）。这个私有仓库
没有分支保护（需要 GitHub Pro），服务端拦不住强推和改写，所以合并是否正确由本脚本把关：

    python3 scripts/collab_branch.py check     只读：在临时工作树里试合并，跑全部检查
    python3 scripts/collab_branch.py merge     check 全过才合并进 main，再把协作分支快进到 main
    python3 scripts/collab_branch.py sync      把 main 合并进协作分支（合并提交，从不 rebase）

check 依次检查，任何一项不过都不合并：

1. 历史：上次合并进 main 的协作分支提交仍在协作分支上，即协作分支没被强推改写；
2. 冲突：main 与协作分支能否无冲突合并；
3. 迁移：合并结果的迁移图只有一个 head、编号不重复、每个前序都存在；main 上已有的迁移
   文件不能改，也不能删；
4. 文件：协作分支带来的改动里不能有 .env、密钥、证书这类文件；
5. 仓库检查：架构检查通过，架构索引与脚本目录是最新的；改动的 Python 文件跑一遍 ruff，
   只作提示；
6. 测试：quant-service 全量测试，用本检出的 collab_isolated_tests.sh 隔离运行：不带密钥、
   不连外网、一次性数据库。

所有改动都在临时工作树里做，不碰本地检出，因为别的代理也在用它。推送从不强推：合并或
同步期间 main、协作分支被别人推进时，推送会被拒，脚本说明原因后退出，重跑即可。
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "origin"
MAIN = "main"
COLLAB = "collab/owner-peer"
MIGRATIONS = "quant-service/migrations/versions"
MERGE_PREFIX = f"merge({COLLAB})"
SYNC_PREFIX = f"sync(main -> {COLLAB})"
#: Files that must never travel through the branch. A basename is checked.
FORBIDDEN = (".env", ".env.*", "*.env", "*-secrets.env", "*.pem", "*.key", "*.p12", "*.pfx",
             "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.kdbx")
ALLOWED = (".env.example", "*.env.example", "env.example")
TEST_TIMEOUT_SECONDS = 1800
#: Our own copy runs the suite, never the one inside the tree under test.
ISOLATED_RUNNER = Path(__file__).resolve().parent / "collab_isolated_tests.sh"


class GitError(RuntimeError):
    pass


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result


# -- pure checks ----------------------------------------------------------------

def parse_revision(source: str) -> tuple[str | None, tuple[str, ...]]:
    """An Alembic file's ``revision`` and its ``down_revision`` parents."""
    revision: str | None = None
    parents: tuple[str, ...] = ()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id in ("revision", "down_revision")):
                continue
            parsed = ast.literal_eval(value)
            if target.id == "revision":
                revision = parsed
            else:
                items = parsed if isinstance(parsed, (tuple, list)) else (parsed,)
                parents = tuple(item for item in items if item)
    return revision, parents


def migration_problems(sources: dict[str, str]) -> list[str]:
    """Why a set of migration files is not one clean chain; empty when it is."""
    problems: list[str] = []
    revisions: dict[str, tuple[str, tuple[str, ...]]] = {}
    for name, source in sorted(sources.items()):
        try:
            revision, parents = parse_revision(source)
        except (SyntaxError, ValueError) as error:
            problems.append(f"{name}: revision unreadable ({error})")
            continue
        if not revision:
            problems.append(f"{name}: no revision id")
            continue
        if revision in revisions:
            problems.append(f"duplicate revision {revision}: {revisions[revision][0]} and {name}")
            continue
        revisions[revision] = (name, parents)
    for _revision, (name, parents) in sorted(revisions.items()):
        for parent in parents:
            if parent not in revisions:
                problems.append(f"{name}: down_revision {parent} is not in the chain")
    referenced = {parent for _name, parents in revisions.values() for parent in parents}
    heads = sorted(revision for revision in revisions if revision not in referenced)
    if revisions and len(heads) != 1:
        problems.append(f"{len(heads)} heads ({', '.join(heads) or 'none: the chain loops'}); it must end in one")
    state: dict[str, int] = {}

    def visit(revision: str) -> bool:
        state[revision] = 1
        for parent in revisions[revision][1]:
            if parent in revisions and (state.get(parent) == 1 or (parent not in state and visit(parent))):
                return True
        state[revision] = 2
        return False

    if any(revision not in state and visit(revision) for revision in sorted(revisions)):
        problems.append("the chain has a cycle")
    return problems


def edited_migrations(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Migration files from ``before`` that ``after`` changed or dropped; applied ones never change."""
    return sorted(name for name, source in before.items() if after.get(name) != source)


def forbidden_paths(paths: Sequence[str]) -> list[str]:
    def blocked(path: str) -> bool:
        name = Path(path).name
        if any(fnmatch.fnmatch(name, pattern) for pattern in ALLOWED):
            return False
        return any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN)
    return sorted(path for path in paths if blocked(path))


# -- repository helpers -----------------------------------------------------------

def ref(name: str) -> str:
    return f"{REMOTE}/{name}"


def tree_migrations(revision: str, cwd: Path) -> dict[str, str]:
    names = git("ls-tree", "-r", "--name-only", revision, "--", MIGRATIONS, cwd=cwd).stdout.split()
    return {Path(name).name: git("show", f"{revision}:{name}", cwd=cwd).stdout
            for name in names if name.endswith(".py") and not Path(name).name.startswith("__")}


def disk_migrations(tree: Path) -> dict[str, str]:
    folder = tree / MIGRATIONS
    if not folder.is_dir():
        return {}
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.py"))
            if not path.name.startswith("__")}


def is_ancestor(older: str, newer: str, cwd: Path) -> bool:
    return git("merge-base", "--is-ancestor", older, newer, cwd=cwd, check=False).returncode == 0


def last_merged_collab_tip(main: str, cwd: Path) -> str | None:
    """The collab commit that the latest merge into main took in, if there has been one."""
    parents = git("log", "--merges", "--first-parent", "--fixed-strings", f"--grep={MERGE_PREFIX}",
                  "--format=%P", "-1", main, cwd=cwd).stdout.split()
    return parents[1] if len(parents) >= 2 else None


@contextmanager
def worktree(start: str, cwd: Path) -> Iterator[Path]:
    holder = Path(tempfile.mkdtemp(prefix="collab-branch-"))
    tree = holder / "tree"
    git("worktree", "add", "--detach", str(tree), start, cwd=cwd)
    try:
        yield tree
    finally:
        git("worktree", "remove", "--force", str(tree), cwd=cwd, check=False)
        git("worktree", "prune", cwd=cwd, check=False)
        shutil.rmtree(holder, ignore_errors=True)


def run(command: Sequence[str], cwd: Path, timeout: int = 900) -> tuple[bool, str]:
    try:
        result = subprocess.run(list(command), cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"{type(error).__name__}: {error}"
    text = (result.stdout + result.stderr).strip()
    return result.returncode == 0, text


# -- the check ----------------------------------------------------------------------

@dataclass
class Report:
    checks: list[tuple[str, str, str]] = field(default_factory=list)   # (name, PASS|FAIL|WARN|SKIP, detail)

    def add(self, name: str, ok: bool, detail: str = "", *, warn_only: bool = False) -> None:
        self.checks.append((name, "PASS" if ok else ("WARN" if warn_only else "FAIL"), detail))

    def skip(self, name: str, why: str) -> None:
        self.checks.append((name, "SKIP", why))

    @property
    def ok(self) -> bool:
        return all(status != "FAIL" for _name, status, _detail in self.checks)

    def render(self) -> str:
        lines = [f"[{status}] {name}" + (f": {detail}" if detail else "") for name, status, detail in self.checks]
        lines.append("RESULT: " + ("all checks passed" if self.ok else "blocked, nothing merged"))
        return "\n".join(lines)


#: Files a generator writes from the whole tree. Both sides regenerate them, so they conflict whenever
#: both add modules (2026-10-10: docs/ARCHITECTURE_INDEX.md). Such a conflict is resolved by running the
#: generator on the merged tree; a conflict anywhere else still blocks.
GENERATED = {
    "docs/ARCHITECTURE_INDEX.md": ("scripts/generate_architecture_index.py",),
    "scripts/CATALOG.md": ("scripts/generate_scripts_catalog.py",),
}


def resolve_generated(tree: Path, conflicts: Sequence[str]) -> tuple[list[str], list[str]]:
    """Regenerate conflicted generated files once nothing else conflicts; returns (regenerated, still conflicted)."""
    if not conflicts or any(path not in GENERATED for path in conflicts):
        return [], list(conflicts)
    regenerated = []
    for path in conflicts:
        git("checkout", "--theirs", "--", path, cwd=tree)     # either side: the generator rewrites it whole
        ok, _text = run([sys.executable, *GENERATED[path]], tree)
        if not ok:
            return regenerated, [item for item in conflicts if item not in regenerated]
        git("add", "--", path, cwd=tree)
        regenerated.append(path)
    return regenerated, []


def merge_attempt(tree: Path, other: str, report: Report) -> bool:
    """Stage the merge of ``other`` into ``tree`` (uncommitted); True when nothing is left conflicted."""
    attempt = git("merge", "--no-ff", "--no-commit", other, cwd=tree, check=False)
    conflicts = git("diff", "--name-only", "--diff-filter=U", cwd=tree).stdout.split()
    regenerated, conflicts = resolve_generated(tree, conflicts)
    clean = not conflicts and (attempt.returncode == 0 or bool(regenerated))
    detail = ", ".join(conflicts) or ("" if clean else (attempt.stderr or attempt.stdout).strip()[-300:])
    report.add("merges without conflicts", clean, detail or (f"regenerated {', '.join(regenerated)}" if regenerated else ""))
    return clean


def regenerated_differs(tree: Path, path: str) -> bool:
    """Whether a regenerated file now differs from the trial merge's version of it.

    The trial merge is staged, not committed, so ``git status`` lists every file the
    branch changed as modified. Only a difference between the working tree and the
    index means the generator disagreed with what the merge brought in (2026-10-09:
    the owner side's own regenerated index was reported stale).
    """
    return git("diff", "--quiet", "--", path, cwd=tree, check=False).returncode != 0


def repo_checks(tree: Path, changed: Sequence[str], report: Report) -> None:
    ok, text = run([sys.executable, "scripts/verify_architecture.py"], tree)
    report.add("architecture check", ok, "" if ok else text[-400:])
    ok, text = run([sys.executable, "scripts/generate_architecture_index.py"], tree)
    stale = regenerated_differs(tree, "docs/ARCHITECTURE_INDEX.md")
    report.add("architecture index up to date", ok and not stale,
               "" if ok and not stale else (text[-300:] if not ok else "regenerate docs/ARCHITECTURE_INDEX.md on the branch"))
    if (tree / "scripts" / "generate_scripts_catalog.py").exists():
        ok, text = run([sys.executable, "scripts/generate_scripts_catalog.py", "--check"], tree)
        report.add("scripts catalog up to date", ok, "" if ok else text[-300:])
    python_files = [path for path in changed if path.endswith(".py") and (tree / path).exists()]
    if python_files:
        ok, text = run([sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "concise", *python_files], tree)
        report.add("ruff on changed Python files", ok, "" if ok else text[-500:], warn_only=True)


def test_suite(tree: Path, report: Report) -> None:
    """The quant suite on the merged tree, with this checkout's runner: no secrets, no internet."""
    if not (tree / "quant-service" / "database_bootstrap.py").exists():
        report.skip("quant-service tests", "no quant-service in the tree")
        return
    ok, text = run(["bash", str(ISOLATED_RUNNER), str(tree)], tree, timeout=TEST_TIMEOUT_SECONDS)
    summary = " ".join(line for line in text.splitlines() if line.startswith(("Ran ", "OK", "FAILED")))
    report.add("quant-service tests (isolated)", ok, summary or text[-500:])


def check_merge(tree: Path, cwd: Path, report: Report, *, skip_tests: bool, skip_repo_checks: bool) -> bool:
    """Stage the merge of collab into main inside ``tree`` (left uncommitted) and run every gate."""
    main, collab = ref(MAIN), ref(COLLAB)
    merged_tip = last_merged_collab_tip(main, cwd)
    report.add("history not rewritten", merged_tip is None or is_ancestor(merged_tip, collab, cwd),
               "" if merged_tip is None or is_ancestor(merged_tip, collab, cwd)
               else f"{merged_tip[:12]}, merged into main earlier, is no longer on {COLLAB}: someone force-pushed")
    incoming = git("rev-list", "--count", f"{main}..{collab}", cwd=cwd).stdout.strip()
    if incoming == "0":
        report.skip("merge", f"{COLLAB} has nothing that main lacks")
        return False
    if not merge_attempt(tree, collab, report):
        return False
    problems = migration_problems(disk_migrations(tree))
    report.add("migration chain: one head, unique, resolvable", not problems, "; ".join(problems))
    edited = edited_migrations(tree_migrations(main, cwd), disk_migrations(tree))
    report.add("migrations on main left untouched", not edited, ", ".join(edited))
    changed = git("diff", "--name-only", f"{main}...{collab}", cwd=cwd).stdout.split()
    blocked = forbidden_paths(changed)
    report.add("no secret or env files", not blocked, ", ".join(blocked))
    if skip_repo_checks:
        report.skip("repository checks", "--skip-repo-checks")
    else:
        repo_checks(tree, changed, report)
    if skip_tests:
        report.skip("quant-service tests", "--skip-tests")
    elif report.ok:
        test_suite(tree, report)
    else:
        report.skip("quant-service tests", "an earlier check failed")
    return True


def message(subject: str, lines: Sequence[str], trailers: Sequence[str]) -> str:
    body = "\n".join(lines[:40]) + (f"\n... and {len(lines) - 40} more" if len(lines) > 40 else "")
    tail = ("\n\n" + "\n".join(trailers)) if trailers else ""
    return f"{subject}\n\n{body}{tail}\n"


def push(tree: Path, target: str) -> tuple[bool, str]:
    result = git("push", REMOTE, f"HEAD:refs/heads/{target}", cwd=tree, check=False)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


# -- commands -------------------------------------------------------------------------

def command_check(cwd: Path, args: argparse.Namespace, *, commit: bool) -> int:
    git("fetch", "--quiet", REMOTE, MAIN, COLLAB, cwd=cwd)
    report = Report()
    with worktree(ref(MAIN), cwd) as tree:
        staged = check_merge(tree, cwd, report, skip_tests=args.skip_tests, skip_repo_checks=args.skip_repo_checks)
        print(report.render())
        if not staged or not report.ok:
            return 0 if not staged and report.ok else 1
        if not commit:
            return 0
        log = git("log", "--format=- %h %s (%an)", f"{ref(MAIN)}..{ref(COLLAB)}", cwd=cwd).stdout.splitlines()
        subject = f"{MERGE_PREFIX}: {len(log)} commit(s) from the owner-peer branch"
        git("commit", "--quiet", "-m", message(subject, log, args.trailer), cwd=tree)
        ok, detail = push(tree, MAIN)
        if not ok:
            print(f"main was not updated (it moved during the check?): {detail}\nRun merge again.")
            return 2
        ok, detail = push(tree, COLLAB)
        print(f"merged into {MAIN} as {git('rev-parse', '--short', 'HEAD', cwd=tree).stdout.strip()}")
        if not ok:
            print(f"{COLLAB} got new commits meanwhile, so it was not fast-forwarded: {detail}\n"
                  "Main has the checked merge; run sync to bring it to the branch.")
            return 0
        print(f"{COLLAB} fast-forwarded to {MAIN}")
        return 0


def command_sync(cwd: Path, args: argparse.Namespace) -> int:
    git("fetch", "--quiet", REMOTE, MAIN, COLLAB, cwd=cwd)
    main, collab = ref(MAIN), ref(COLLAB)
    if is_ancestor(main, collab, cwd):
        print(f"{COLLAB} already contains {MAIN}")
        return 0
    report = Report()
    with worktree(collab, cwd) as tree:
        if is_ancestor(collab, main, cwd):
            # The branch has nothing of its own that main lacks, so a fast-forward is exact.
            git("merge", "--ff-only", "--quiet", main, cwd=tree)
            ok, detail = push(tree, COLLAB)
            print(f"{COLLAB} fast-forwarded to {MAIN}" if ok else f"push refused: {detail}")
            return 0 if ok else 2
        merge_attempt(tree, main, report)
        if report.ok:
            problems = migration_problems(disk_migrations(tree))
            report.add("migration chain: one head, unique, resolvable", not problems, "; ".join(problems))
            edited = edited_migrations(tree_migrations(main, cwd), disk_migrations(tree))
            edited += edited_migrations(tree_migrations(collab, cwd), disk_migrations(tree))
            report.add("existing migrations left untouched", not edited, ", ".join(sorted(set(edited))))
        if report.ok and not args.skip_tests:
            test_suite(tree, report)
        print(report.render())
        if not report.ok:
            print(f"Resolve on {COLLAB} with a merge commit (never a rebase), then run sync again.")
            return 1
        log = git("log", "--format=- %h %s", f"{collab}..{main}", cwd=cwd).stdout.splitlines()
        git("commit", "--quiet", "-m", message(f"{SYNC_PREFIX}: {len(log)} commit(s) from main", log, args.trailer),
            cwd=tree)
        ok, detail = push(tree, COLLAB)
        if not ok:
            print(f"{COLLAB} moved during the sync; nothing pushed: {detail}\nRun sync again.")
            return 2
        print(f"{COLLAB} now contains {MAIN} ({git('rev-parse', '--short', 'HEAD', cwd=tree).stdout.strip()})")
        return 0


def main(argv: Sequence[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "merge", "sync"))
    parser.add_argument("--skip-tests", action="store_true", help="skip the quant-service suite")
    parser.add_argument("--skip-repo-checks", action="store_true", help="skip architecture, catalog and lint checks")
    parser.add_argument("--trailer", action="append", default=[], help="trailer line for the merge commit")
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            return command_sync(root, args)
        return command_check(root, args, commit=args.command == "merge")
    except GitError as error:
        print(f"git failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
