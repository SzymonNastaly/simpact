"""Climate science stays outside Concordia. The built-in evaluator is a TOY MOCK."""

import hashlib
import json
from pathlib import Path
from typing import Protocol

from .models import ClimateCommitment, ClimateOutcome, EmissionsPoint, WORLDS


class ClimateEvaluator(Protocol):
    def evaluate(self, commitments: dict[str, ClimateCommitment]) -> ClimateOutcome: ...


def validate_world(commitments: dict[str, ClimateCommitment]) -> str:
    worlds = [name for name, regions in WORLDS.items() if set(commitments) == {d.value for d in regions}]
    if not worlds:
        raise ValueError("Climate evaluation requires exactly the negotiating regions of one world")
    if any(key != value.delegation.value for key, value in commitments.items()):
        raise ValueError("Commitment keys and delegation identities must match")
    return worlds[0]


def commitment_digest(commitments: dict[str, ClimateCommitment]) -> str:
    validate_world(commitments)
    canonical = json.dumps({k: v.model_dump(mode="json") for k, v in sorted(commitments.items())}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class MockClimateEvaluator:
    """Monotone toy cumulative-emissions index; NOT C-ROADS or a calibrated climate model.

    Baseline shares, growth, land-use multipliers and regional vulnerability are fabricated.
    The eight-delegation world carves Brazil and Saudi Arabia out of Other Developing's share.
    Temperature = 1.1 + 2.5 * cumulative_policy_index/cumulative_baseline_index.
    Finance has no direct physical effect, matching the workshop's separation of finance
    from C-ROADS emissions inputs. Agent decisions can respond to finance instead.
    """

    weights = {"six": {"usa": .14, "eu": .09, "china": .30, "india": .08,
                       "other_developed": .14, "other_developing": .25},
               "eight": {"usa": .14, "eu": .09, "china": .30, "india": .08,
                         "other_developed": .14, "other_developing": .20, "brazil": .03, "saudi_arabia": .02}}
    growth = {"usa": .003, "eu": 0, "china": .012, "india": .022,
              "other_developed": 0, "other_developing": .025, "brazil": .01, "saudi_arabia": .015}
    land_weight = {"brazil": 3}  # Amazon deforestation dominates Brazil's emissions
    vulnerability = {"usa": 1, "eu": .9, "china": 1.1, "india": 1.4,
                     "other_developed": 1, "other_developing": 1.8, "brazil": 1.3, "saudi_arabia": 1.5}

    def evaluate(self, commitments: dict[str, ClimateCommitment]) -> ClimateOutcome:
        weights = self.weights[validate_world(commitments)]
        trajectory = []
        baseline_total = 0.0
        for year in range(2026, 2101):
            global_index = 0.0
            for region, commitment in commitments.items():
                base = 100 * weights[region]
                growth = self.growth[region]
                baseline_total += base * (1 + growth) ** (year - 2026)
                growing_years = max(0, min(year, commitment.emissions_peak_year) - 2026)
                declining_years = max(0, year - commitment.reduction_start_year)
                industrial = base * (1 + growth) ** growing_years * (1 - commitment.annual_reduction_rate_percent / 100) ** declining_years
                land_progress = min(1, (year - 2026) / 24)
                land_factor = 1 - land_progress * self.land_weight.get(region, 1) * (.04 * commitment.deforestation_prevention_percent / 100 + .02 * commitment.afforestation_percent / 100)
                global_index += industrial * land_factor
            trajectory.append(EmissionsPoint(year=year, emissions_index=round(global_index, 6)))
        cumulative = sum(p.emissions_index for p in trajectory)
        warming = round(1.1 + 2.5 * cumulative / baseline_total, 4)
        return ClimateOutcome(
            warming_celsius=warming, target_2c_achieved=warming < 2,
            target_1_5c_achieved=warming <= 1.5,
            global_emissions_trajectory=trajectory,
            regional_impacts={k: {"mock_risk_index": round(max(0, warming - 1) * self.vulnerability[k], 4),
                                   "label": "Synthetic relative risk; no economic or physical calibration"}
                              for k in weights},
            explanation="DETERMINISTIC MOCK ONLY. Toy cumulative emissions and synthetic regional risk. Not a scientific forecast, not C-ROADS. Finance changes negotiation incentives but does not directly alter this evaluator.",
            evaluator="mock-cumulative-index-v1", is_mock=True,
        )


class ReviewedOutcomeEvaluator:
    """Offline bridge for outcomes manually evaluated in the official C-ROADS UI.

    Each JSON record must contain exact commitments, their digest, outcome, simulator
    version and scenario reference. Unknown inputs fail rather than fall back to mock.
    No public C-ROADS API is assumed or scraped.
    """

    def __init__(self, path: Path):
        self.records = json.loads(path.read_text())

    def evaluate(self, commitments: dict[str, ClimateCommitment]) -> ClimateOutcome:
        digest = commitment_digest(commitments)
        for record in self.records:
            recorded = {k: ClimateCommitment.model_validate(v) for k, v in record["commitments"].items()}
            if commitment_digest(recorded) != record["commitment_sha256"]:
                raise ValueError("Reviewed C-ROADS record has a mismatched input digest")
            if record["commitment_sha256"] == digest:
                if not record.get("simulator_version") or not record.get("scenario_reference"):
                    raise ValueError("Reviewed outcomes need simulator version and scenario evidence")
                outcome = ClimateOutcome.model_validate(record["outcome"])
                if outcome.is_mock or not outcome.evaluator.startswith("c-roads-reviewed"):
                    raise ValueError("Reviewed bridge requires an explicitly labelled C-ROADS outcome")
                return outcome
        raise ValueError(f"No reviewed C-ROADS result for commitments {digest}; evaluate/export these inputs first")
