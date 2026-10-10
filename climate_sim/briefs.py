from pathlib import Path

from .models import Brief, DelegationId, ObserverBrief, ObserverId, WORLDS

DEFAULT_BRIEFS = Path(__file__).resolve().parents[1] / "data/climate/briefs"
SOURCES = DEFAULT_BRIEFS.parent  # every brief's source_file is relative to data/climate


def brief_file(delegation: DelegationId, us_variant: str, world: str) -> str:
    if delegation == DelegationId.USA and us_variant != "national":
        return "climate_alliance_proxy"
    if delegation == DelegationId.OTHER_DEVELOPING and world == "eight":
        return "other_developing_rest"
    return delegation.value


def load_briefs(directory: Path = DEFAULT_BRIEFS, us_variant: str = "national", world: str = "six") -> dict[str, Brief]:
    briefs = {}
    for delegation in WORLDS[world]:
        key = brief_file(delegation, us_variant, world)
        brief = Brief.model_validate_json((directory / f"{key}.json").read_text())
        if brief.delegation != delegation:
            raise ValueError(f"Brief identity mismatch: {key}")
        brief.check_commitment(brief.experimental_baseline)
        briefs[delegation.value] = brief
    return briefs


def load_observers(observers: list[ObserverId | str], directory: Path = DEFAULT_BRIEFS) -> dict[str, ObserverBrief]:
    briefs = {}
    for observer in map(ObserverId, observers):  # saved run configs hold plain strings
        brief = ObserverBrief.model_validate_json((directory / "observers" / f"{observer.value}.json").read_text())
        if brief.delegation != observer:
            raise ValueError(f"Observer brief identity mismatch: {observer.value}")
        briefs[observer.value] = brief
    return briefs
