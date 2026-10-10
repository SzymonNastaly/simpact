"""Run a complete nested negotiation without credentials, or opt into Gemini agents."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .briefs import DEFAULT_BRIEFS
from .evaluator import ReviewedOutcomeEvaluator
from .models import ObserverId, SimulationConfig
from .simulation import Simulation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["offline", "gemini"], default="offline")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--team-size", type=int, default=3)
    parser.add_argument("--decision-rule", choices=["leader", "vote"], default="leader")
    parser.add_argument("--composition", choices=["diverse", "homogeneous"], default="diverse")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--internal-turns", type=int, default=2)
    parser.add_argument("--negotiation-turns", type=int, default=1)
    parser.add_argument("--offer-visibility", choices=["mixed", "public", "private"], default="mixed")
    parser.add_argument("--memory-length", type=int, default=100)
    parser.add_argument("--bilateral-slots", type=int, default=2, help="0 disables bilaterals before each later round; any other value enables them (no slots)")
    parser.add_argument("--bilateral-messages", type=int, default=8, help="message limit per bilateral conversation")
    parser.add_argument("--model", default=SimulationConfig.model_fields["default_model"].default)
    parser.add_argument("--agent-models", type=Path, help="JSON object mapping exact member IDs to Gemini model names")
    parser.add_argument("--us-variant", choices=["national", "climate_alliance_proxy"], default="national")
    parser.add_argument("--world", choices=["six", "eight"], default="six", help="eight adds Brazil and Saudi Arabia")
    parser.add_argument("--observers", nargs="*", choices=[o.value for o in ObserverId], default=[],
                        help="observer groups who meet delegates and give press statements; no values means all three")
    parser.add_argument("--stop-on-target", action="store_true")
    parser.add_argument("--briefs", type=Path, default=DEFAULT_BRIEFS)
    parser.add_argument("--output", type=Path, help="New output directory (existing paths are refused)")
    parser.add_argument("--reviewed-outcomes", type=Path, help="Use exact-input manually reviewed C-ROADS results instead of the mock")
    args = parser.parse_args()
    try:
        config = SimulationConfig(seed=args.seed, team_size=args.team_size,
            decision_rule=args.decision_rule, composition=args.composition, rounds=args.rounds,
            internal_turns=args.internal_turns, negotiation_turns=args.negotiation_turns,
            offer_visibility=args.offer_visibility, memory_length=args.memory_length,
            bilateral_slots=args.bilateral_slots, bilateral_messages=args.bilateral_messages,
            backend=args.backend, default_model=args.model,
            agent_models=json.loads(args.agent_models.read_text()) if args.agent_models else {},
            us_variant=args.us_variant, stop_on_target=args.stop_on_target, world=args.world,
            observers=args.observers if args.observers or "--observers" not in sys.argv else list(ObserverId))
        output = args.output or Path("runs") / (datetime.now(timezone.utc).strftime("climate-%Y%m%dT%H%M%S-%fZ") + f"-seed{args.seed}")
        evaluator = ReviewedOutcomeEvaluator(args.reviewed_outcomes) if args.reviewed_outcomes else None
        sim = Simulation(config, output=output, briefs_directory=args.briefs, evaluator=evaluator)
        result = sim.run()
        print(result["report"])
        print(f"Run saved to {output.resolve()}")
        return 0
    except Exception as error:
        print(f"Simulation failed ({type(error).__name__}). Inspect failure.json/events.jsonl if a run directory was created. No provider exception or credential values are printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
