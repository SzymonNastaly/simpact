"""Check Model UN scenario files before a run, so a typo fails in a second instead of mid-session after paid model calls.

    python check_scenario.py                            # every scenario in scenarios/
    python check_scenario.py scenarios/my_topic.toml    # one or more files

Standard library only. Exits 1 if any file has problems.
"""

import re
import string
import sys
import tomllib
from pathlib import Path

DELEGATION_KEYS = ("name", "title", "public_position", "objectives", "red_lines", "style")
PHASE_KEYS = ("name", "instruction")
PLACEHOLDERS = ("agenda", "committee", "drafts")  # what model_un.py passes to str.format for each phase instruction
NAME = re.compile(r"[\w\-.]+")  # delegation names become agent IDs and inbox topic names


def text_problems(table: dict, keys: tuple[str, ...], where: str) -> list[str]:
    return [f"{where}: missing or empty {key!r}" for key in keys
            if not isinstance(table.get(key), str) or not table[key].strip()]


def placeholder_problems(instruction: str, where: str) -> list[str]:
    try:
        fields = [field for _, field, _, _ in string.Formatter().parse(instruction) if field is not None]
    except ValueError as e:
        return [f"{where}: {e}; write {{{{ or }}}} for a literal brace"]
    # Exact names only: {agenda.x} or {agenda[0]} would pass a prefix check and still raise inside str.format.
    allowed = ", ".join(f"{{{p}}}" for p in PLACEHOLDERS)
    return [f"{where}: unknown placeholder {{{field}}}; use one of {allowed}"
            + ("" if field.isidentifier() else ", or write {{ and }} for literal braces (e.g. JSON)")
            for field in fields if field not in PLACEHOLDERS]


def problems(sc: dict) -> list[str]:
    """Everything wrong with a parsed scenario, as readable messages. Empty means the scenario is runnable."""
    out = text_problems(sc, ("committee", "agenda"), "scenario")
    hops = sc.get("max_hops", 4)
    if isinstance(hops, bool) or not isinstance(hops, int) or hops < 1:
        out.append(f"scenario: max_hops must be a whole number >= 1, got {hops!r}")

    delegations = sc.get("delegations")
    if not isinstance(delegations, list) or len(delegations) < 2:
        out.append("scenario: needs at least two [[delegations]] to negotiate")
        delegations = delegations if isinstance(delegations, list) else []
    seen = set()
    for i, d in enumerate(delegations, 1):
        if not isinstance(d, dict):
            out.append(f"delegation {i}: must be a [[delegations]] table")
            continue
        where = f"delegation {i}" + (f" ({d['name']})" if isinstance(d.get("name"), str) else "")
        out += text_problems(d, DELEGATION_KEYS, where)
        name = d.get("name")
        if isinstance(name, str) and name.strip():
            if not NAME.fullmatch(name):
                out.append(f"{where}: name may only use letters, digits, '_', '-' and '.' (e.g. Saudi_Arabia)")
            if name in seen:
                out.append(f"{where}: duplicate name {name!r}")
            seen.add(name)

    phases = sc.get("phases")
    if not isinstance(phases, list) or not phases:
        out.append("scenario: needs at least one [[phases]]")
        phases = []
    for i, phase in enumerate(phases, 1):
        if not isinstance(phase, dict):
            out.append(f"phase {i}: must be a [[phases]] table")
            continue
        where = f"phase {i}" + (f" ({phase['name']})" if isinstance(phase.get("name"), str) else "")
        out += text_problems(phase, PHASE_KEYS, where)
        if isinstance(phase.get("instruction"), str):
            out += placeholder_problems(phase["instruction"], where)
    return out


def check_file(path: Path) -> list[str]:
    try:
        sc = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return [f"cannot read: {e}"]
    return problems(sc)


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or sorted(Path("scenarios").glob("*.toml"))
    if not paths:
        print("no scenario files found", file=sys.stderr)
        return 1
    failed = 0
    for path in paths:
        if found := check_file(path):
            failed += 1
            print(f"FAIL {path}")
            for p in found:
                print(f"  - {p}")
        else:
            print(f"ok   {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
