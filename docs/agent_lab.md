# Round 1 conversations

The frontend has three steps: choose a country, read its Round 1 discussion,
and select an agent to read its thinking traces grouped by round. There are no sidebars,
meeting controls, round switches, mandate dashboards or climate charts.

## Open a session

For a continuous Gemini conversation:

```bash
# Export GEMINI_API_KEY in the launching shell.
uv run python -m climate_sim.lab --live --port 8766
```

Choose a country and press Start. Each agent prepares privately once, then
contributes through a fresh Concordia observation/action cycle. Each new message
is saved before the next call receives the full authorized country transcript.
The next speaker rotates through the roster. Other countries' private data and
other members' preparation are excluded from the input.

Pause prevents subsequent calls; an in-flight response can still finish.
`--message-budget 12` is the default per-country limit for each start. Continue
renews that limit and retains prior messages, preferences, and the next speaker.
`--thinking-level low|medium|high`, `--model`, `--team-size`, and `--seed` configure
a new live session. `--backend offline` exercises the same loop using explicitly
scripted policies. Only Round 1 runs in live mode.
`--world eight` also enables Brazil and Saudi Arabia in the live country board.

Live events, traces, failures and an atomic state checkpoint are saved under
`runs/round-one-live-*/` with private file permissions. These can later be opened
as a recorded session with `--run`; restarting the server creates a new live
session. `--resume-live runs/round-one-live-...` restores an existing session with
its conversations, preferences, traces and closing progress. Countries start
paused; completed meetings remain complete.

For an existing saved simulation:

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

## Concluding Round 1

During discussion, the head negotiator pushes for concrete follow-up work. Once
all members have contributed, the head may set `finish_discussion` and a reason
when the discussion is sufficiently resolved. **Ask head to conclude** requests
a fresh head assessment; the head can continue discussion if more work is needed.
Only the head can conclude, and no member can close before everyone contributes.

A conclusion ends discussion and starts one fresh `summary` call for every agent,
then a final `plan` call for the head. Summaries state conclusions, agreements,
unresolved issues and responsibilities. The head reads all summaries and assigns
every team member a task, deliverable and `target_round: 2`. Plan validation rejects
foreign assignees, missing team members and assignments targeting a different
round. These are planned tasks; they have not yet been performed.

Summaries and the action plan appear below the conversation. Closing calls do not
consume the discussion message budget. Pause also works during closing; Continue
retries a failed closing step without repeating completed summaries. The meeting
becomes complete only after all summaries and the action plan exist.

## What the viewer shows

- Country cards use the actual roster, including eight-country worlds.
- Discussion messages appear continuously as agent calls complete.
- Agent panels show only thinking traces, grouped by round. The latest trace opens
  by default; clicking a message opens its exact trace. Provider thought summaries
  appear when returned, with recorded decision summaries as an explicitly labelled
  fallback. Scripted policy summaries remain labelled.
- Preparation notes, token counters, prompt dumps, proposal metrics and repeated
  evidence panels are absent from the agent panel. The full audit remains saved.
- End-of-round summaries and the head's assignments belong to the country view.

`/api/round-one` exposes Round 1 discussion and closing results. The agent endpoint
with `view=thinking` returns only the round-grouped trace projection. Both enforce
existing researcher/member scopes. Public and foreign viewers cannot access
private delegation discussions, summaries, assignments or individual traces.
The server binds to loopback and is a local researcher's viewer.

## Bilaterals

Before each later round's team discussion, members talk privately one to one
(`climate_sim/bilaterals.py`). Each slot: every member and observer names up to
three people it wants to meet (`nominate`); the game master pairs requests across
delegations first, then mutual requests, then by rank, so nobody has two meetings
at once and no pair meets twice in a round. Each pair takes turns, opener first
(`bilateral`), until one side sets `end_conversation` or the message limit is
reached; a partner who did not ask for the meeting can decline on its first turn.
Each participant then reports to its own team (`debrief`, a `team_meeting_report`
event). Only the two participants see a conversation; reports are claims, not
commitments. `--bilateral-slots` (default 2, 0 disables) and `--bilateral-messages`
(default 8) configure them. This replaces the earlier tick-based `meet` and
`member_floor` protocols and the viewer's manual meeting sandbox.

After every slot a neutral Gemini rapporteur summarizes it, and after the last slot three
independent Gemini analysts each rate how cooperative every pair of delegations that talked
has been (-1 adversarial to +1 well coordinated); the game master averages them and keeps the
spread. Both are researcher-only (owners `researcher`); agents never see them.

## Charts

`frontend/viz.js` draws plain SVG charts (the CSP allows no chart library): where each
delegation's members stand on emission cuts and finance (home), how each member's proposal
moved per message (country view), who talked to whom in the bilaterals, and the analysts'
cooperation matrix. Every chart has tooltips and a data table.

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
`journalists`. They take part in bilaterals like delegates, and after
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

Following the repository hackathon guidance, run the code instead of adding tests:

```bash
uv run python -m climate_sim --backend offline --world eight --observers --output runs/check-new-run
node --check frontend/app.js
```

Also exercise a live country through discussion, head closure, every member's
summary and the next-round action plan. Check Pause/Continue during closing and
restoration of a saved session. Browser visual verification is separate.

Round 1 runs one worker per country, so active countries make provider calls concurrently.
Within each country, agents still speak sequentially and each call sees all prior team messages.
