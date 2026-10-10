

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
6. **Do not approve or merge your own trail.** The approvals gate needs a reviewer other than the author. Hand the trail URL to the user with the scores and how findings were handled, and let a human approve and merge it on entire.io.
