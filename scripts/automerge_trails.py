"""Merge Entire trails as soon as every blocking gate passes, so nobody has to approve or click merge.

    python3 scripts/automerge_trails.py               # one pass: merge every open trail that is ready
    python3 scripts/automerge_trails.py --wait 7      # wait for trail 7's runners, then merge it (agents run this)
    python3 scripts/automerge_trails.py --watch 60    # keep merging ready trails, checking every 60 seconds
    python3 scripts/automerge_trails.py --dry-run     # report what would merge, merge nothing

Needs the Entire CLI and the GitHub CLI (gh), both logged in. Entire's own auto-merge policy only accepts
"disabled" so far and the approvals gate is off for this repo, so this script is the merge step. A trail merges
only when Entire itself reports it mergeable: agent review done for the current head with no open high-severity
findings, up to date with main, CI checks passing. A trail that is only behind main gets main merged in and is
merged on a later pass, after the runners have reviewed the new head. The merge pins the head SHA that was
evaluated, so a push that lands in between is never merged unreviewed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JURISDICTIONS = ("us", "eu")  # trails live on one cell; find it instead of hardcoding


class EntireError(RuntimeError):
    pass


def entire(*args: str) -> str:
    p = subprocess.run(["entire", *args], cwd=REPO, capture_output=True, text=True)
    if p.returncode != 0:
        raise EntireError((p.stderr or p.stdout).strip() or f"entire {args[0]} exited {p.returncode}")
    return p.stdout


def api(jurisdiction: str, path: str, method: str = "GET", **fields: str) -> dict:
    args = ["api", "--jurisdiction", jurisdiction, "-X", method, path]
    for key, value in fields.items():
        args += ["-F", f"{key}={value}"]
    out = json.loads(entire(*args) or "{}")
    if isinstance(out, dict) and ("error" in out or (isinstance(out.get("status"), int) and out["status"] >= 400)):
        raise EntireError(out.get("error") or out.get("detail") or json.dumps(out))
    return out


def locate() -> tuple[str, str]:
    """(jurisdiction, repo_id) of the cell that serves this repo's trails."""
    for j in JURISDICTIONS:
        try:
            gates = api(j, "/api/v1/trails/gh/{owner}/{repo}/gates")["gates"]
        except EntireError:
            continue
        if gates:
            return j, gates[0]["repoId"]
    raise EntireError("no Entire cell serves trails for this repo; is it mirrored and are trails enabled?")


def open_trails() -> list[dict]:
    return json.loads(entire("trail", "list", "--status", "open", "--json", "--limit", "100") or "[]")


def blockers(m: dict) -> list[str]:
    return [f"{g['gate_key']} {g.get('status')}: {g.get('rationale', '')}"
            for g in m.get("gates", []) if g.get("blocking") and g.get("status") not in ("passed", "skipped")]


def gh(*args: str) -> str:
    p = subprocess.run(["gh", *args], cwd=REPO, capture_output=True, text=True)
    if p.returncode != 0:
        raise EntireError(f"gh {args[0]} {args[1]}: {(p.stderr or p.stdout).strip()}")
    return p.stdout


def try_merge(where: tuple[str, str], trail: dict, dry_run: bool) -> tuple[str, str]:
    """('merged' | 'ready' | 'updated' | 'waiting' | 'blocked', detail) for one trail.

    Entire decides readiness; GitHub does the git work, because this repo is a GitHub mirror and a trail is
    mirrored as a pull request on its branch (merging the PR marks the trail merged).
    """
    j, repo_id = where
    branch = trail["branch"]
    m = api(j, f"/api/v1/repos/{repo_id}/trails/{trail['number']}/mergeability")
    if not m.get("mergeable"):
        found = blockers(m)
        if found and all(b.startswith("up_to_date failed") for b in found):
            # Only behind main: merge main into the branch; the runners review the new head and a later pass merges.
            if dry_run:
                return "behind", "would update the branch from main"
            gh("pr", "update-branch", branch)
            return "updated", "merged main into the branch; runners re-run on the new head"
        # A pending gate (review still running) settles on its own; a failed one needs a new push.
        state = "waiting" if not found or any(" pending" in b or " running" in b for b in found) else "blocked"
        return state, "; ".join(found) or f"not mergeable (conflicts: {m.get('conflict_status')})"
    if dry_run:
        return "ready", f"would merge head {m['head_sha'][:7]}"
    if json.loads(gh("pr", "view", branch, "--json", "isDraft"))["isDraft"]:
        gh("pr", "ready", branch)
    # --match-head-commit: merge exactly the head Entire evaluated, never a push that landed after it.
    gh("pr", "merge", branch, "--merge", "--match-head-commit", m["head_sha"])
    return "merged", f"head {m['head_sha'][:7]}"


def one_pass(where: tuple[str, str], dry_run: bool) -> int:
    trails = open_trails()
    if not trails:
        print("no open trails")
    merged = 0
    for t in trails:
        try:
            state, detail = try_merge(where, t, dry_run)
        except EntireError as e:
            state, detail = "error", str(e)
        merged += state == "merged"
        print(f"{state:8} #{t['number']} {t['title']}: {detail}", flush=True)
    return merged


def wait_for(where: tuple[str, str], number: int, timeout: int, dry_run: bool) -> int:
    trail = json.loads(entire("trail", "show", str(number), "--json"))
    deadline = time.monotonic() + timeout
    errors = 0
    while True:
        try:
            state, detail = try_merge(where, trail, dry_run)
            errors = 0
        except EntireError as e:  # e.g. the head moved between the gate check and the merge; look again
            errors += 1
            if errors >= 3:
                raise
            state, detail = "retrying", str(e)
        if state in ("merged", "ready", "behind"):
            print(f"{state} #{number}: {detail}")
            return 0
        if state == "blocked":
            print(f"blocked #{number}: {detail}", file=sys.stderr)
            return 1
        if time.monotonic() > deadline:
            print(f"timed out waiting for #{number}: {detail}", file=sys.stderr)
            return 1
        print(f"waiting #{number}: {detail}", flush=True)
        time.sleep(20)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--wait", type=int, metavar="TRAIL", help="wait for one trail to become mergeable, then merge it")
    mode.add_argument("--watch", type=int, metavar="SECONDS", help="repeat a pass over all open trails forever")
    p.add_argument("--timeout", type=int, default=1200, help="seconds --wait gives the runners (default 1200)")
    p.add_argument("--dry-run", action="store_true", help="report what would merge without merging")
    a = p.parse_args()

    try:
        where = locate()
        if a.wait is not None:
            return wait_for(where, a.wait, a.timeout, a.dry_run)
        while True:
            one_pass(where, a.dry_run)
            if a.watch is None:
                return 0
            time.sleep(a.watch)
    except EntireError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
