"""Transparent behavioural measures and experimental satisfaction proxies."""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from statistics import mean
from typing import TYPE_CHECKING

from .agents import average_commitments, policy_distance
from .models import ClimateCommitment, Role

if TYPE_CHECKING:
    from .simulation import Simulation


def dispersion(positions: list[ClimateCommitment]) -> float:
    distances = [policy_distance(a, b) for a, b in combinations(positions, 2)]
    return mean(distances) if distances else 0


def finance_accounting(commitments: dict[str, ClimateCommitment]) -> dict:
    contributions = sum(max(0, c.climate_fund_billion_per_year) for c in commitments.values())
    requests = sum(max(0, -c.climate_fund_billion_per_year) for c in commitments.values())
    disbursed = min(contributions, requests)
    allocation = {k: disbursed * max(0, -c.climate_fund_billion_per_year) / requests if requests else 0
                  for k, c in commitments.items()}
    return {"contributions_billion_per_year": contributions, "requests_billion_per_year": requests,
            "net_pledges_billion_per_year": contributions - requests,
            "mock_disbursement_billion_per_year": disbursed,
            "funding_gap_billion_per_year": max(0, requests - contributions),
            "allocation_rule": "Synthetic pro-rata allocation among requests, capped at total contributions; not an actual agreement",
            "mock_received_by_delegation": allocation}


def calculate_metrics(sim: Simulation) -> dict:
    initial, final = sim.commitments[1], sim.commitments[max(sim.commitments)]
    outcome = sim.outcomes[max(sim.outcomes)]
    finance = finance_accounting(final)
    delegations, teams = {}, {}
    for region, members in sim.teams.items():
        first_positions = [member.initial_preference for member in members]
        last_positions = [member.preferred for member in members]
        initial_disagreement, final_disagreement = dispersion(first_positions), dispersion(last_positions)
        speech_counts, word_counts = Counter(), Counter()
        for event in sim.store.events:
            if event.kind == "internal_speech" and event.owners == [region]:
                speech_counts[event.actor] += 1
                word_counts[event.actor] += len(event.payload["speech"].split())
        total = sum(speech_counts.values())
        shares = [speech_counts[m.name] / total if total else 0 for m in members]
        balance = -sum(p * math.log(p) for p in shares if p) / math.log(len(members))
        mandate = sim.mandates[region]
        minority = {}
        for member in members:
            if not mandate.internal_support[member.name]:
                counterfactual = {**final, region: member.preferred}
                temperature = sim.evaluator.evaluate(counterfactual).warming_celsius if outcome.is_mock else None
                minority[member.name] = {"counterfactual_warming_celsius": temperature,
                    "improved_mock_warming": temperature < outcome.warming_celsius if temperature is not None else None,
                    "interpretation": "One-region substitution holding every other final commitment fixed. Not a causal estimate or agent forecast."}
        teams[region] = {
            "initial_disagreement": round(initial_disagreement, 6),
            "final_disagreement": round(final_disagreement, 6),
            "opinion_convergence": round(initial_disagreement - final_disagreement, 6),
            "participation_balance": round(balance, 6),
            "speech_counts": dict(speech_counts),
            "leader_turn_share": shares[0],
            "leader_word_share": word_counts[members[0].name] / max(1, sum(word_counts.values())),
            "leader_policy_distance": policy_distance(final[region], members[0].preferred),
            "spokesperson_drift_from_team_mean": policy_distance(final[region], average_commitments(last_positions)),
            "minority_advice_counterfactuals": minority,
        }
        changed = {field: getattr(final[region], field) - getattr(initial[region], field)
                   for field in ClimateCommitment.model_fields if field != "delegation"}
        received = finance["mock_received_by_delegation"][region]
        rate_within_limit = final[region].annual_reduction_rate_percent <= sim.briefs[region].experimental_limits["reduction_rate_maximum"]
        fund_within_limit = abs(final[region].climate_fund_billion_per_year) <= sim.briefs[region].experimental_limits["finance_absolute_maximum"]
        other_regions_reduce = all(c.annual_reduction_rate_percent > 0 for k, c in final.items() if k != region)
        requested = max(0, -final[region].climate_fund_billion_per_year)
        proxies = {"global_below_2c": outcome.target_2c_achieved,
                   "global_at_or_below_1_5c": outcome.target_1_5c_achieved,
                   "transition_within_experimental_ceiling": rate_within_limit,
                   "finance_within_experimental_ceiling": fund_within_limit,
                   "reciprocal_positive_reductions": other_regions_reduce,
                   "request_fully_mock_funded": received >= requested - 1e-6 if requested else None}
        violations = []
        if not rate_within_limit:
            violations.append("experimental_transition_ceiling")
        if not fund_within_limit:
            violations.append("experimental_finance_ceiling")
        agreements = [o.id for o in sim.offers.values() if o.status == "fulfilled" and (o.sender.value == region or region in {d.value for d in o.recipients})]
        delegations[region] = {"official_goals": sim.briefs[region].goals,
            "objective_satisfaction_proxies": proxies,
            "objective_proxy_score": sum(x is True for x in proxies.values()) / sum(x is not None for x in proxies.values()),
            "official_qualitative_red_lines": sim.briefs[region].negotiating_constraints,
            "qualitative_red_line_assessment": "Not automatically decidable; review the transcript. Numeric checks below are experimental proxies only.",
            "experimental_red_lines_violated": violations,
            "funding_pledged_billion_per_year": max(0, final[region].climate_fund_billion_per_year),
            "funding_requested_billion_per_year": requested,
            "mock_funding_obtained_billion_per_year": received,
            "concessions_from_initial": changed,
            "agreements_secured": agreements,
            "mandate_commitment_distance": policy_distance(final[region], mandate.approved_commitment)}
    status_counts = Counter(o.status for o in sim.offers.values())
    meetings = list(sim.hub.meetings.values())
    delegation_of = {name: member.brief.delegation.value for name, member in sim.agents.items()}
    fulfilled = [o for o in sim.offers.values() if o.status == "fulfilled"]
    return {"metric_notes": "Policy distances normalize years by 74, rates by 10, forests by 100 and finance by 300. Turn balance is normalized Shannon entropy; equal speaking turns are scheduled, not emergent. Objective scores, finance allocation, warming and regional risk from the mock are experimental diagnostics, not national welfare or climate science.",
        "global": {"initial_warming_celsius": sim.outcomes[1].warming_celsius,
                   "final_warming_celsius": outcome.warming_celsius,
                   "warming_change_celsius": outcome.warming_celsius - sim.outcomes[1].warming_celsius,
                   "target_2c_achieved": outcome.target_2c_achieved,
                   "target_1_5c_achieved": outcome.target_1_5c_achieved,
                   "is_mock": outcome.is_mock, "evaluator": outcome.evaluator,
                   "rounds_completed": max(sim.commitments), "finance": finance,
                   "mean_commitment_change": mean(policy_distance(initial[k], final[k]) for k in final),
                   "offer_status_counts": dict(status_counts), "agreements_fulfilled": len(fulfilled),
                   "meetings": {"held": len(meetings), "mutual": sum(m.mutual for m in meetings),
                                "close_reasons": dict(Counter(m.close_reason for m in meetings)),
                                "messages": sum(len(m.messages) for m in meetings),
                                "cross_delegation": sum(len({delegation_of[p] for p in m.participants}) > 1 for m in meetings),
                                "non_spokesperson_openers": sum(sim.agents[m.initiator].role != Role.HEAD for m in meetings),
                                "with_observers": sum(any(sim.agents[p].role == Role.OBSERVER for p in m.participants) for m in meetings)},
                   "observers": {member.name: {
                       "statements": sum(e.kind == "observer_statement" and e.actor == member.name for e in sim.store.events),
                       "meetings_opened": sum(m.initiator == member.name for m in meetings),
                       "meetings_held": sum(member.name in m.participants and bool(m.messages) for m in meetings),
                       "delegates_met": sorted({p for m in meetings if member.name in m.participants and m.messages
                                                for p in m.participants if p != member.name})}
                       for member in sim.observers},
                   "agreement_types": {"public": sum(o.public for o in fulfilled), "private": sum(not o.public for o in fulfilled),
                                       "conditional": sum(bool(o.conditions) for o in fulfilled), "coalition": sum(len(o.recipients) > 1 for o in fulfilled)}},
        "delegations": delegations, "internal_teams": teams}


def render_report(sim: Simulation, metrics: dict) -> str:
    global_metrics = metrics["global"]
    mock_label = "DETERMINISTIC MOCK — not C-ROADS or climate science" if global_metrics["is_mock"] else global_metrics["evaluator"]
    finance = global_metrics["finance"]
    lines = ["# Nested climate negotiation report", "", mock_label, "",
        f"Seed {sim.config.seed}; {len(sim.agents)} members; {sim.config.team_size} per delegation; {sim.config.decision_rule} decisions; {sim.config.composition} priorities; {global_metrics['rounds_completed']} rounds.", "",
        f"Warming: **{global_metrics['initial_warming_celsius']:.2f} → {global_metrics['final_warming_celsius']:.2f} °C**.",
        f"Below 2°C: {global_metrics['target_2c_achieved']}. At/below 1.5°C: {global_metrics['target_1_5c_achieved']}.",
        f"Finance: {finance['contributions_billion_per_year']:.1f} billion/year contributed; {finance['requests_billion_per_year']:.1f} requested; gap {finance['funding_gap_billion_per_year']:.1f}.",
        f"Fulfilled agreements: {global_metrics['agreements_fulfilled']}; offer states: {global_metrics['offer_status_counts']}.",
        f"Bilaterals: {global_metrics['meetings']['held']} held ({global_metrics['meetings']['mutual']} mutually requested), ended by {global_metrics['meetings']['close_reasons']}; "
        f"{global_metrics['meetings']['messages']} messages; {global_metrics['meetings']['cross_delegation']} across delegations; "
        f"{global_metrics['meetings']['with_observers']} with observers.",
        *[f"Observer {sim.agents[name].brief.title}: {o['statements']} public statements; met {', '.join(o['delegates_met']) or 'nobody'}."
          for name, o in global_metrics["observers"].items()], "",
        "| Delegation | Annual reduction: initial → final | Mandate drift | Disagreement: initial → final | Leader word share |", "|---|---:|---:|---:|---:|"]
    for region in sim.teams:
        team = metrics["internal_teams"][region]
        initial = sim.commitments[1][region]
        final = sim.commitments[max(sim.commitments)][region]
        lines.append(f"| {sim.briefs[region].title} | {initial.annual_reduction_rate_percent:.2f}% → {final.annual_reduction_rate_percent:.2f}% | {metrics['delegations'][region]['mandate_commitment_distance']:.3f} | {team['initial_disagreement']:.3f} → {team['final_disagreement']:.3f} | {team['leader_word_share']:.0%} |")
    lines += ["", "Metrics are explicit proxies. Minority advice uses one-region counterfactual substitutions; it does not establish causality. Check metrics.json for goals, finance allocations, red-line limitations and voting support.", "",
              "Private research archive: events.jsonl and state.json contain individual preferences, team transcripts and targeted offers. public.json contains only public events. Never feed the research archive back into agents or publish it as a public summit transcript.", "",
              sim.briefs["usa"].attribution + " Materials: CC BY 4.0. https://www.climateinteractive.org/world-climate-simulation-resources/", ""]
    if sim.config.us_variant != "national":
        lines += ["US Climate Alliance proxy adaptation: its official brief grants no negotiating standing. Whole-US commitments in this experiment are hypothetical.", ""]
    return "\n".join(lines)
