"""Validated wire formats. Finance is USD billions/year: positive pays, negative requests."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DelegationId(StrEnum):
    USA = "usa"
    EU = "eu"
    CHINA = "china"
    INDIA = "india"
    OTHER_DEVELOPED = "other_developed"
    OTHER_DEVELOPING = "other_developing"


class Role(StrEnum):
    HEAD = "head_negotiator"
    ECONOMIC = "economic_minister"
    CLIMATE = "climate_adviser"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)


Percent = Annotated[float, Field(ge=0, le=100)]
Year = Annotated[int, Field(ge=2026, le=2100, strict=True)]


class ClimateCommitment(Model):
    delegation: DelegationId
    emissions_peak_year: Year
    reduction_start_year: Year
    annual_reduction_rate_percent: Percent
    deforestation_prevention_percent: Percent
    afforestation_percent: Percent
    climate_fund_billion_per_year: float = Field(ge=-1_000_000, le=1_000_000)

    @model_validator(mode="after")
    def chronological(self):
        if self.reduction_start_year < self.emissions_peak_year:
            raise ValueError("Reduction cannot start before emissions peak")
        return self

    def changed(self, changes: dict[str, Any]) -> ClimateCommitment:
        return ClimateCommitment.model_validate({**self.model_dump(), **changes})


COMMITMENT_FIELDS = tuple(x for x in ClimateCommitment.model_fields if x != "delegation")
CONDITION_SUFFIXES = {
    "climate_fund_contribution_minimum": ("climate_fund_billion_per_year", "minimum"),
    "annual_reduction_rate_percent_minimum": ("annual_reduction_rate_percent", "minimum"),
    "emissions_peak_year_maximum": ("emissions_peak_year", "maximum"),
}


def parse_condition(key: str) -> tuple[DelegationId, str, str]:
    for delegation in DelegationId:
        for suffix, (field, operator) in CONDITION_SUFFIXES.items():
            if key == f"{delegation.value}_{suffix}":
                return delegation, field, operator
    raise ValueError(f"Unsupported offer condition: {key}")


class NegotiationOffer(Model):
    id: str = Field(min_length=1, max_length=120)
    sender: DelegationId
    recipients: list[DelegationId] = Field(min_length=1, max_length=5)
    public: bool = False
    offered_changes: dict[str, float | int] = Field(min_length=1)
    conditions: dict[str, float] = Field(default_factory=dict)
    expires_after_round: int = Field(ge=1)
    status: Literal["proposed", "accepted", "rejected", "expired", "fulfilled", "unfulfilled"] = "proposed"

    @model_validator(mode="after")
    def valid_offer(self):
        if self.sender in self.recipients or len(set(self.recipients)) != len(self.recipients):
            raise ValueError("Recipients must be distinct foreign delegations")
        if set(self.offered_changes) - set(COMMITMENT_FIELDS):
            raise ValueError("An offer can change only the sender's commitment fields")
        for key in self.conditions:
            parse_condition(key)
        for field, value in self.offered_changes.items():
            if field.endswith("year") and (not isinstance(value, int) or not 2026 <= value <= 2100):
                raise ValueError("Offered years must be integers in 2026..2100")
            if field.endswith("percent") and not 0 <= value <= 100:
                raise ValueError("Offered percentages must be in 0..100")
        return self

    def conditions_met(self, commitments: dict[str, ClimateCommitment]) -> bool:
        for key, threshold in self.conditions.items():
            delegation, field, operator = parse_condition(key)
            value = getattr(commitments[delegation.value], field)
            if operator == "minimum" and value < threshold:
                return False
            if operator == "maximum" and value > threshold:
                return False
        return True


class TeamMandate(Model):
    delegation: DelegationId
    approved_commitment: ClimateCommitment
    priorities: list[str] = Field(min_length=1)
    red_lines: list[str] = Field(default_factory=list)
    permitted_concessions: list[str] = Field(default_factory=list)
    desired_partners: list[DelegationId] = Field(default_factory=list)
    internal_support: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self):
        if self.approved_commitment.delegation != self.delegation:
            raise ValueError("Mandate and commitment delegation must match")
        return self


class EmissionsPoint(Model):
    year: int
    emissions_index: float = Field(ge=0)


class ClimateOutcome(Model):
    warming_celsius: float = Field(ge=0, le=20)
    target_2c_achieved: bool
    target_1_5c_achieved: bool
    global_emissions_trajectory: list[EmissionsPoint]
    regional_impacts: dict[str, dict[str, float | str]]
    explanation: str
    evaluator: str
    is_mock: bool

    @model_validator(mode="after")
    def correct_targets(self):
        if self.target_2c_achieved != (self.warming_celsius < 2):
            raise ValueError("2C flag must agree with warming (<2C)")
        if self.target_1_5c_achieved != (self.warming_celsius <= 1.5):
            raise ValueError("1.5C flag must agree with warming (<=1.5C)")
        return self


class SourcePage(Model):
    page: int = Field(ge=1)
    text: str


class Brief(Model):
    delegation: DelegationId
    title: str
    variant: str = "national"
    source_file: str
    source_sha256: str
    source_url: str
    source_pages: list[SourcePage]
    attribution: str
    license: str
    goals: list[str]
    negotiating_constraints: list[str]
    opportunities: list[str]
    climate_risks: list[str]
    finance_position: Literal["contributor", "recipient", "flexible"]
    experimental_baseline: ClimateCommitment
    experimental_limits: dict[str, float]
    adaptation_notes: str

    def check_commitment(self, commitment: ClimateCommitment) -> None:
        if commitment.delegation != self.delegation:
            raise ValueError("Cannot submit another delegation's commitment")
        fund = commitment.climate_fund_billion_per_year
        if self.finance_position == "contributor" and fund < 0:
            raise ValueError("This experimental contributor role cannot request finance")
        if self.finance_position == "recipient" and fund > 0:
            raise ValueError("This experimental recipient role cannot contribute finance")
        if abs(fund) > self.experimental_limits["finance_absolute_maximum"]:
            raise ValueError("Commitment exceeds the experimental finance ceiling")
        if commitment.annual_reduction_rate_percent > self.experimental_limits["reduction_rate_maximum"]:
            raise ValueError("Commitment exceeds the experimental transition-rate ceiling")


class SimulationConfig(Model):
    seed: int = Field(ge=0)
    team_size: int = Field(default=3, ge=3, le=12)
    decision_rule: Literal["leader", "vote"] = "leader"
    composition: Literal["diverse", "homogeneous"] = "diverse"
    rounds: int = Field(default=2, ge=2, le=10)
    internal_turns: int = Field(default=2, ge=1, le=10)
    negotiation_turns: int = Field(default=1, ge=1, le=10)
    offer_visibility: Literal["mixed", "public", "private"] = "mixed"
    memory_length: int = Field(default=100, ge=1, le=10000)
    backend: Literal["offline", "gemini"] = "offline"
    default_model: str = "gemini-flash-latest"
    agent_models: dict[str, str] = Field(default_factory=dict)
    us_variant: Literal["national", "climate_alliance_proxy"] = "national"
    stop_on_target: bool = False


class MemberAction(Model):
    speech: str = Field(default="", max_length=5000)
    private_strategy: str | None = Field(default=None, max_length=5000)
    commitment: ClimateCommitment | None = None
    mandate: TeamMandate | None = None
    offers: list[NegotiationOffer] = Field(default_factory=list, max_length=2)
    offer_responses: dict[str, Literal["accept", "reject"]] = Field(default_factory=dict)
    ranked_candidates: list[str] = Field(default_factory=list)
    approved_candidates: list[str] = Field(default_factory=list)
