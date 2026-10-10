# Agent dynamics lab

The primary research object is the **member**, not the national commitment or
temperature chart. The frontend shows six delegations and their actual roster,
member types, recorded preferences, private discussions and mandate support by
round. The existing Model UN simulator remains separate.

## Start

```bash
uv sync
# Generate a saved run (no credentials or LLM charges):
uv run python -m climate_sim --seed 42 --output runs/my-climate-run
# Open the agent lab in a regular browser:
uv run python -m climate_sim.lab --run runs/my-climate-run --port 8766
```

Open `http://127.0.0.1:8766/`. The lab binds to loopback only. Use an unused output
directory when generating a run; existing archives are not overwritten.

For Gemini, set `GEMINI_API_KEY` locally and use `--backend gemini` when generating
the run. Per-member models can be supplied with `--agent-models file.json`.
The replay labels the actual backend; an offline-scripted run is never presented
as an LLM run. The lab does not itself make paid LLM calls.

## Delegation room

- Country navigation shows the actual number of members in each team.
- Round selection chooses that round's recorded internal discussion, not a
  fabricated transcript or the final state reused for every round.
- Member cards identify head negotiator, economic minister and climate adviser,
  their recorded backend, shared preference and approval of the selected mandate.
- The member inspector separates individual-private preparation from the proposal
  that the member chose to share with the team.
- Alignment uses the existing mean pairwise normalized policy distance across
  all six commitment fields, before and after this round's discussion. Lower is
  closer. It is not a probability, psychology score or claim of consensus.
- In round one, the starting positions are individual-private preparation. In
  later rounds, the starting positions are the previous round's shared proposals.
- Mandate selection, priorities, red lines and actual recorded support are shown
  separately from the public summit.

## Information perspectives

`Researcher` inspects the privileged audit. `Public summit` receives only public
events. A selected member receives their own private preparation, their team's
internal discussions, and only meetings addressed to that exact member.
Filtering happens in the Python server before a response is sent. Private data
is not embedded in the public HTML or simply hidden by CSS.

This is a **single-operator local research lab**, not an authenticated multi-user
service. Switching perspectives is a researcher inspection tool. Before real
external humans join, add authenticated identities and bind each connection to a
participant capability; never expose the researcher endpoint to players. Host
and Origin checks and a custom action header limit cross-origin browser requests,
but do not replace authentication.

## Meeting protocol sandbox

Any registered member—not just spokespeople—can invite any other member or
cross-country group. The protocol in `climate_sim/meetings.py` is independent of
the decision backend and can be used by AI or human controllers.

```text
request → pending → every invitee accepts → active → messages → closed
                  → any invitee declines → declined
                  → five ticks without every ACK → expired → choose new action
```

Five means **five simulation ticks**, not seconds. A request at tick zero expires
at tick five; an acknowledgement at tick five is too late. The lab clock advances
only when the operator chooses “Advance 1 tick.” An active meeting does not expire
on its original invitation deadline. Every invitee must accept; there is no
silent quorum, auto-accept or forged AI acknowledgement.

Requests and messages are participant-private. Inviting a Chinese adviser does
not give the rest of the Chinese delegation access. A single invitation can
contain any combination of registered member roles and countries.

The floor is a **manual protocol sandbox**, separate from the saved run. The
researcher can act as different members to exercise request, acknowledgement,
decline, message and timeout transitions. This is real protocol state, not an
animation, but agents do not autonomously request or respond in this sandbox.
Live Concordia scheduling and Gemini-driven meeting actions are a next integration
step; the existing saved run still has a six-spokesperson formal summit.

Choosing a member perspective and “Play this role” changes its sandbox controller
to human and restricts actions to that member. The HTTP server also forces private
reads into that member's view while the role is held. “Leave human role” returns to
researcher mode. This is a local control prototype, not networked human participation.

Each lab launch writes scoped meeting events and controller changes to a new
`runs/meeting-lab-*/meetings.jsonl` archive. Archives contain private conversations,
are intentionally Git-ignored, and should not be publicly pushed. The sandbox is
not automatically restored after a server restart.

## Climate and sources

Climate evaluation is secondary and collapsible. The default deterministic mock
is neither C-ROADS nor climate science. `ClimateEvaluator` keeps the scientific
boundary separate from social simulation. `ReviewedOutcomeEvaluator` can use
exact-input manually reviewed C-ROADS results; no undocumented public API is assumed.

Primary sources: [World Climate resources](https://www.climateinteractive.org/world-climate-simulation-resources/),
[C-ROADS user guide](https://docs.climateinteractive.org/projects/c-roads/en/latest/guide/using.html),
and [Google DeepMind Concordia](https://github.com/google-deepmind/concordia).
Source PDFs and attributed extracted pages are in `data/climate/`. Adapted briefing
materials are credited to Climate Interactive, MIT Sloan and the UMass Lowell
Climate Change Initiative under CC BY 4.0. Numeric starting proposals and scripted
personalities are experiment assumptions, not official prescribed positions.

## Verify

```bash
uv run pytest -q
node --check frontend/app.js
node tests/test_agent_lab_ui.cjs
node tests/test_frontend_preview.cjs
```

The meeting tests cover cross-country groups, strict acknowledgement barriers,
exact deadlines, decline and subsequent actions, uninvited-teammate exclusion,
copy isolation, replay scoping and human-controller impersonation prevention.
