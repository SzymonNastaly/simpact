"""Score the lint health of a branch: ruff violations it adds to the Python files it changes.

Only violations the branch introduces count, so a one-line edit to an old file is
not charged for that file's existing issues. Each (file, rule) pair is compared
between the merge base and HEAD. The last line of output is the JSON object the
trail-lint runner reports:

    {"value": <0-100>, "rationale": "..."}

    python3 scripts/lint_score.py --base origin/main
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

RUFF_VERSION = "0.6.9"
# Bugs, complexity and dead weight. ARG is left out: AutoGen handlers and
# Concordia components must accept arguments their framework passes, used or not.
RULES = "F,E4,E7,E9,B,BLE,C90,SIM,ERA,UP,PIE,RET,PLR0911,PLR0912,PLR0913,PLR0915"
# Likely bugs: pyflakes, syntax errors, bare except, bugbear and blind except.
SEVERE_PREFIXES = ("F", "E9", "E722", "B")
SEVERE_PENALTY = 10
OTHER_PENALTY = 4
EXAMPLES = 3


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def ruff_command(scratch: Path) -> list[str]:
    if shutil.which("uvx"):
        return ["uvx", f"ruff@{RUFF_VERSION}"]
    if shutil.which("ruff"):
        return ["ruff"]
    target = scratch / "ruff-pkg"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", str(target), f"ruff=={RUFF_VERSION}"],
        check=True,
    )
    return [str(target / "bin" / "ruff")]


def export(rev: str, paths: list[str], dest: Path) -> None:
    """Write each path as it is at rev into dest, skipping paths that don't exist there."""
    for path in paths:
        try:
            content = git("show", f"{rev}:{path}")
        except subprocess.CalledProcessError:
            continue
        out = dest / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)


def lint(ruff: list[str], root: Path) -> list[dict]:
    if not any(root.rglob("*.py")):
        return []
    proc = subprocess.run(
        [*ruff, "check", "--isolated", "--no-cache", "--exit-zero", "--target-version", "py312",
         "--select", RULES, "--output-format", "json", "."],
        cwd=root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ruff failed: {proc.stderr.strip()}")
    violations = json.loads(proc.stdout)
    for item in violations:
        item["path"] = Path(item["filename"]).resolve().relative_to(root.resolve()).as_posix()
    return violations


def score(base: str) -> dict:
    merge_base = git("merge-base", base, "HEAD").strip()
    changed = git("diff", "--name-only", "--diff-filter=AMR", f"{merge_base}..HEAD", "--", "*.py").split()
    if not changed:
        return {"value": 100, "rationale": "No Python files changed, so there is nothing to lint."}

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        ruff = ruff_command(scratch)
        export(merge_base, changed, scratch / "base")
        export("HEAD", changed, scratch / "head")
        before = lint(ruff, scratch / "base") if (scratch / "base").exists() else []
        after = lint(ruff, scratch / "head")

    count_before = Counter((v["path"], v["code"]) for v in before)
    count_after = Counter((v["path"], v["code"]) for v in after)
    added = count_after - count_before
    fixed = sum((count_before - count_after).values())

    severe = sum(n for (_, code), n in added.items() if code.startswith(SEVERE_PREFIXES))
    other = sum(added.values()) - severe
    value = max(0, 100 - SEVERE_PENALTY * severe - OTHER_PENALTY * other)

    rationale = f"{plural(sum(added.values()), 'new ruff violation')} in {plural(len(changed), 'changed Python file')}"
    if severe:
        rationale += f", {plural(severe, 'likely bug')}"
    if added:
        first = {}
        for v in after:
            first.setdefault((v["path"], v["code"]), v)
        examples = [
            f"{code} {path}:{first[path, code]['location']['row']} ({first[path, code]['message']})"
            for (path, code), _ in added.most_common(EXAMPLES)
        ]
        rationale += ": " + "; ".join(examples)
        if len(added) > EXAMPLES:
            rationale += f"; and {len(added) - EXAMPLES} more"
    if fixed:
        rationale += f". Fixed {plural(fixed, 'existing violation')}"
    return {"value": value, "rationale": rationale + f". ruff {RUFF_VERSION}."}


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' * (n != 1)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main", help="branch to compare against (default: origin/main)")
    result = score(parser.parse_args().base)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
