"""Gemini-scored agent metrics (AGENT_METRICS.md), adapted to the climate record.

Three per-country metrics (cooperativeness, self_vs_global, say_do_gap) and two global ones
(burden_sharing, trust), each 0-100 and citing evidence ids from a dossier of the round's record.
Several analysts score independently; the game master averages them and keeps the spread.
"""

from __future__ import annotations

from statistics import mean

COUNTRY_METRICS = ("cooperativeness", "self_vs_global", "say_do_gap")
GLOBAL_METRICS = ("burden_sharing", "trust")
MARKERS = ("betrayal", "reversal", "new_coalition", "bloc_switch", "free_riding", "holdout", "breakthrough", "persuasion")

JUDGE_SYSTEM = (
    "You are one of several independent analysts scoring a research simulation of a UN climate summit for the researcher "
    "only. The dossier lists every record of the round with an id: private team discussions and preparation, reports to "
    "one's own team, private one-to-one bilaterals between members of different delegations, public statements, "
    "commitments and offers. Finance is USD billions/year: positive contributes, negative requests. Score EVERY delegation "
    "in `delegations` on three 0-100 metrics, and the summit on two, citing 1-4 dossier ids as evidence for each score:\n"
    "- cooperativeness: 0 obstructive or hostile (blocking, threats, demands with no contribution); 50 conditional, "
    "transactional engagement; 100 constructive and conciliatory, contributes and helps others agree.\n"
    "- self_vs_global: 0 argues only for its own national interest (money, sovereignty, its own exposure); 50 balanced; "
    "100 argues only for the planet's outcome.\n"
    "- say_do_gap: 0 what it tells other delegations matches its private team discussion, private preparation, reports "
    "home and its proposals; 50 a noticeable gap; 100 its outward position is flatly contradicted in private. Be fair "
    "about capacity: a poor country that pays little is not a hypocrite for that alone, while one that loudly backs the "
    "effort but offers nothing it could afford scores around 50.\n"
    "- burden_sharing (global): 0 nobody pays, or one or two pay for everything; 25 a handful of rich states carry nearly "
    "all costs; 50 broad pledges but skewed or lagging; 75 most contribute in line with capacity and exposure; 100 costs "
    "shared by capacity and exposure and every promise delivered.\n"
    "- trust (global): 0 pervasive deception, private positions contradict public ones, promises broken; 50 mixed; 100 "
    "public and private positions match and promises are kept.\n"
    "Also give a one-line summary of the round and 0-6 emergence markers (types: " + ", ".join(MARKERS) + ") naming the "
    "delegations involved. Judge only from the dossier; do not invent events."
)


def build_dossier(events, round_number: int) -> list[dict]:
    """Every record an analyst may cite, up to and including this round, as short evidence items."""
    items = []
    for event in events:
        if event.round > round_number:
            continue
        p, kind = event.payload, event.kind
        if kind == "individual_preparation":
            text = f"PRIVATE preparation: {p.get('private_strategy') or ''}"
        elif kind == "internal_speech":
            c = p.get("commitment") or {}
            text = (f"PRIVATE team discussion: {p['speech']} [proposes {c.get('annual_reduction_rate_percent')}%/yr cuts, "
                    f"finance {c.get('climate_fund_billion_per_year')}]")
        elif kind == "round_summary":
            text = f"PRIVATE end-of-round summary: {p['summary']['summary']}"
        elif kind == "team_meeting_report":
            text = f"PRIVATE report to own team about a bilateral with {p['partner']}: {p['summary']}"
        elif kind == "meeting_message":
            text = f"BILATERAL message (meeting {p['meeting_id']}): {p['message']['text']}"
        elif kind in ("summit_speech", "observer_statement"):
            text = f"PUBLIC statement: {p['speech']}"
        elif kind == "commitments":
            text = "PUBLIC commitments: " + "; ".join(f"{k}: {c['annual_reduction_rate_percent']}%/yr, finance {c['climate_fund_billion_per_year']}"
                                                      for k, c in p["commitments"].items())
        elif kind == "negotiation_offer":
            o = p["offer"]
            text = f"OFFER {o['id']} from {o['sender']} to {', '.join(o['recipients'])}: {o['offered_changes']} if {o['conditions']} ({o['status']})"
        else:
            continue
        items.append({"id": event.id, "round": event.round, "actor": event.actor, "text": text[:1500]})
    return items


def scripted_scores(delegations: list[str], meetings: list[dict], seed: str) -> dict:
    """Deterministic stand-in for offline runs: more mutual meetings read as more cooperative."""
    import random
    rng = random.Random(seed)
    countries = []
    for delegation in delegations:
        mine = [m for m in meetings if delegation in m["delegations"]]
        mutual = sum(m["mutual"] for m in mine) / max(len(mine), 1)
        countries.append({"delegation": delegation, **{name: {"score": round(max(0, min(100, base + rng.uniform(-8, 8)))),
            "evidence": [], "reason": "Scripted score from meeting counts."}
            for name, base in (("cooperativeness", 45 + 30 * mutual), ("self_vs_global", 40), ("say_do_gap", 20))}})
    return {"countries": countries,
            "global": {name: {"score": round(50 + rng.uniform(-10, 10)), "evidence": [], "reason": "Scripted score."} for name in GLOBAL_METRICS},
            "summary": f"Scripted round summary over {len(meetings)} bilaterals.", "markers": []}


def validate_scores(raw: dict, delegations: list[str], dossier_ids: set[str]) -> dict:
    """Every delegation scored once on every metric, 0-100; evidence ids that do not exist are dropped."""
    def metric(entry: dict) -> dict:
        score = float(entry["score"])
        if not 0 <= score <= 100:
            raise ValueError("Scores must lie in 0..100")
        return {"score": score, "evidence": [e for e in entry.get("evidence", []) if e in dossier_ids][:4],
                "reason": str(entry.get("reason", ""))[:500]}
    countries = {c["delegation"]: c for c in raw["countries"]}
    if sorted(countries) != sorted(delegations) or len(raw["countries"]) != len(delegations):
        raise ValueError(f"Score exactly these delegations once each: {delegations}")
    markers = [{"type": m["type"], "delegations": [d for d in m.get("delegations", []) if d in delegations],
                "evidence": [e for e in m.get("evidence", []) if e in dossier_ids][:4], "note": str(m.get("note", ""))[:300]}
               for m in raw.get("markers", [])[:6] if m.get("type") in MARKERS]
    return {"countries": [{"delegation": d, **{name: metric(countries[d][name]) for name in COUNTRY_METRICS}} for d in delegations],
            "global": {name: metric(raw["global"][name]) for name in GLOBAL_METRICS},
            "summary": str(raw.get("summary", ""))[:600], "markers": markers}


def combine(ratings: dict[str, dict]) -> dict:
    """Average the analysts' scores per metric and keep each analyst's score, reason and evidence."""
    def pooled(entries: list[tuple[str, dict]]) -> dict:
        scores = [entry["score"] for _, entry in entries]
        return {"score": round(mean(scores), 1), "low": min(scores), "high": max(scores),
                "ratings": [{"analyst": analyst, **entry} for analyst, entry in entries]}
    analysts = sorted(ratings)
    delegations = [c["delegation"] for c in ratings[analysts[0]]["countries"]]
    by = {a: {c["delegation"]: c for c in ratings[a]["countries"]} for a in analysts}
    markers: dict[tuple, dict] = {}
    for analyst in analysts:
        for marker in ratings[analyst]["markers"]:
            key = (marker["type"], tuple(sorted(marker["delegations"])))
            entry = markers.setdefault(key, {"type": marker["type"], "delegations": sorted(marker["delegations"]), "analysts": [], "notes": []})
            entry["analysts"].append(analyst)
            entry["notes"].append(marker["note"])
    return {"analysts": len(analysts),
            "countries": [{"delegation": d, **{name: pooled([(a, by[a][d][name]) for a in analysts]) for name in COUNTRY_METRICS}} for d in delegations],
            "global": {name: pooled([(a, ratings[a]["global"][name]) for a in analysts]) for name in GLOBAL_METRICS},
            "summaries": [{"analyst": a, "summary": ratings[a]["summary"]} for a in analysts],
            "markers": sorted(markers.values(), key=lambda m: (-len(m["analysts"]), m["type"]))}
