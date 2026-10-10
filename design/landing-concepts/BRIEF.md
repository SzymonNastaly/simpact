# Landing-page concepts brief

Simpact simulates a climate summit (model-UN style). 8 delegations (flags), each a team of three
AI agents cast as real politicians: head negotiator, economic minister, climate adviser.

- **Round 1 — delegation discussion.** Each delegation talks internally (3 agents), proposes a
  mandate, votes, and ends with a national position (annual reduction %, peak year, climate fund).
- **Round 2 — bilaterals.** Agents rank who they want to meet across delegations and hold
  one-to-one conversations; no fixed slots, an agent is paired again as soon as it is free (up to
  five meetings each). Meetings end in an agreement, an offer on the table, or a stall.
- Global readout: warming 2.15 → 2.10 °C, finance pledged 202 vs requested 375 $bn/yr.

Each concept is one standalone HTML file in this folder that loads `cast.js` then `data.js`
(`<script src="cast.js"></script><script src="data.js"></script>`) and renders from
`window.SIM` and `window.DATA`. Must keep: the flags and the portraits of the cast.

## Data API

- `SIM.PHOTOS[name]` → data-URI JPEG portrait (128×160, crop to circle/rect with object-fit).
- `SIM.flagSrc(delegationId)` → data-URI SVG for `<img src>`; `SIM.flagURL(id)` → CSS `url(...)`.
- `SIM.CREDITS` → `[{name, source, credit, license}]` — show a small photo-credits footer.
- `DATA.DELEGATIONS[]` → `{id, name, code, accent, rate:[initial, final], peak, fund, status, votes, thread:[[role, text]]}`.
  `fund` > 0 contributes, < 0 requests ($bn/yr).
- `DATA.AGENTS[]` → `{id:'usa.head_negotiator', delegation, role, name}`; `DATA.agent(id)`, `DATA.photo(id)`.
- `DATA.ROLES`, `DATA.ROLE_SHORT`, `DATA.ROLE_KEYS`, `DATA.BY_ID[id]`.
- `DATA.BILATERALS[]` → `{a, b, t, d, outcome:'agreed'|'offer'|'stalled', topic, lines:[['a'|'b', text]]}`
  (`a`/`b` are agent ids; `t` start minute, `d` duration minutes). `DATA.OUTCOMES[outcome]` → label.
- `DATA.GLOBAL` → `{warming:[2.15, 2.10], finance:{pledged, requested}, bilaterals, messages, agreements}`.

## Rules

- Vanilla HTML/CSS/JS, no build. Google Fonts allowed. No other external requests.
- Page is a landing page: a hero that sells Simpact, then how Round 1 and Round 2 are organised
  and shown, using the real cast/flags. Interactive where it helps (clicking a delegation opens its
  thread, clicking a bilateral shows its exchange, a Round 1 ↔ Round 2 switch, etc.).
- Must look spectacular and distinct from the other concepts. Work at 1440px and at 390px wide
  (no horizontal page scroll). Respect `prefers-reduced-motion`.
- Escape any text you inject with innerHTML (the data is trusted here, but keep the habit).
- Small footer line: "Concept N of 5 · ← all concepts" linking to `index.html`.
