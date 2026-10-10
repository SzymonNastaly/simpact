"""Rebuild portable, attributed briefing JSON from the downloaded official PDFs.

Curated summaries and experimental parameters are separate from page-exact source text.
This transforms CC BY 4.0 material; no source PDF or user dataset is modified.
"""

import argparse
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTION = "World Climate Simulation materials by Climate Interactive, MIT Sloan, and the UMass Lowell Climate Change Initiative. Updated November 2025. Adapted for nested-agent experiments."
FIELDS = ("emissions_peak_year", "reduction_start_year", "annual_reduction_rate_percent", "deforestation_prevention_percent", "afforestation_percent", "climate_fund_billion_per_year")


def extract(dataset: Path, output: Path) -> None:
    specs = json.loads((ROOT / "data/climate/brief_specs.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    for key, spec in specs.items():
        source = dataset / "materials" / spec["file"]
        pages = [{"page": n + 1, "text": " ".join(page.extract_text().split())}
                 for n, page in enumerate(PdfReader(source).pages)]
        if any(not p["text"] for p in pages):
            raise ValueError(f"Missing text layer: {source}")
        variant = "climate_alliance_proxy" if key == "climate_alliance_proxy" else "national"
        delegation = "usa" if variant != "national" else key
        brief = {
            "delegation": delegation, "title": spec["title"], "variant": variant,
            "source_file": "materials/" + spec["file"],
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_url": "https://www.climateinteractive.org/world-climate-simulation-resources/",
            "source_pages": pages, "attribution": ATTRIBUTION, "license": "CC BY 4.0",
            **{k: spec[k] for k in ("goals", "negotiating_constraints", "opportunities", "climate_risks", "finance_position")},
            "experimental_baseline": {"delegation": delegation, **dict(zip(FIELDS, spec["baseline"], strict=True))},
            "experimental_limits": {"reduction_rate_maximum": spec["limits"][0], "finance_absolute_maximum": spec["limits"][1]},
            "adaptation_notes": "Goals and qualitative constraints paraphrase pages 1-2. Baseline commitments, numeric ceilings, finance-role restrictions, personalities and scoring are author-created experimental assumptions, NOT official prescribed positions. Source text reflects November 2025 and is not a live policy fact-check." +
                (" This variant deliberately grants a hypothetical whole-US proxy role to a coalition that has NO official negotiating standing in the source. Results must not be described as binding US commitments." if variant != "national" else ""),
        }
        (output / f"{key}.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n")
        print(f"{key}: {len(pages)} source pages, sha256 {brief['source_sha256'][:12]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/climate")
    parser.add_argument("--output", type=Path, default=ROOT / "data/climate/briefs")
    args = parser.parse_args()
    extract(args.dataset, args.output)
