from pathlib import Path

from .models import Brief, DelegationId

DEFAULT_BRIEFS = Path(__file__).resolve().parents[1] / "data/climate/briefs"


def load_briefs(directory: Path = DEFAULT_BRIEFS, us_variant: str = "national") -> dict[str, Brief]:
    briefs = {}
    for delegation in DelegationId:
        key = "climate_alliance_proxy" if delegation == DelegationId.USA and us_variant != "national" else delegation.value
        brief = Brief.model_validate_json((directory / f"{key}.json").read_text())
        if brief.delegation != delegation:
            raise ValueError(f"Brief identity mismatch: {key}")
        brief.check_commitment(brief.experimental_baseline)
        briefs[delegation.value] = brief
    return briefs
