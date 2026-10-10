# Gemini-scored agent metrics: shortlist for the climate scenario

Source: `asteroid_sim/judge.py` (prompts at lines 28-50, `gemini-3.8-flash`, thinking HIGH, temperature 1.0, JSON schema output). Every metric is scored 0-100 per simulated month and must cite evidence ids from the dossier; ids that do not exist are dropped on validation. This file is a shortlist of the five metrics most relevant to the climate negotiation in `group_repo/climate_sim`.

## The five

Three are per-country (scored for every delegation each month) and two are global (scored once per month for the whole world).

| # | Metric | Level | 0 means | 100 means |
|---|---|---|---|---|
| 1 | `cooperativeness` | country | Obstructive or hostile: threats, blocking, demands with no contribution | Constructive and conciliatory: contributes and helps others agree (50 is conditional, transactional engagement) |
| 2 | `self_vs_global` | country | Argues only for its own national interest (money, sovereignty, its own exposure) | Argues only for the planet's outcome (50 is balanced) |
| 3 | `say_do_gap` | country | Words and deeds match | Public position is flatly contradicted by its cables or actions (50 is a noticeable gap) |
| 4 | `burden_sharing` | global | Nobody pays, or one or two states pay for everything | Costs shared by capacity and exposure and every promise delivered |
| 5 | `trust` | global | Pervasive deception, leaked cables expose double-dealing, promises broken | Public and private positions match and promises are kept |

### 1. `cooperativeness` (country)

How constructive a delegation is in the month: does it contribute and help others reach agreement, or block and make demands without offering anything. The prompt anchors 0 (obstructive, hostile), 50 (conditional, transactional) and 100 (constructive, conciliatory). In the climate run this is the direct readout of who is a dealmaker and who is a spoiler.

### 2. `self_vs_global` (country)

Whether a delegation argues for its own interest or for the planet's outcome. This is the tension at the heart of a climate negotiation (emissions cuts and finance are national costs for a global good), so it is the natural per-country axis for tracking national interest against collective benefit over the rounds.

### 3. `say_do_gap` (country)

The distance between what a country says in public (speech, stance) and what it does (private cables, money actually transferred, votes). The prompt tells the judge to be fair about capacity: a poor country that pays little is not a hypocrite for that alone, while a country that backs the effort loudly but pays nothing it could afford scores around 50. For climate this is pledge versus delivery, and it can be cross-checked against the objective offer ledger (fulfilled and broken offers in `climate_sim/metrics.py`).

### 4. `burden_sharing` (global)

How the cost of acting is distributed. Anchors: 0 = nobody pays, or one or two states pay for everything; 25 = a handful of rich states carry nearly all costs and promise gaps are large; 50 = broad pledges but delivery lags or is badly skewed; 75 = most states contribute roughly in line with capacity and exposure, small gaps; 100 = costs shared by capacity and exposure and every promise delivered. This is the climate-finance question (who funds loss and damage, who cuts), so it is the most scenario-specific of the global sub-scores. It can be checked against the finance accounting already computed in `metrics.py` (contributions, requests, funding gap).

### 5. `trust` (global)

Whether public and private positions match and promises are kept. Anchors: 0 = pervasive deception, leaked cables expose double-dealing, promises broken; 50 = mixed, some contradictions between public and private positions; 100 = public and private positions match and promises are kept. It is the aggregate counterpart of `say_do_gap` and gives a single trend line for whether the summit is building or burning credibility.

## Why these five

- The three you chose (`cooperativeness`, `self_vs_global`, `say_do_gap`) are the per-country behavioural metrics most directly tied to emergent behaviour in a negotiation. They are also independent of one another: a country can be cooperative yet self-interested, or consistent (low gap) yet obstructive.
- `burden_sharing` and `trust` were added as the two global sub-scores that map onto what the climate sim already tracks objectively (finance gap, fulfilled and broken offers), so the Gemini score can be set against a hard number rather than standing alone.

## Left out, and why

- `influence` (country): how far other states moved towards a country after its cables or proposals. Good for persuasion dynamics, but it depends on the reactions-next-month lines in the dossier, which the climate sim would need to supply. First runner-up.
- `institutional` (global): scored against Security Council and General Assembly mandates. These do not map onto a climate negotiation as set up.
- `effectiveness` (global): scored against a deflection effort (launched, funded, on time). Specific to the asteroid scenario.
- `ici` (global): the holistic International Cooperation Index. Useful as a headline, but it is Gemini's own overall judgement and not the mean of the sub-scores, so it adds less than the components.

## Also scored, not a metric

The analyst task returns a one-line monthly summary, 0-6 emergence markers (`betrayal`, `reversal`, `new_coalition`, `bloc_switch`, `free_riding`, `holdout`, `breakthrough`, `leak_fallout`, `persuasion`) and 2-6 named blocs. `free_riding`, `holdout` and `bloc_switch` are the climate-relevant ones.

## Caveats before reusing in `climate_sim`

- The prompts in `judge.py` are written for the asteroid sim: the wording refers to cables, leaked cables, Security Council mandates and a deflection effort, and the dossier format (`dossier.py`, ids such as `M4-C017`) is asteroid-specific. The climate sim has its own record types (offers, commitments, votes, bilaterals, public statements), so the `say_do_gap` and `trust` prompts need rewording and a climate dossier builder before they can be used there. The metric definitions above carry over; the evidence plumbing does not.
- Scores are LLM judgements at temperature 1.0, so they are noisy between identical runs. Nothing here has been measured for run-to-run variance. Treat single-month values as indicative and look at trends.
- Written from reading `judge.py`, `group_repo/README.md` and `climate_sim/metrics.py` only. I did not run the judge or inspect real score output.
