This is a hackathon project: don't write (unit) tests. Check changes by running the code instead,
for example an offline `uv run python -m climate_sim` run.

## Use Concordia properly

Every agent turn runs through a Concordia engine and a game master. Don't write your own
scheduler, worker thread, thread pool or polling loop for agents, and don't wrap single
messages in throwaway one-step scenes.

- A phase is a schedule: `due()` (who acts next, in which stage), `resolve(stage, actions)`
  (what that causes), `round` and `done`. Run it with `scenes.ScheduledGameMaster`:
  - `run_schedule` (Simultaneous engine) for step-wise phases, e.g. each country's Round 1.
  - `run_async_schedule` with `concurrent=True` (Asynchronous engine) when agents must not
    wait for each other, e.g. Round 2 bilaterals, which have no rounds or slots: an agent is
    paired again as soon as it is free. The schedule tracks `inflight` assignments.
  - `run_scene` for fixed scenes in the batch runner.
- Pause and resume with Concordia's controls: `StepController` for step loops,
  `Asynchronous.pause()` / `play()` for async loops.
- Researcher-side LLM roles (rapporteur, analysts) are entities in the same loop, not side calls.
- Agents learn only from observations the game master builds; never pass state between agents directly.

<!-- entire-agent:begin -->
Read .entire/agent-guide.md for this repository's workflow, source inspection, and verification guidance.
@.entire/agent-guide.md
<!-- entire-agent:end -->
