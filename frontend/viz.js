'use strict';
// Charts for the rounds, drawn as plain SVG (the page's CSP allows no chart library).
// Roles carry the first three categorical slots, validated all-pairs for colour-vision deficiency;
// two sit below 3:1 on the page, so every chart has a legend, tooltips and a data table.
window.SimpactViz = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ROLE_COLORS = {head_negotiator:'var(--series-1)',economic_minister:'var(--series-2)',climate_adviser:'var(--series-3)'};
  const ROLE_ORDER = ['head_negotiator','economic_minister','climate_adviser'];
  const ROLE_LABELS = {head_negotiator:'Head negotiator',economic_minister:'Economic minister',climate_adviser:'Climate adviser',observer:'Observer'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roleKey = role => ROLE_ORDER.find(key => role === key || role.startsWith(key.split('_')[0])) || 'observer';
  const fmt = (value, digits = 1) => Number(value).toLocaleString('en-US', {maximumFractionDigits: digits, minimumFractionDigits: digits});
  const money = value => `${value > 0 ? '+' : value < 0 ? '−' : ''}$${fmt(Math.abs(value), 0)}B`;

  // One tooltip for every chart: any SVG element with data-tip shows it on hover or focus.
  let tip = null;
  function showTip(target, event) {
    if (!tip) { tip = document.createElement('div'); tip.className = 'viz-tip'; tip.setAttribute('role', 'tooltip'); document.body.appendChild(tip); }
    tip.innerHTML = target.getAttribute('data-tip');
    tip.hidden = false;
    const box = target.getBoundingClientRect();
    const x = event?.clientX ?? box.left + box.width / 2, y = event?.clientY ?? box.top;
    const width = tip.offsetWidth, height = tip.offsetHeight;
    tip.style.left = `${Math.min(window.innerWidth - width - 8, Math.max(8, x - width / 2)) + window.scrollX}px`;
    tip.style.top = `${(y - height - 14 < 8 ? y + 18 : y - height - 14) + window.scrollY}px`;
  }
  const hideTip = () => { if (tip) tip.hidden = true; };
  document.addEventListener('pointermove', event => { const target = event.target.closest?.('[data-tip]'); target ? showTip(target, event) : hideTip(); });
  document.addEventListener('focusin', event => { const target = event.target.closest?.('[data-tip]'); if (target) showTip(target); });
  document.addEventListener('focusout', hideTip);
  window.addEventListener('scroll', hideTip, {passive: true});

  function niceTicks(lo, hi, count = 5) {
    const span = hi - lo || 1, raw = span / count, power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map(m => m * power).find(m => m >= raw);
    // Extend to the first ticks at or beyond both ends, so no mark falls outside the axis.
    const ticks = [], end = Math.ceil(hi / step - 1e-9) * step;
    for (let v = Math.floor(lo / step + 1e-9) * step; v <= end + step * 1e-9; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
    return ticks;
  }
  const scale = (d0, d1, r0, r1) => value => r0 + (value - d0) / (d1 - d0 || 1) * (r1 - r0);
  const svg = (width, height, label) => `<svg class="viz-svg" viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(label)}">`;
  const legendRoles = (roles = ROLE_ORDER) => `<div class="viz-legend">${roles.map(role => `<span><i style="background:${ROLE_COLORS[role]}"></i>${ROLE_LABELS[role]}</span>`).join('')}</div>`;
  const dot = (x, y, role, tipText, r = 5) => `<g class="viz-dot" tabindex="0" data-tip="${esc(tipText)}"><circle cx="${x}" cy="${y}" r="12" fill="transparent"/><circle cx="${x}" cy="${y}" r="${r}" fill="${ROLE_COLORS[role] || 'var(--viz-muted)'}" stroke="var(--viz-surface)" stroke-width="2"/></g>`;

  // Each member's latest stated commitment: their last message, else their private starting proposal.
  function latestPositions(country) {
    return country.members.map(member => {
      const said = country.discussion.filter(event => event.actor === member.agent && event.payload.commitment);
      const commitment = said.length ? said[said.length - 1].payload.commitment : member.initial_preference;
      return commitment ? {member, commitment, source: said.length ? `message ${country.discussion.indexOf(said[said.length - 1]) + 1}` : 'private preparation'} : null;
    }).filter(Boolean);
  }

  // Home: where each delegation's members stand on the two headline numbers.
  function positions(el, countries, names) {
    const rows = countries.map(country => ({country, points: latestPositions(country)}));
    if (!rows.some(row => row.points.length)) { el.hidden = true; return; }
    el.hidden = false;
    const panels = [
      {key: 'annual_reduction_rate_percent', title: 'Annual emissions cut proposed', unit: '% per year', format: v => `${fmt(v)}%`},
      {key: 'climate_fund_billion_per_year', title: 'Climate finance proposed', unit: 'USD billion per year', format: money, diverging: true},
    ];
    const width = Math.max(320, el.clientWidth || 900), side = width >= 760;
    const rowHeight = 34, top = 30, bottom = 34;
    const panelWidth = side ? (width - 170 - 40) / 2 : width - 150;
    const height = top + rows.length * rowHeight + bottom;
    const charts = panels.map(panel => {
      const values = rows.flatMap(row => row.points.map(p => p.commitment[panel.key]));
      let lo = Math.min(...values), hi = Math.max(...values);
      if (panel.diverging) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); } else { lo = 0; }
      const ticks = niceTicks(lo, hi === lo ? lo + 1 : hi, side ? 4 : 5);
      const x = scale(ticks[0], ticks[ticks.length - 1], 8, panelWidth - 12);
      let body = ticks.map(t => `<line x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${height - bottom + 4}" class="${t === 0 && panel.diverging ? 'viz-zero' : 'viz-grid'}"/><text x="${x(t)}" y="${height - bottom + 20}" class="viz-tick" text-anchor="middle">${esc(panel.format(t))}</text>`).join('');
      if (panel.diverging) body += `<text x="${x(0) - 6}" y="${top - 12}" class="viz-tick" text-anchor="end">← requests</text><text x="${x(0) + 6}" y="${top - 12}" class="viz-tick">contributes →</text>`;
      rows.forEach((row, i) => {
        const y = top + i * rowHeight + rowHeight / 2;
        const xs = row.points.map(p => x(p.commitment[panel.key]));
        if (xs.length > 1) body += `<line x1="${Math.min(...xs)}" x2="${Math.max(...xs)}" y1="${y}" y2="${y}" class="viz-range"/>`;
        row.points.forEach(p => { body += dot(x(p.commitment[panel.key]), y, roleKey(p.member.role),
          `<strong>${esc(row.country.name)} · ${esc(names.roleName(p.member))}</strong><br>${esc(panel.format(p.commitment[panel.key]))} ${esc(panel.unit)}<br><span>From ${esc(p.source)}</span>`); });
      });
      return {panel, body};
    });
    const rowLabels = rows.map((row, i) => `<text x="0" y="${top + i * rowHeight + rowHeight / 2 + 4}" class="viz-label">${esc(row.country.name)}</text>`).join('');
    const block = (chart, labels) => `<figure class="viz-figure" style="flex:${(labels ? 150 : 0) + panelWidth} 1 0;min-width:0"><figcaption><strong>${esc(chart.panel.title)}</strong><span>${esc(chart.panel.unit)}</span></figcaption>${svg((labels ? 150 : 0) + panelWidth, height, chart.panel.title)}${labels ? rowLabels : ''}<g transform="translate(${labels ? 150 : 0},0)">${chart.body}</g></svg></figure>`;
    const table = `<details class="viz-table"><summary>Data table</summary><table><thead><tr><th>Delegation</th><th>Member</th><th>Cut % per year</th><th>Finance $B per year</th><th>From</th></tr></thead><tbody>${rows.flatMap(row => row.points.map(p => `<tr><td>${esc(row.country.name)}</td><td>${esc(names.roleName(p.member))}</td><td>${fmt(p.commitment.annual_reduction_rate_percent, 2)}</td><td>${fmt(p.commitment.climate_fund_billion_per_year, 1)}</td><td>${esc(p.source)}</td></tr>`)).join('')}</tbody></table></details>`;
    el.innerHTML = `<div class="viz-heading"><h2>Where the delegations stand</h2><p>Each dot is one agent’s latest proposal; the grey bar spans the disagreement inside a team.</p></div>${legendRoles()}
      <div class="viz-row${side ? ' side' : ''}">${charts.map((chart, i) => block(chart, !side || i === 0)).join('')}</div>${table}`;
  }

  // Country view: how each member's proposal moved through the conversation.
  function movement(el, country, names) {
    const members = country.members, discussion = country.discussion;
    if (!discussion.length) { el.hidden = true; return; }
    el.hidden = false;
    const width = Math.max(320, el.clientWidth || 900), height = 190, left = 52, right = 18, top = 14, bottom = 30;
    const x = scale(0, Math.max(discussion.length, 1), left, width - right);
    const series = members.map(member => {
      let current = member.initial_preference || null;
      const points = current ? [{i: 0, c: current, said: false}] : [];
      discussion.forEach((event, index) => { if (event.actor === member.agent && event.payload.commitment) { current = event.payload.commitment; points.push({i: index + 1, c: current, said: true}); } });
      if (current && points[points.length - 1].i < discussion.length) points.push({i: discussion.length, c: current, said: false});
      return {member, role: roleKey(member.role), points};
    }).filter(s => s.points.length);
    const chart = (key, title, unit, format, diverging) => {
      const values = series.flatMap(s => s.points.map(p => p.c[key]));
      let lo = Math.min(...values), hi = Math.max(...values);
      if (diverging) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
      const pad = (hi - lo) * .1 || 1, ticks = niceTicks(lo - (diverging ? 0 : pad), hi + pad, 4);
      const y = scale(ticks[0], ticks[ticks.length - 1], height - bottom, top);
      let body = ticks.map(t => `<line x1="${left}" x2="${width - right}" y1="${y(t)}" y2="${y(t)}" class="${t === 0 && diverging ? 'viz-zero' : 'viz-grid'}"/><text x="${left - 8}" y="${y(t) + 4}" class="viz-tick" text-anchor="end">${esc(format(t))}</text>`).join('');
      const xTicks = niceTicks(0, discussion.length, Math.min(discussion.length, 6)).filter(t => Number.isInteger(t));
      body += xTicks.map(t => `<text x="${x(t)}" y="${height - 10}" class="viz-tick" text-anchor="middle">${t === 0 ? 'start' : t}</text>`).join('');
      for (const s of series) {
        // A member's position holds until they speak again, so the line steps.
        const path = s.points.map((p, i) => i === 0 ? `M${x(p.i)},${y(p.c[key])}` : `H${x(p.i)}V${y(p.c[key])}`).join('');
        body += `<path d="${path}" fill="none" stroke="${ROLE_COLORS[s.role]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      }
      for (const s of series) for (const p of s.points.filter(point => point.said)) {
        body += dot(x(p.i), y(p.c[key]), s.role, `<strong>${esc(names.roleName(s.member))}</strong> · message ${p.i}<br>${esc(format(p.c[key]))} ${esc(unit)}`, 4);
      }
      return `<figure class="viz-figure"><figcaption><strong>${esc(title)}</strong><span>${esc(unit)} · by message</span></figcaption>${svg(width, height, title)}${body}</svg></figure>`;
    };
    const table = `<details class="viz-table"><summary>Data table</summary><table><thead><tr><th>#</th><th>Speaker</th><th>Cut % per year</th><th>Finance $B per year</th><th>Peak year</th></tr></thead><tbody>${discussion.map((event, i) => { const member = members.find(m => m.agent === event.actor); const c = event.payload.commitment || {}; return `<tr><td>${i + 1}</td><td>${esc(member ? names.roleName(member) : event.actor)}</td><td>${c.annual_reduction_rate_percent != null ? fmt(c.annual_reduction_rate_percent, 2) : ''}</td><td>${c.climate_fund_billion_per_year != null ? fmt(c.climate_fund_billion_per_year, 1) : ''}</td><td>${esc(c.emissions_peak_year ?? '')}</td></tr>`; }).join('')}</tbody></table></details>`;
    el.innerHTML = `<div class="viz-heading"><h2>How positions moved</h2><p>Each agent’s proposal after every message it sent, starting from its private preparation.</p></div>${legendRoles(ROLE_ORDER.filter(role => series.some(s => s.role === role)))}
      ${chart('annual_reduction_rate_percent', 'Annual emissions cut', '% per year', v => `${fmt(v)}%`)}
      ${chart('climate_fund_billion_per_year', 'Climate finance', 'USD billion per year', money, true)}${table}`;
  }

  // Bilaterals: every agent around a ring, grouped by delegation; each conversation is a link.
  function network(el, data, names, {slot = 'all', selected = null, onSelect, onSlot} = {}) {
    const meetings = (data.rounds || []).flatMap(round => round.slots.flatMap(s => s.meetings.map(m => ({...m, roundNumber: round.round}))));
    const continuous = data.live?.allocator;
    const slots = [...new Set(meetings.map(m => m.slot))].sort();
    if (!meetings.length) { el.hidden = true; return; }
    el.hidden = false;
    const shown = meetings.filter(m => continuous || slot === 'all' || m.slot === slot);
    const people = data.participants;
    const groups = [...new Set(people.map(p => p.delegation))];
    const width = Math.max(320, el.clientWidth || 900), height = Math.min(width, 620);
    const cx = width / 2, cy = height / 2, radius = Math.min(width, height) / 2 - (width < 560 ? 70 : 110);
    const slotsTotal = people.length + groups.length;
    const position = {};
    let index = 0;
    const labels = [];
    for (const group of groups) {
      const members = people.filter(p => p.delegation === group);
      const start = index;
      for (const p of members) {
        const angle = -Math.PI / 2 + index / slotsTotal * 2 * Math.PI;
        position[p.agent] = {x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle), angle, p};
        index += 1;
      }
      const middle = -Math.PI / 2 + (start + (members.length - 1) / 2) / slotsTotal * 2 * Math.PI;
      labels.push({text: members[0].delegation_name, angle: middle});
      index += 1;
    }
    const busy = {};
    for (const m of shown) for (const id of [m.initiator, m.partner]) busy[id] = (busy[id] || 0) + 1;
    const declined = m => m.status === 'closed' && m.messages.length <= 1 && m.closed_by === m.partner;
    let edges = '', hits = '';
    for (const m of shown) {
      const a = position[m.initiator], b = position[m.partner];
      if (!a || !b) continue;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const qx = cx + (mx - cx) * .35, qy = cy + (my - cy) * .35;
      const d = `M${a.x},${a.y} Q${qx},${qy} ${b.x},${b.y}`;
      const widthPx = 1.5 + Math.min(m.messages.length, 10) * .35;
      const kind = declined(m) ? 'declined' : m.mutual ? 'mutual' : 'one-sided';
      const state = selected ? (m.id === selected ? ' selected' : ' faded') : '';
      edges += `<path d="${d}" class="viz-edge ${kind}${state}" stroke-width="${m.id === selected ? widthPx + 1.5 : widthPx}"/>`;
      const tipText = `<strong>${esc(names.fullName(m.initiator))} ↔ ${esc(names.fullName(m.partner))}</strong><br>${esc(m.topic)}<br><span>${continuous ? 'Round 2' : `Slot ${m.slot}`} · ${m.messages.length} messages · ${kind === 'mutual' ? 'both asked' : kind === 'declined' ? 'declined' : 'one side asked'}</span>`;
      hits += `<path d="${d}" class="viz-edge-hit" data-meeting="${esc(m.id)}" data-tip="${esc(tipText)}" tabindex="0" role="button" aria-label="${esc(`${names.fullName(m.initiator)} and ${names.fullName(m.partner)}: ${m.topic}`)}"/>`;
    }
    let nodes = '';
    for (const {x, y, p} of Object.values(position)) {
      const role = roleKey(p.role), count = busy[p.agent] || 0;
      const tipText = `<strong>${esc(names.fullName(p.agent))}</strong><br><span>${count} ${count === 1 ? 'conversation' : 'conversations'}${continuous || slot === 'all' ? '' : ` in slot ${slot}`} </span>`;
      const mark = role === 'observer'
        ? `<rect x="${x - 6}" y="${y - 6}" width="12" height="12" transform="rotate(45 ${x} ${y})" fill="var(--viz-muted)" stroke="var(--viz-surface)" stroke-width="2"/>`
        : `<circle cx="${x}" cy="${y}" r="7" fill="${ROLE_COLORS[role]}" stroke="var(--viz-surface)" stroke-width="2"${count ? '' : ' opacity=".35"'}/>`;
      nodes += `<g class="viz-dot" tabindex="0" data-tip="${esc(tipText)}"><circle cx="${x}" cy="${y}" r="13" fill="transparent"/>${mark}</g>`;
    }
    const groupLabels = labels.map(({text, angle}) => {
      const r = radius + 22, x = cx + r * Math.cos(angle), y = cy + r * Math.sin(angle);
      const anchor = Math.abs(Math.cos(angle)) < .25 ? 'middle' : Math.cos(angle) > 0 ? 'start' : 'end';
      return `<text x="${x}" y="${y + 4}" class="viz-label" text-anchor="${anchor}">${esc(text)}</text>`;
    }).join('');
    const pills = !continuous && slots.length > 1 ? `<div class="viz-pills" role="group" aria-label="Show slot">${['all', ...slots].map(s => `<button type="button" data-slot="${s}" aria-pressed="${s === slot}">${s === 'all' ? 'All slots' : `Slot ${s}`}</button>`).join('')}</div>` : '';
    const mutual = shown.filter(m => m.mutual).length, cross = shown.filter(m => people.find(p => p.agent === m.initiator)?.delegation !== people.find(p => p.agent === m.partner)?.delegation).length;
    el.innerHTML = `<div class="viz-heading"><h2>Who talked to whom</h2><p>${shown.length} conversations · ${cross} across delegations · ${mutual} both asked for. Thicker links had more messages; select one to read it.</p></div>
      ${pills}<div class="viz-legend">${ROLE_ORDER.map(role => `<span><i style="background:${ROLE_COLORS[role]}"></i>${ROLE_LABELS[role]}</span>`).join('')}<span><i class="diamond"></i>Observer</span><span><b class="line mutual"></b>Both asked</span><span><b class="line one-sided"></b>One side asked</span><span><b class="line declined"></b>Declined</span></div>
      <figure class="viz-figure network">${svg(width, height, 'Network of bilateral conversations')}<g>${edges}</g><g>${hits}</g><g>${nodes}</g>${groupLabels}</svg></figure>`;
    el.querySelectorAll('[data-meeting]').forEach(path => {
      path.addEventListener('click', () => onSelect?.(path.dataset.meeting));
      path.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect?.(path.dataset.meeting); } });
    });
    el.querySelectorAll('[data-slot]').forEach(button => button.addEventListener('click', () => onSlot?.(button.dataset.slot === 'all' ? 'all' : Number(button.dataset.slot))));
  }

  // Diverging blue (cooperative) <-> red (adversarial) through a neutral grey; never a hue at zero.
  const POLES = {cool: [28, 92, 171], warm: [184, 49, 47], mid: [240, 239, 236]};
  function divergingColor(score) {
    const pole = score >= 0 ? POLES.cool : POLES.warm, t = Math.min(1, Math.abs(score));
    const rgb = POLES.mid.map((m, i) => Math.round(m + (pole[i] - m) * t));
    const light = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255 > .55;
    return {fill: `rgb(${rgb.join(',')})`, ink: light ? 'var(--ink)' : '#ffffff'};
  }

  // Bilaterals: how cooperative each pair of delegations was, averaged over independent Gemini analysts.
  function relations(el, data, names) {
    const round = (data.rounds || []).filter(r => r.relations).slice(-1)[0];
    if (!round) { el.hidden = true; return; }
    el.hidden = false;
    const pairs = round.relations.pairs, analysts = round.relations.analysts;
    const ids = (data.delegations || []).map(d => d.id).filter(id => pairs.some(p => p.a === id || p.b === id));
    const nameOf = id => data.delegations.find(d => d.id === id)?.name || id;
    const lookup = (a, b) => pairs.find(p => (p.a === a && p.b === b) || (p.a === b && p.b === a));
    const width = Math.max(320, el.clientWidth || 900), labelW = width < 560 ? 92 : 170;
    const cell = Math.max(30, Math.min(64, (width - labelW - 8) / Math.max(ids.length, 1)));
    const top = width < 560 ? 92 : 120, height = top + cell * ids.length + 6;
    const short = text => width < 560 && text.length > 14 ? `${text.slice(0, 13)}…` : text;
    let body = '';
    ids.forEach((id, i) => {
      body += `<text x="${labelW - 10}" y="${top + i * cell + cell / 2 + 4}" class="viz-label" text-anchor="end">${esc(short(nameOf(id)))}</text>`;
      const cx = labelW + i * cell + cell / 2;
      body += `<text x="${cx}" y="${top - 10}" class="viz-label" transform="rotate(-40 ${cx} ${top - 10})">${esc(short(nameOf(id)))}</text>`;
    });
    ids.forEach((a, row) => ids.forEach((b, col) => {
      const x = labelW + col * cell, y = top + row * cell;
      if (a === b) { body += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" class="viz-cell-self"/>`; return; }
      const pair = lookup(a, b);
      if (!pair) { body += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" class="viz-cell-empty" data-tip="${esc(`<strong>${esc(nameOf(a))} · ${esc(nameOf(b))}</strong><br><span>No conversation between them</span>`)}"/>`; return; }
      const color = divergingColor(pair.score);
      const tipText = `<strong>${esc(nameOf(a))} · ${esc(nameOf(b))}</strong><br>${pair.score >= 0 ? 'Cooperative' : 'Adversarial'} ${pair.score > 0 ? '+' : ''}${fmt(pair.score, 2)} · analysts ranged ${fmt(pair.low, 2)} to ${fmt(pair.high, 2)}<br>${pair.ratings.map(r => `<span>${esc(r.analyst)} (${r.score > 0 ? '+' : ''}${fmt(r.score, 2)}): ${esc(r.rationale)}</span>`).join('<br>')}`;
      body += `<g tabindex="0" class="viz-dot" data-tip="${esc(tipText)}"><rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" rx="4" fill="${color.fill}"/>${cell >= 40 ? `<text x="${x + cell / 2}" y="${y + cell / 2 + 4}" text-anchor="middle" class="viz-cell-value" fill="${color.ink}">${pair.score > 0 ? '+' : ''}${fmt(pair.score, 1)}</text>` : ''}</g>`;
    }));
    const ranked = [...pairs].sort((p, q) => q.score - p.score);
    const best = ranked[0], worst = ranked[ranked.length - 1];
    const describe = p => `${esc(nameOf(p.a))} · ${esc(nameOf(p.b))} (${p.score > 0 ? '+' : ''}${fmt(p.score, 2)})`;
    const legend = `<div class="viz-scale"><span>Adversarial −1</span><i></i><span>+1 Cooperative</span><span class="viz-scale-empty"><b></b>No conversation</span></div>`;
    const table = `<details class="viz-table"><summary>Data table</summary><table><thead><tr><th>Delegations</th><th>Average</th><th>Range</th><th>Analysts’ reasons</th></tr></thead><tbody>${ranked.map(p => `<tr><td>${esc(nameOf(p.a))} · ${esc(nameOf(p.b))}</td><td>${fmt(p.score, 2)}</td><td>${fmt(p.low, 2)} to ${fmt(p.high, 2)}</td><td>${p.ratings.map(r => esc(r.rationale)).join('<br>')}</td></tr>`).join('')}</tbody></table></details>`;
    el.innerHTML = `<div class="viz-heading"><h2>How the delegations relate</h2><p>${analysts} independent Gemini analysts read every conversation between two countries and rated it from adversarial (−1) to well coordinated (+1); each cell is their average. Researcher only.</p></div>
      <p class="viz-callout">Most cooperative: <strong>${describe(best)}</strong>${pairs.length > 1 ? ` · ${worst.score < 0 ? 'Most adversarial' : 'Least cooperative'}: <strong>${describe(worst)}</strong>` : ''}</p>${legend}
      <figure class="viz-figure">${svg(width, height, 'Cooperation between delegations')}${body}</svg></figure>${table}`;
  }

  return {positions, movement, network, relations};
})();
