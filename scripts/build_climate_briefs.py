"""Rebuild attributed briefing JSON for the official World Climate PDFs.

Gemini members read the PDFs themselves; the JSON pins each PDF by hash and keeps the
curated summaries and experimental parameters the validator and mock evaluator need.
This adapts CC BY 4.0 material; no source PDF or user dataset is modified.
"""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.climateinteractive.org/world-climate-simulation-resources/"
CREDIT = "World Climate Simulation materials by Climate Interactive, MIT Sloan, and the UMass Lowell Climate Change Initiative."
ATTRIBUTION = f"{CREDIT} Updated November 2025. Adapted for nested-agent experiments."
OBSERVER_ATTRIBUTION = f"{CREDIT} Updated March 2022. Adapted for nested-agent experiments."
FIELDS = ("emissions_peak_year", "reduction_start_year", "annual_reduction_rate_percent", "deforestation_prevention_percent", "afforestation_percent", "climate_fund_billion_per_year")


def source(dataset: Path, relative: str) -> dict:
    path = dataset / relative
    if path.read_bytes()[:5] != b"%PDF-":
        raise ValueError(f"Not a PDF: {path}")
    return {"source_file": relative, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source_url": SOURCE_URL}


def build(dataset: Path, output: Path) -> None:
    specs = json.loads((ROOT / "data/climate/brief_specs.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    for key, spec in specs.items():
        variant = spec.get("variant", "climate_alliance_proxy" if key == "climate_alliance_proxy" else "national")
        delegation = spec.get("delegation", "usa" if key == "climate_alliance_proxy" else key)
        brief = {
            "delegation": delegation, "title": spec["title"], "variant": variant,
            **source(dataset, "materials/" + spec["file"]), "attribution": ATTRIBUTION, "license": "CC BY 4.0",
            **{k: spec[k] for k in ("goals", "negotiating_constraints", "opportunities", "climate_risks", "finance_position")},
            "experimental_baseline": {"delegation": delegation, **dict(zip(FIELDS, spec["baseline"], strict=True))},
            "experimental_limits": {"reduction_rate_maximum": spec["limits"][0], "finance_absolute_maximum": spec["limits"][1]},
            "adaptation_notes": (spec["note"] + " " if "note" in spec else "") +
                "Goals and qualitative constraints paraphrase the briefing. Baseline commitments, numeric ceilings, finance-role restrictions, personalities and scoring are author-created experimental assumptions, NOT official prescribed positions. Source text reflects November 2025 and is not a live policy fact-check." +
                (" This variant deliberately grants a hypothetical whole-US proxy role to a coalition that has NO official negotiating standing in the source. Results must not be described as binding US commitments." if variant == "climate_alliance_proxy" else ""),
        }
        (output / f"{key}.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n")
        print(f"{key}: sha256 {brief['source_sha256'][:12]}")
    observers = json.loads((ROOT / "data/climate/observer_specs.json").read_text())
    (output / "observers").mkdir(exist_ok=True)
    for key, spec in observers.items():
        brief = {"delegation": key, "title": spec["title"], **source(dataset, "supporting/" + spec["file"]),
                 "attribution": OBSERVER_ATTRIBUTION, "license": "CC BY 4.0",
                 **{k: spec[k] for k in ("goals", "tactics", "persona")},
                 "adaptation_notes": "Goals and tactics paraphrase the briefing. The persona and the observer rules (meetings and one public statement per round; no pledges, offers or votes) are author-created experimental assumptions, NOT official prescribed roles. Source text reflects March 2022."}
        (output / "observers" / f"{key}.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n")
        print(f"observers/{key}: sha256 {brief['source_sha256'][:12]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/climate")
    parser.add_argument("--output", type=Path, default=ROOT / "data/climate/briefs")
    args = parser.parse_args()
    build(args.dataset, args.output)
