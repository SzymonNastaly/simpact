

<!-- entire-agent:begin -->
Read .entire/agent-guide.md for this repository's workflow, source inspection, and verification guidance.
@.entire/agent-guide.md
<!-- entire-agent:end -->

## Ship changes through Entire Trails

Every change reaches `main` through a trail, never a direct push to `main`. A trail is one branch from intent to merge, reviewed on entire.io. Each push to a trail branch automatically runs the runners in `.entire/runners/`: a Problem/Solution summary, an AI code review that raises findings, and risk, drift, confidence and security scores.

1. **Branch.** Start a `trail/<short-name>` branch from an up-to-date `origin/main`. If another session is using the main checkout, create a git worktree instead of switching its branch.
2. **Open the trail with your intent once the first commit exists.** Run `entire trail create --base main --type feature|bug|task --title "<outcome>" --body "<problem, plan, how you will verify>"`. It pushes the branch and its checkpoints, then prints the trail URL; give that URL to the user.
3. **Push small commits with `git push`.** Each push re-runs the runners on the new head.
4. **Read the results once the runners finish.** `entire trail show --json` shows the phase, scores and merge gates, `entire trail finding list` shows open findings, and `entire trail watch --json` streams events live.
5. **Handle every open finding.** Fix real ones in a new commit, then `entire trail finding resolve <id> -m "<what changed>"`. Dismiss false positives with `entire trail finding dismiss <id> -m "<why it does not apply>"`. High-severity findings block the merge.
6. **Merge automatically; nobody approves by hand.** The approvals gate is off for this repo, so no human sign-off is needed. Run `python3 scripts/automerge_trails.py --wait <trail number>`: it waits for the runners, merges `main` into the branch if it fell behind, and merges the trail's pull request once Entire reports every blocking gate passed. If it stops with `blocked`, fix the reason it prints and push again. If you are not allowed to run it, tell the user the trail is ready and that `python3 scripts/automerge_trails.py` merges it. Then give the user the trail URL with the scores and how you handled the findings.
