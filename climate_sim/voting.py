"""Auditable ranked-choice voting with deterministic, declared tie breaking."""

from collections import Counter


def ranked_choice(ballots: dict[str, list[str]], candidates: list[str]) -> tuple[str, list[dict]]:
    expected = set(candidates)
    if not ballots or not expected:
        raise ValueError("An election needs candidates and voters")
    if any(len(ranking) != len(expected) or set(ranking) != expected for ranking in ballots.values()):
        raise ValueError("Each voter must rank every candidate exactly once")
    remaining = set(candidates)
    trace = []
    while remaining:
        counts = Counter(next(key for key in ranking if key in remaining) for ranking in ballots.values())
        trace.append({"remaining": sorted(remaining), "votes": dict(sorted(counts.items()))})
        winner = next((key for key, count in sorted(counts.items()) if count > len(ballots) / 2), None)
        if winner is not None or len(remaining) == 1:
            return winner or next(iter(remaining)), trace
        # In a tied elimination, the lexically last candidate is eliminated.
        # An even-size final tie is resolved by the same predeclared rule, not a fabricated majority.
        loser = max(remaining, key=lambda key: (-counts[key], key))
        trace[-1]["eliminated"] = loser
        remaining.remove(loser)
    raise AssertionError("unreachable")
