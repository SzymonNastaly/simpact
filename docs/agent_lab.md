# Round 1 viewer

The frontend has three steps: choose a country, read its Round 1 discussion,
and select an agent to inspect its own recorded perspective. There are no sidebars,
meeting controls, round switches, mandate dashboards or climate charts.

## Open a session

```bash
uv sync
uv run python -m climate_sim --seed 42 --output runs/my-climate-run
uv run python -m climate_sim.lab --run runs/my-climate-run --port 8766
```

Open `http://127.0.0.1:8766/`. Existing output directories are refused; use a new
directory for a new run. The simulator still produces a complete experiment;
the frontend presents only its first round. Opening the viewer makes no LLM calls.

To generate Gemini agent data, export `GEMINI_API_KEY` and add `--backend gemini`
to the simulation command. The default model is `gemini-3.8-flash`; `--model`
and `--agent-models` override it. Scripted runs are labelled in the viewer.

## What the viewer shows

- Country cards use the actual run roster, including non-default team sizes.
- The discussion comes directly from Round 1 `internal_speech` events in order.
- Agent details show that member's private preparation and starting proposal.
- Decision records show recorded summaries, cited evidence, authorized
  observations and the resulting action. They are summaries reported by the
  agent or descriptions of the scripted policy, not raw provider thinking.
- Older runs without `agent_trace` events show their preparation notes and
  explicitly identify the missing decision records.

The new API endpoints `/api/round-one` and `/api/round-one/agent` return only
Round 1 data. They retain the existing server-side perspective filtering.
The frontend uses the local researcher's perspective to inspect each member.
The server binds to loopback; this is a local operator's research viewer, not an
authenticated multi-user service. Saved private run archives remain Git-ignored.

## Backend meetings

The simulation's member meeting protocol remains available independently of the
frontend. `--meeting-ticks` controls the normal phase before later rounds;
`--meeting-ticks 0` or `--no-member-meetings` disables autonomous meetings.
Additional group-floor scenes and participant-authored team reports are opt-in
through `--member-floor-ticks`. Both paths share one clock and meeting registry.
Requests require every invitee's acknowledgement within five simulation ticks.
Private messages remain scoped to the exact participants; outcome reports can
notify the reporting participant's delegation without copying the transcript.

## Eight delegations and observers

`--world eight` splits Brazil and Saudi Arabia out of Other Developing Nations, so
eight teams negotiate; `six` (the default) is World Climate's own game and the only one
C-ROADS reviewed outcomes can match. There is no World Climate briefing for either
country: their members read the Other Developing Nations PDF, which covers their
regions, plus author-written country goals and experimental parameters in
`data/climate/briefs/`. The mock evaluator carves their emissions out of that bloc's
share and weights Brazil's forest pledges more heavily.

`--observers [GROUP ...]` adds one representative per World Climate supporting role
(all three when no group is named): `climate_activists`, `fossil_fuel_lobby` and
`journalists`. They take part in member meetings under the same protocol, and after
each round's commitments every observer gives a public `observer_statement` that all
delegates see. They never prepare, deliberate, pledge, make or answer offers, or vote.
The replay lists them under `observers`; the Round 1 viewer shows countries only.

## Briefing PDFs

Gemini members get their own briefing PDF in every call, uploaded once per run through
the Gemini Files API after its hash is checked against the brief. The brief JSON keeps
what the PDFs do not contain: the experimental baseline, ceilings and finance role
that the validator and mock evaluator use. `scripts/build_climate_briefs.py` rebuilds
the JSON from `data/climate/brief_specs.json` and `observer_specs.json`.

## Verify

```bash
uv run pytest -q
node --check frontend/app.js
node tests/test_agent_lab_ui.cjs
```

The interaction test uses an actual saved simulation with five agents per country.
It checks country navigation, dialogue, agent details, stale responses, escaped
model output and loading failures. Browser visual verification is a separate check.
