'use strict';
(() => {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roleNames = {head_negotiator:'Head negotiator',economic_minister:'Economic minister',climate_adviser:'Climate adviser'};
  const roleClass = role => role.startsWith('head') ? 'head' : role.startsWith('economic') ? 'economic' : 'climate';
  const roleLetter = role => roleClass(role)[0].toUpperCase();
  const priorities = {head_negotiator:'Diplomacy · agreements · coalitions',economic_minister:'Growth · competitiveness · finance',climate_adviser:'Emissions · climate risk · credibility'};
  const codes = {usa:'US',eu:'EU',china:'CN',india:'IN',other_developed:'OD',other_developing:'OG'};
  const s = {scope:'researcher',country:'china',round:'1',workspace:'delegation',agent:'china.climate_adviser',acting:'china.climate_adviser',meeting:null,replay:null,hub:null,busy:false,cursor:Infinity,playing:false,timer:null,trace:null,traceCache:new Map(),tracePending:new Set(),expanded:new Set(['china']),loadVersion:0,demo:new URLSearchParams(location.search).get('demo') === '1'};
  const stageNames = {prepare:'Preparation',internal:'Team discussion',ballot:'Internal vote',mandate:'Team mandate',summit:'Public statement',announce:'Public statement',negotiate:'Negotiation',member_floor:'Member meeting',meeting:'Member meeting',commit:'Commitment',submit:'Commitment',final_commitment:'Commitment'};
  const member = id => s.replay.roster.find(a => a.agent === id);
  const country = id => s.replay.delegations.find(d => d.id === id);
  const memberName = id => {const a = member(id); return a ? `${country(a.delegation).name} / ${roleNames[a.role] || a.role}` : id;};
  const shortName = id => {const a = member(id); return a ? `${codes[a.delegation]} · ${roleNames[a.role] || a.role}` : id;};
  const controller = id => s.hub?.participants.find(p => p.id === id)?.controller || 'agent';
  const roundData = () => s.replay.rounds[s.round];
  const teamData = () => roundData().teams[s.country];
  const fmtMoney = n => `${n > 0 ? '+' : n < 0 ? '−' : ''}$${Math.abs(n).toFixed(1)}B/yr`;
  const fmtRate = c => c ? `${c.annual_reduction_rate_percent.toFixed(2)}%` : 'Private';
  const empty = (title,text) => `<div class="empty-state"><strong>${esc(title)}</strong>${esc(text)}</div>`;
  const stageName = stage => stageNames[stage] || String(stage).replaceAll('_',' ');
  const eventNumber = e => Number((e?.id || '').split('-').pop()) || 0;
  function timeline() {
    const records = roundData().timeline || [];
    if (s.workspace === 'delegation') return records.length ? records.filter(e => (e.actor && member(e.actor)?.delegation === s.country && ['agent_trace','internal_speech','internal_ballot','individual_preparation'].includes(e.kind)) || (e.kind === 'national_mandate' && e.owners.includes(s.country))) : teamData().speeches;
    return records.filter(e => e.kind.startsWith('meeting_') || ['summit_speech','team_meeting_report'].includes(e.kind));
  }
  function currentEvent() {const events = timeline(); return events[Math.min(events.length,s.cursor)-1];}
  function seen(e) {return s.cursor === Infinity || eventNumber(e) <= eventNumber(currentEvent());}
  function stopPlayback() {clearTimeout(s.timer);s.playing=false;}
  function resetPlayback() {stopPlayback();s.cursor=Infinity;s.trace=null;}
  function clearPrivateViews() {
    stopPlayback();s.switching=true;s.loadVersion++;s.traceCache.clear();s.tracePending.clear();
    ['internal-transcript','trace-timeline','trace-detail','floor-trace-timeline','floor-trace-detail','agent-perspective','mandate','alignment','meeting-list','room-messages','room-members','meeting-outcomes','team-notifications','agent-meetings'].forEach(id => {const e = $(`#${id}`);if(e)e.innerHTML='';});
  }

  async function api(path, options) {
    if (s.demo) return demoApi(path,options);
    const response = await fetch(path, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }
  function showError(error) { $('#error').textContent = error.message || String(error); $('#error').hidden = false; }
  async function load() {
    const version = ++s.loadVersion;
    const [replay,hub] = await Promise.all([api(`/api/replay?scope=${encodeURIComponent(s.scope)}`),api(`/api/meetings?scope=${encodeURIComponent(s.scope)}`)]);
    if (version !== s.loadVersion) return;
    s.switching=false;
    s.replay = replay; s.hub = hub; s.scope = replay.scope;
    if (hub.human_role) s.acting = hub.human_role;
    if (!s.replay.rounds[s.round]) s.round = Object.keys(s.replay.rounds)[0];
    if (s.scope !== 'researcher' && s.scope !== 'public') {
      s.country = member(s.scope).delegation;
      s.agent = s.scope;
      s.acting = s.scope;
    }
    s.expanded.add(s.country);
    render();
  }
  async function act(operation, extras = {}) {
    if (s.busy) return;
    s.busy = true; $('#error').hidden = true;
    try { resetPlayback();await api('/api/actions',{method:'POST',headers:{'Content-Type':'application/json','X-Simpact-Lab':'1'},body:JSON.stringify({operation,actor:s.acting,round:Number(s.round),...extras})}); await load(); }
    catch (error) { showError(error); }
    finally { s.busy = false; }
  }
  function options(selected) {
    return s.replay.roster.map(a => `<option value="${esc(a.agent)}" ${a.agent === selected ? 'selected' : ''}>${esc(memberName(a.agent))}</option>`).join('');
  }
  function avatar(a) {return `<span class="agent-avatar ${roleClass(a.role)}" aria-hidden="true">${roleLetter(a.role)}</span>`;}
  function policy(c) {
    if (!c) return '<div class="private-empty">This preference is not available in this information view.</div>';
    const rows = [['Emissions peak',c.emissions_peak_year],['Reductions start',c.reduction_start_year],['Annual reduction',fmtRate(c)],['Deforestation prevention',`${c.deforestation_prevention_percent}%`],['Afforestation',`${c.afforestation_percent}%`],['Climate finance',fmtMoney(c.climate_fund_billion_per_year)]];
    return `<dl class="policy-list">${rows.map(([k,v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;
  }
  function renderChrome() {
    const members = s.replay.roster;
    $('#roster-count').textContent = `${s.replay.delegations.length} / ${members.length}`;
    $('#run-label').textContent = s.demo ? '18 agents · frontend demo' : `${members.length} members · ${s.replay.config.backend} replay`;
    $('#viewer').innerHTML = `<option value="researcher">Researcher · full audit</option><option value="public">Public summit</option>${options(s.scope)}`;
    $('#viewer').value = s.scope;
    $('#viewer').disabled = !!s.hub.human_role;
    $('#claim-role').disabled = !s.hub.human_role && ['researcher','public'].includes(s.scope);
    $('#claim-role').textContent = s.hub.human_role ? 'Leave human role' : 'Play this role';
    $('#claim-role').classList.toggle('primary',!!s.hub.human_role);
    $('#countries').innerHTML = s.replay.delegations.map(d => `<div class="country-group"><button class="country-button" type="button" data-country="${d.id}" aria-pressed="${d.id === s.country}" aria-expanded="${s.expanded.has(d.id)}"><span class="country-code">${codes[d.id]}</span><span class="country-copy"><span class="country-name">${esc(d.name)}</span><span class="country-meta">${d.members.length} members</span></span><span class="country-chevron">${s.expanded.has(d.id) ? '⌄' : '›'}</span></button>${s.expanded.has(d.id) ? `<div class="country-members">${d.members.map(id => {const a=member(id);return `<button type="button" class="country-member" data-agent="${esc(id)}" aria-pressed="${id === s.agent}"><span class="role-dot ${roleClass(a.role)}"></span>${esc(roleNames[a.role] || a.role)}</button>`;}).join('')}</div>` : ''}</div>`).join('');
    $('#round-switch').innerHTML = Object.keys(s.replay.rounds).map(r => `<button type="button" data-round="${r}" aria-pressed="${r === s.round}">Round ${r}</button>`).join('');
    $('#workspace-title').textContent = s.workspace === 'delegation' ? `${country(s.country).name} delegation` : 'The negotiation floor';
    $('#workspace-eyebrow').textContent = s.workspace === 'delegation' ? 'INSIDE THE DELEGATION' : 'BETWEEN DELEGATIONS';
    $('#workspace-description').textContent = s.workspace === 'delegation' ? `${country(s.country).members.length} individual agents. Select one to follow its decisions.` : '18 individuals. Private conversations. Shared consequences.';
    $$('.workspace-tabs button').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.workspace === s.workspace)));
    $('#delegation-workspace').hidden = s.workspace !== 'delegation';
    $('#floor-workspace').hidden = s.workspace !== 'floor';
    const offline = s.replay.config.backend === 'offline' ? 'Saved offline run · scripted agents, not Gemini.' : 'Saved Gemini run · not a live session.';
    $('#mode-notice').classList.toggle('human-notice',!!s.hub.human_role);
    $('#mode-notice').textContent = s.hub.human_role ? `You control ${memberName(s.hub.human_role)} · manual controls, no AI replies.` : s.demo ? 'Frontend demo · illustrative conversations and decision summaries · no models running.' : `${offline} ${s.scope === 'researcher' ? 'Private researcher audit.' : s.scope === 'public' ? 'Public records only.' : 'Your private state + your team + your meetings.'}`;
    $('#replay-note').textContent = s.demo ? 'Frontend prototype · example data, not a simulation result.' : 'Recorded replay. Manual meeting actions do not rerun the simulation.';
    const outcome = roundData().climate_outcome;
    $('#climate-summary').textContent = outcome ? `${outcome.is_mock ? 'MOCK, not C-ROADS. ' : ''}Round ${s.round}: ${outcome.warming_celsius.toFixed(2)}°C. ${outcome.explanation}` : 'No climate outcome recorded.';
  }
  function renderAgents() {
    const team = teamData(), members = s.replay.roster.filter(a => a.delegation === s.country);
    $('#team-summary').textContent = `${members.length} members · ${s.replay.config.composition} priorities`;
    if (!members.some(a => a.agent === s.agent)) s.agent = members[0].agent;
    $('#agent-cards').innerHTML = members.map(a => {
      const position = s.cursor === Infinity ? team.positions[a.agent] : team.speeches.filter(e => e.actor === a.agent && seen(e)).at(-1)?.payload.commitment || team.starting_positions[a.agent];
      const support = team.mandate?.mandate.internal_support[a.agent];
      return `<button type="button" class="agent-card" data-agent="${esc(a.agent)}" aria-pressed="${a.agent === s.agent}"><span class="agent-card-header">${avatar(a)}<span class="controller-pill ${controller(a.agent) === 'human' ? 'human' : ''}">${controller(a.agent) === 'human' ? 'HUMAN' : 'AGENT'}</span></span><h3>${esc(roleNames[a.role] || a.role)}</h3><span class="agent-priority">${esc(priorities[a.role] || a.personality)}</span><span class="agent-model">${esc(a.backend)}</span><span class="agent-position"><span class="agent-position-value">${fmtRate(position)}<span class="agent-position-label">latest shared preference</span></span>${support === undefined ? '<span class="support-label">Private team record</span>' : `<span class="support-label ${support ? '' : 'dissent'}">${support ? '✓ Approved mandate' : 'Did not approve'}</span>`}</span></button>`;
    }).join('');
    const active = currentEvent();
    $$('#agent-cards [data-agent]').forEach(b => b.classList.toggle('is-speaking',s.cursor !== Infinity && b.dataset.agent === active?.actor));
  }
  function renderDiscussion() {
    const team = teamData();
    const visible = team.speeches.filter(seen);
    $('#discussion-access').textContent = team.speeches.length ? `Round ${s.round} · ${visible.length}/${team.speeches.length} turns · team-only` : 'Private room';
    $('#internal-transcript').innerHTML = visible.length ? visible.map((e,i) => {
      const a = member(e.actor), c = e.payload.commitment;
      const trace = (roundData().traces || []).find(t => t.actor === e.actor && t.payload.resolution_event_ids?.includes(e.id));
      return `<article class="speech ${e.actor === s.agent ? 'selected-speech' : ''} ${currentEvent()?.id === e.id ? 'is-current' : ''}">${avatar(a)}<div class="speech-body"><div class="speech-heading"><strong>${esc(roleNames[a.role] || a.role)}</strong><span>Turn ${i+1}</span></div><p class="speech-text">${esc(e.payload.speech)}</p><button type="button" class="inspect-message" ${trace ? `data-trace="${esc(trace.id)}"` : ''} data-message-agent="${esc(e.actor)}">↗ Inspect this member’s decision</button><div class="speech-policy"><span>${fmtRate(c)} cuts/yr</span><span>Peak ${c.emissions_peak_year}</span></div></div></article>`;
    }).join('') : team.speeches.length ? empty('Before the first turn','Press Play or step forward to watch the team deliberate.') : empty('Private delegation room','This information view cannot access the team conversation.');
    const a = member(s.agent);
    $('#inspector-role').textContent = roleNames[a.role] || a.role;
    const preparation = a.private_strategy ? `<div class="inspector-title">Individual-private preparation strategy</div><p class="strategy">${esc(a.private_strategy)}</p>` : '<div class="private-empty">Individual-private strategy is restricted to its owner and the researcher. Team colleagues only see what was shared.</div>';
    $('#agent-perspective').innerHTML = `<div class="inspector-content">${preparation}<div class="table-subheading">${s.round === '1' ? 'Starting private preference' : 'Starting shared position from prior round'}</div>${policy(team.starting_positions[a.agent])}<div class="table-subheading">Last position shared in this round</div>${policy(team.positions[a.agent])}</div>`;
  }
  function renderAlignment() {
    const team = teamData(), positions = s.cursor === Infinity ? team.positions : Object.fromEntries(team.speeches.filter(seen).map(e => [e.actor,e.payload.commitment]));
    const entries = Object.entries(positions);
    if (!entries.length) {$('#alignment').innerHTML = empty('Alignment is private','No internal positions are available in this view.');renderMandate();return;}
    const max = Math.ceil(Math.max(...entries.map(([,c]) => c.annual_reduction_rate_percent),team.mandate?.mandate.approved_commitment.annual_reduction_rate_percent || 0));
    $('#alignment').innerHTML = `<div class="inspector-content"><div class="alignment-summary"><div><div class="inspector-title">Mean pairwise policy distance</div><div class="distance-values">${team.disagreement_before == null ? '—' : team.disagreement_before.toFixed(3)}<span>→</span>${team.disagreement_after == null ? '—' : team.disagreement_after.toFixed(3)}</div></div></div><p class="alignment-caption">Lower means closer positions across all six commitment fields. Not a consensus probability. A missing start value means private preferences are unavailable.</p>${entries.map(([id,c]) => {const a = member(id), start = team.starting_positions[id];return `<div class="alignment-row"><div class="alignment-row-label"><span>${esc(roleNames[a.role] || a.role)}</span><span>${start ? `${fmtRate(start)} → ` : ''}${fmtRate(c)}</span></div><div class="alignment-track ${roleClass(a.role)}"><span class="current-position" style="width:${Math.max(0,Math.min(100,c.annual_reduction_rate_percent/Math.max(1,max)*100))}%"></span></div></div>`;}).join('')}<div class="alignment-axis"><span>0% annual reduction</span><span>${max}%</span></div></div>`;
    renderMandate();
  }
  function renderMandate() {
    const mandateEvent=(roundData().timeline || []).find(e => e.kind === 'national_mandate' && e.owners.includes(s.country));
    const mandate = s.cursor !== Infinity && mandateEvent && !seen(mandateEvent) ? null : teamData().mandate;
    $('#mandate-rule').textContent = mandate ? `${mandate.decision_rule === 'vote' ? 'Ranked-choice vote' : 'Leader decision'} · ${Object.values(mandate.mandate.internal_support).filter(Boolean).length}/${Object.values(mandate.mandate.internal_support).length} approve` : 'Private record';
    if (!mandate) {$('#mandate').innerHTML = empty('National mandate is delegation-private','Public commitments are in the summit record; internal approvals and red lines are not public.');return;}
    $('#mandate').innerHTML = `<div class="mandate-content"><div>${policy(mandate.mandate.approved_commitment)}<div class="mandate-support">${Object.entries(mandate.mandate.internal_support).map(([id,yes]) => `<span>${yes ? '✓' : '–'} ${esc(roleNames[member(id).role] || member(id).role)}</span>`).join('')}</div><p class="mandate-explanation">Selected candidate: ${esc(mandate.winning_candidate)}. Approvals were recorded individually; the leader did not invent them.</p></div><div><h3>Team priorities</h3><ul class="mandate-goals">${mandate.mandate.priorities.map(g => `<li>${esc(g)}</li>`).join('')}</ul><h3 class="table-subheading">Red lines</h3><ul class="mandate-goals">${mandate.mandate.red_lines.map(g => `<li>${esc(g)}</li>`).join('')}</ul></div></div>`;
  }
  function renderTraces() {
    const traces = (roundData().traces || []).filter(t => t.actor === s.agent && (seen(t) || t.payload.resolution_event_ids?.includes(currentEvent()?.id)));
    let selected = traces.find(t => t.id === s.trace) || (s.workspace === 'delegation' ? traces.filter(t => t.payload.stage === 'internal').at(-1) : traces.at(-1)) || traces.at(-1);
    if (s.playing && currentEvent()?.kind === 'agent_trace' && currentEvent().actor === s.agent) selected = currentEvent();
    s.trace = selected?.id || null;
    const timelineHTML = traces.length ? `<div class="trace-timeline">${traces.map((t,i) => `<button type="button" class="trace-step" data-trace="${esc(t.id)}" aria-pressed="${t.id === s.trace}"><small>${String(i+1).padStart(2,'0')}</small>${esc(stageName(t.payload.stage))}</button>`).join('')}</div>` : '';
    const full = selected && (selected.payload.observations ? selected : s.traceCache.get(selected.id));
    const t = full?.payload || selected?.payload;
    let detail;
    if (!t) detail = empty(s.scope !== 'researcher' && s.scope !== s.agent ? 'Private to this member' : 'No decision trace recorded',s.scope !== 'researcher' && s.scope !== s.agent ? 'Their messages can be shared, but their private decisions are not.' : 'This saved run has no per-action trace for this step. Nothing is invented.');
    else {
      const label = s.demo ? 'Illustrative frontend example' : t.summary_source === 'instrumented_scripted_policy' ? 'Scripted policy summary' : t.summary_source === 'agent_reported' ? 'Agent-reported summary' : 'No summary recorded';
      const summary = t.decision_summary;
      const observations = t.observations || [];
      const action = t.action || {};
      const evidence = new Set(summary?.evidence_event_ids || []);
      detail = `<div class="trace-body"><div class="trace-provenance"><span>${esc(label)}</span><span>${esc(t.backend || member(s.agent).backend)}</span><span>${esc(stageName(t.stage))}</span></div><p class="decision-summary">${esc(summary?.summary || 'The action was recorded, but this member did not provide a decision summary.')}</p>${summary?.intended_action ? `<p class="decision-action">${esc(summary.intended_action)}</p>` : ''}<p class="trace-note">Decision summary + observable inputs and actions. Not hidden model reasoning.</p><details class="trace-evidence"><summary>What the member observed · ${t.observed_count ?? observations.length} records${full ? '' : ' · loading…'}</summary>${observations.length ? observations.map(e => `<details class="observation"><summary>${evidence.has(e.id) ? '↳ Cited · ' : ''}${esc(e.id)} · ${esc(e.kind.replaceAll('_',' '))}${e.actor ? ` · ${esc(shortName(e.actor))}` : ''}</summary><pre class="trace-json">${esc(JSON.stringify(e.payload,null,2))}</pre></details>`).join('') : `<p class="trace-note">${t.snapshot_unavailable ? 'Observation snapshot unavailable from this server.' : full ? 'No prior event observations for this decision.' : 'Retrieving the recorded observation snapshot.'}</p>`}</details><details class="trace-evidence"><summary>Action taken</summary>${action.speech ? `<p class="decision-summary">${esc(action.speech)}</p>` : ''}<pre class="trace-json">${esc(JSON.stringify(action,null,2))}</pre></details><details class="trace-evidence"><summary>Preference before → after</summary><div class="trace-preferences"><div>${policy(t.preference_before)}</div><div>${policy(t.preference_after)}</div></div></details>${full?.payload.inputs ? `<details class="trace-evidence"><summary>Other decision inputs</summary><pre class="trace-json">${esc(JSON.stringify(full.payload.inputs,null,2))}</pre></details>` : ''}</div>`;
    }
    $('#trace-timeline').innerHTML = timelineHTML;
    $('#trace-detail').innerHTML = detail;
    $('#floor-inspector-role').textContent = shortName(s.agent);
    $('#floor-trace-timeline').innerHTML = timelineHTML;
    $('#floor-trace-detail').innerHTML = detail;
    if (selected && !full && !s.tracePending.has(selected.id)) {
      const version=s.loadVersion, scope=s.scope, id=selected.id;
      s.tracePending.add(id);
      api(`/api/trace?id=${encodeURIComponent(id)}&scope=${encodeURIComponent(scope)}`).then(data => {
        if (version !== s.loadVersion || scope !== s.scope) return;
        s.traceCache.set(id,data);s.tracePending.delete(id);if(s.trace === id)renderTraces();
      }).catch(error => {
        if(version !== s.loadVersion || scope !== s.scope)return;
        s.tracePending.delete(id);
        // An older server can still supply the overview; do not synthesize its missing snapshot.
        s.traceCache.set(id,{payload:{...selected.payload,observations:[],snapshot_unavailable:true}});
        if(s.trace === id)renderTraces();
      });
    }
  }
  function visibleMeetings() {
    if (s.cursor !== Infinity) {
      const snapshots=new Map();
      timeline().slice(0,s.cursor).forEach(e => {if(e.payload.meeting)snapshots.set(e.payload.meeting.id,e.payload.meeting);});
      return [...snapshots.values()];
    }
    const snapshots=new Map((roundData().meetings || []).map(m => [m.id,m]));
    (s.hub.meetings || []).filter(m => Number(m.round || 1) === Number(s.round)).forEach(m => snapshots.set(m.id,m));
    return [...snapshots.values()];
  }
  function renderFloor() {
    const allMeetings = visibleMeetings();
    const meetings = allMeetings.filter(m => [m.sender,...m.invitees].includes(s.agent));
    s.displayMeetings = allMeetings;
    if (!meetings.some(m => m.id === s.meeting)) s.meeting = meetings[0]?.id || null;
    const selected = allMeetings.find(m => m.id === s.meeting);
    const connected = new Set(selected ? [selected.sender,...selected.invitees] : []);
    const speaker = s.cursor !== Infinity ? currentEvent()?.actor : null;
    $('#floor-roster').innerHTML = s.replay.delegations.map(d => `<section class="floor-country"><h3>${esc(d.name)} · ${d.members.length}</h3>${d.members.map(id => {const a = member(id);return `<button type="button" class="floor-member ${connected.has(id) ? 'is-connected' : ''} ${speaker === id ? 'is-speaking' : ''}" data-acting="${esc(id)}" aria-pressed="${s.agent === id}"><span class="role-dot ${roleClass(a.role)}"></span>${esc(roleNames[a.role] || a.role)}<span class="controller-pill">${controller(id) === 'human' ? 'HUMAN' : 'AGENT'}</span></button>`;}).join('')}</section>`).join('');
    $('#acting-member').innerHTML = options(s.acting); $('#acting-member').value = s.acting;
    $('#acting-member').disabled = !!s.hub.human_role || !['researcher','public'].includes(s.scope);
    $('#open-request').disabled = s.scope === 'public';
    $('#clock').textContent = `Tick ${s.hub.tick} · ${s.hub.acknowledgement_ticks}-tick ACK window`;
    $('#acting-note').textContent = s.hub.human_role ? 'Manual human-role prototype. No automatic AI replies.' : s.scope === 'researcher' ? 'Manual controls. Select a member to act as them.' : s.scope === 'public' ? 'Public view excludes private meetings.' : 'Only your participant can send messages.';
    $('#meeting-count').textContent = `${meetings.length} for ${codes[member(s.agent).delegation]} ${roleLetter(member(s.agent).role)}`;
    $('#meeting-list').innerHTML = meetings.length ? [...meetings].reverse().map(m => {
      const live = s.cursor === Infinity && s.hub.meetings.some(x => x.id === m.id) && s.scope !== 'public';
      const canRespond = live && m.status === 'pending' && m.acknowledgements[s.acting] === 'pending';
      const canClose = live && ['pending','active'].includes(m.status) && [m.sender,...m.invitees].includes(s.acting);
      const deadline = m.status === 'pending' ? `${Math.max(0,m.expires_tick-s.hub.tick)} ticks left to acknowledge` : m.status === 'expired' ? 'ACK deadline missed. Choose a new action or request another meeting.' : m.status === 'active' ? 'All invitees acknowledged · room open' : m.status === 'declined' ? 'An invitee declined. Choose another action.' : 'Conversation closed.';
      return `<article class="meeting-item ${m.id === s.meeting ? 'selected' : ''}"><button class="meeting-select" type="button" data-meeting="${m.id}"><span class="meeting-item-top"><h3>${esc(m.topic)}</h3><span class="meeting-state ${m.status}">${m.status.toUpperCase()}</span></span><span class="meeting-names">${esc(shortName(m.sender))} → ${m.invitees.map(id => esc(shortName(id))).join(', ')}</span></button><div class="ack-list">${Object.entries(m.acknowledgements).map(([id,state]) => `<span class="ack-chip ${state}">${state === 'accepted' ? '✓' : state === 'declined' ? '×' : '…'} ${esc(shortName(id))}</span>`).join('')}</div><div class="meeting-deadline">${esc(deadline)}</div><div class="meeting-actions">${canRespond ? `<button type="button" class="button primary" data-ack="${m.id}">Acknowledge as ${esc(codes[member(s.acting).delegation])}</button><button type="button" class="button danger" data-decline="${m.id}">Decline</button>` : ''}${canClose ? `<button type="button" class="button secondary" data-close="${m.id}">Close</button>` : ''}</div></article>`;
    }).join('') : empty('No visible meeting requests','Request a one-to-one conversation or invite a cross-delegation group. Only the sender, invitees and researcher can see a private request.');
    renderRoom();
    renderNotifications();
    const recorded = roundData().meetings || [];
    $('#agent-meetings-summary').textContent = `Agent meetings from the saved run · round ${s.round} · ${recorded.length} visible`;
    $('#agent-meetings').innerHTML = recorded.length ? recorded.map(m => `<article class="meeting-item"><span class="meeting-item-top"><h3>${esc(m.topic)}</h3><span class="meeting-state ${m.status}">${m.status.toUpperCase()}</span></span><span class="meeting-names">${esc(shortName(m.sender))} → ${m.invitees.map(id => esc(shortName(id))).join(', ')}</span>${m.messages.map(x => `<article class="room-message"><div class="room-message-heading"><span>${esc(memberName(x.actor))}</span><span>Tick ${x.tick}</span></div><div class="room-message-text">${esc(x.text)}</div></article>`).join('')}</article>`).join('') : empty('No agent meetings visible', s.scope === 'public' ? 'Meetings are private to their participants.' : 'None were recorded in this round for this perspective.');
    $('#public-transcript').innerHTML = roundData().public_events.filter(e => e.kind === 'summit_speech').map(e => {const a = member(e.actor);return `<article class="speech">${avatar(a)}<div class="speech-body"><div class="speech-heading"><strong>${esc(memberName(e.actor))}</strong><span>PUBLIC · ${esc(e.id)}</span></div><p class="speech-text">${esc(e.payload.speech)}</p></div></article>`;}).join('') || empty('No public statements','No public statements were recorded in this round.');
    requestAnimationFrame(drawConnections);
  }
  function renderRoom() {
    const meeting = s.displayMeetings.find(m => m.id === s.meeting);
    $('#room-title').textContent = meeting ? meeting.topic : 'Private room';
    $('#room-status').textContent = meeting ? `${meeting.id} · ${meeting.status}` : 'Select a meeting';
    $('#room-members').innerHTML = meeting ? `<div class="room-members">${[meeting.sender,...meeting.invitees].map(id => esc(shortName(id))).join(' · ')} · participant-only</div>` : '';
    $('#room-messages').innerHTML = meeting?.messages.length ? `<div class="room-chat">${meeting.messages.map(m => `<article class="room-message ${currentEvent()?.payload.message?.id === m.id ? 'is-current' : ''}"><div class="room-message-heading"><button type="button" class="inspect-message" data-message-agent="${esc(m.actor)}">${esc(shortName(m.actor))} ↗</button><span>Tick ${m.tick}</span></div><div class="room-message-text">${esc(m.text)}</div></article>`).join('')}</div>` : empty(meeting?.status === 'pending' ? 'Waiting for acknowledgements' : meeting?.status === 'expired' ? 'Request expired' : meeting?.status === 'active' ? 'The private room is open' : 'No conversation selected',meeting?.status === 'pending' ? 'All invitees must accept before the room opens.' : meeting?.status === 'active' ? 'Only the invited members can read and reply.' : 'Choose a member, then one of their conversations.');
    const participant = meeting && [meeting.sender,...meeting.invitees].includes(s.acting);
    const live = s.cursor === Infinity && s.hub.meetings.some(m => m.id === meeting?.id) && s.scope !== 'public';
    const canSend = live && meeting?.status === 'active' && participant;
    $('#meeting-message').disabled = !canSend; $('#send-message').disabled = !canSend;
    $('#message-note').textContent = canSend ? `Sending as ${shortName(s.acting)}` : 'Only acknowledged meeting participants can send.';
    const reports = meeting?.reports || [];
    $('#meeting-outcomes').innerHTML = reports.length ? reports.map(reportHTML).join('') : empty('No outcome report','A participant can summarize this conversation and notify their team.');
    const canReport = live && participant && ['active','closed'].includes(meeting.status) && !reports.some(r => r.author === s.acting);
    $('#report-summary').disabled = !canReport;$('#notify-team').disabled = !canReport;
  }
  function reportHTML(r) {return `<article class="outcome-report"><div class="report-author"><span class="role-dot ${roleClass(member(r.author)?.role || 'head_negotiator')}"></span>${esc(shortName(r.author))} · ${esc(r.meeting_id)}</div><p class="report-body">${esc(r.summary)}</p>${r.next_steps?.length ? `<p class="report-next">Next: ${r.next_steps.map(esc).join(' · ')}</p>` : ''}${r.notified_delegation ? '<p class="trace-note">↗ Shared with their team · participant-authored report</p>' : ''}</article>`;}
  function renderNotifications() {
    const records = s.cursor === Infinity ? (s.hub.notifications || roundData().notifications || []) : (roundData().notifications || []).filter(seen);
    const team = member(s.agent).delegation;
    const notifications=records.filter(e => Number(e.round) === Number(s.round) && (e.owners || []).includes(team));
    $('#notification-count').textContent = `${codes[team]} · ${notifications.length} update${notifications.length === 1 ? '' : 's'}`;
    $('#team-notifications').innerHTML = notifications.length ? notifications.map(e => reportHTML(e.payload)).join('') : empty('Team inbox','Reports from this delegation’s members will appear here. Private conversation transcripts stay with the invitees.');
  }
  function drawConnections() {
    const svg=$('#meeting-network');svg.innerHTML='';
    if(s.workspace !== 'floor')return;
    const box=$('#network-stage').getBoundingClientRect();if(!box.width)return;
    svg.setAttribute('viewBox',`0 0 ${box.width} ${box.height}`);
    const meeting=s.displayMeetings.find(m => m.id === s.meeting);if(!meeting)return;
    const point = id => {const e=$$('.floor-member').find(b => b.dataset.acting === id);if(!e)return null;const b=e.getBoundingClientRect();return{x:b.left-box.left+b.width/2,y:b.top-box.top+b.height/2};};
    const move=s.playing && currentEvent()?.kind === 'meeting_message' && !matchMedia('(prefers-reduced-motion: reduce)').matches;
    const origin=move ? currentEvent().actor : meeting.sender;
    const source=point(origin);if(!source)return;
    const recipients=[meeting.sender,...meeting.invitees].filter(id => id !== origin);
    svg.innerHTML=recipients.map(id => {const target=point(id);if(!target)return '';const mid=(source.x+target.x)/2;const path=`M ${source.x} ${source.y} C ${mid} ${source.y-30}, ${mid} ${target.y-30}, ${target.x} ${target.y}`;return `<path class="connection ${esc(meeting.status)}" d="${path}"/>${move ? `<circle class="packet" r="3"><animateMotion dur="0.8s" repeatCount="1" path="${path}" fill="freeze"/></circle>` : ''}`;}).join('');
  }
  function renderPlayback() {
    const total=timeline().length, position=Math.min(total,s.cursor);
    $('#replay-position').max=String(Math.max(1,total));$('#replay-position').value=String(position);
    $('#replay-counter').textContent=`${position} / ${total}`;
    $('#play-replay').textContent=s.playing ? 'Ⅱ Pause' : '▶ Play';$('#play-replay').disabled=!total;
    $('#step-back').disabled=position === 0;$('#step-next').disabled=position === total;
  }
  function render() {renderChrome();renderAgents();renderDiscussion();renderAlignment();renderFloor();renderTraces();renderPlayback();}
  function chooseAgent(id) {s.agent=id;s.country=member(id).delegation;s.expanded.add(s.country);s.trace=null;if(s.scope === 'researcher' || s.scope === id)s.acting=id;render();}
  function seek(position) {
    s.cursor=Math.max(0,Math.min(timeline().length,position));s.trace=null;
    const event=currentEvent();
    if(event?.actor && member(event.actor)){s.agent=event.actor;s.country=member(s.agent).delegation;s.expanded.add(s.country);}
    if(event?.payload.meeting)s.meeting=event.payload.meeting.id;
    render();
    if(s.playing){const transcript=s.workspace === 'delegation' ? $('#internal-transcript') : $('#room-messages .room-chat');if(transcript)transcript.scrollTop=transcript.scrollHeight;}
  }
  function playStep() {if(!s.playing)return;if(s.cursor >= timeline().length){stopPlayback();renderPlayback();return;}seek(s.cursor+1);s.timer=setTimeout(playStep,1100);}
  function renderInvitees() {
    const sender = $('#request-sender').value;
    $('#invitees').innerHTML = s.replay.delegations.map(d => d.members.filter(id => id !== sender).map(id => `<label class="invitee-row"><input type="checkbox" name="invitee" value="${esc(id)}"><span class="role-dot ${roleClass(member(id).role)}"></span>${esc(shortName(id))}</label>`).join('')).join('');
    $('#invitation-count').textContent = 'No invitees selected';
  }
  document.addEventListener('click', event => {
    const b = event.target.closest('button'); if (!b || b.disabled || s.switching || !s.replay) return;
    if (b.dataset.country) {const id=b.dataset.country;const same=id === s.country;resetPlayback();if(same && s.expanded.has(id))s.expanded.delete(id);else s.expanded.add(id);s.country=id;s.agent=country(id).members[0];render();}
    if (b.dataset.round) {resetPlayback();s.round=b.dataset.round;s.meeting=null;render();}
    if (b.dataset.agent) {chooseAgent(b.dataset.agent);}
    if (b.dataset.workspace) {resetPlayback();s.workspace=b.dataset.workspace;render();}
    if (b.dataset.acting) {chooseAgent(b.dataset.acting);}
    if (b.dataset.messageAgent) {s.agent=b.dataset.messageAgent;s.country=member(s.agent).delegation;s.expanded.add(s.country);s.trace=b.dataset.trace || null;render();}
    else if (b.dataset.trace) {s.trace=b.dataset.trace;renderTraces();}
    if (b.dataset.meeting) {s.meeting=b.dataset.meeting;renderFloor();renderTraces();}
    if (b.dataset.ack) act('respond',{meeting_id:b.dataset.ack,accepted:true});
    if (b.dataset.decline) act('respond',{meeting_id:b.dataset.decline,accepted:false});
    if (b.dataset.close) act('close',{meeting_id:b.dataset.close});
  });
  $('#viewer').addEventListener('change', async event => {
    clearPrivateViews();resetPlayback();
    s.scope = event.target.value;
    if (!['researcher','public'].includes(s.scope)) s.acting = s.scope;
    try {await load();} catch(error) {s.switching=false;showError(error);}
  });
  $('#acting-member').addEventListener('change', event => {s.acting = event.target.value;chooseAgent(s.acting);});
  $('#step-back').addEventListener('click', () => {stopPlayback();seek(Math.min(timeline().length,s.cursor)-1);});
  $('#step-next').addEventListener('click', () => {stopPlayback();seek(Math.min(timeline().length,s.cursor)+1);});
  $('#latest-replay').addEventListener('click', () => {resetPlayback();render();});
  $('#replay-position').addEventListener('input', event => {stopPlayback();seek(Number(event.target.value));});
  $('#play-replay').addEventListener('click', () => {if(s.playing){stopPlayback();renderPlayback();return;}if(s.cursor >= timeline().length)s.cursor=0;s.playing=true;playStep();});
  window.addEventListener('resize',drawConnections);
  $('#advance-tick').addEventListener('click', () => act('advance'));
  $('#claim-role').addEventListener('click', async () => {
    clearPrivateViews();s.switching=false;
    if (s.hub.human_role) {await act('release');s.scope = 'researcher';await load();}
    else {s.acting = s.scope;await act('claim');}
  });
  $('#open-request').addEventListener('click', () => {$('#request-sender').innerHTML = options(s.acting);$('#request-sender').value = s.acting;$('#request-sender').disabled = !!s.hub.human_role || s.scope !== 'researcher';renderInvitees();$('#meeting-dialog').showModal();});
  $('#close-dialog').addEventListener('click', () => $('#meeting-dialog').close());
  $('#request-sender').addEventListener('change', renderInvitees);
  $('#invitees').addEventListener('change', () => {const n = $$('#invitees input:checked').length;$('#invitation-count').textContent = `${n} invitee${n === 1 ? '' : 's'} selected`;});
  $('#request-form').addEventListener('submit', async event => {
    event.preventDefault();const invitees = $$('#invitees input:checked').map(i => i.value);
    if (!invitees.length) {showError(new Error('Select at least one invitee.'));$('#meeting-dialog').close();return;}
    s.acting = $('#request-sender').value;
    await act('request',{invitees,topic:$('#request-topic').value});$('#meeting-dialog').close();$('#request-topic').value = '';
  });
  $('#message-form').addEventListener('submit', async event => {event.preventDefault();const text = $('#meeting-message').value;if (!text.trim()) return;await act('message',{meeting_id:s.meeting,text});$('#meeting-message').value = '';});
  $('#report-form').addEventListener('submit', async event => {event.preventDefault();const summary=$('#report-summary').value.trim();if(!summary)return;await act('report',{meeting_id:s.meeting,summary,notify_team:true});$('#report-summary').value='';});
  // Frontend-only fixtures. These are explicitly labelled examples, never simulated model output.
  function createDemo() {
    const names={usa:'United States',eu:'European Union',china:'China',india:'India',other_developed:'Other Developed Nations',other_developing:'Other Developing Nations'};
    const baselines={usa:[2028,2030,2.8,25],eu:[2026,2027,4.0,40],china:[2030,2033,2.4,-15],india:[2035,2038,1.8,-30],other_developed:[2027,2030,3.2,20],other_developing:[2038,2040,1.5,-45]};
    const roster=Object.keys(names).flatMap(delegation => Object.keys(roleNames).map(role => {
      const b=baselines[delegation];const delta=role === 'climate_adviser' ? .8 : role === 'economic_minister' ? -.5 : 0;
      return {agent:`${delegation}.${role}`,delegation,role,personality:priorities[role],backend:'frontend-example',private_strategy:role === 'head_negotiator' ? 'Build a coalition without promising concessions the team has not approved.' : role === 'economic_minister' ? 'Support stronger commitments when implementation and finance are credible.' : 'Press for earlier action and ask the team to make climate risks explicit.',initial_preference:{delegation,emissions_peak_year:b[0],reduction_start_year:b[1],annual_reduction_rate_percent:b[2]+delta,deforestation_prevention_percent:40,afforestation_percent:30,climate_fund_billion_per_year:b[3]}};
    }));
    const delegations=Object.entries(names).map(([id,name]) => ({id,name,goals:['Build cooperation while protecting development and implementation capacity.'],members:roster.filter(a => a.delegation === id).map(a => a.agent)}));
    const events=[],rounds={},meetings=[];
    const canSee=(e,id) => id === 'researcher' || e.visibility === 'public' || (e.visibility === 'individual' && e.owners.includes(id)) || (e.visibility === 'delegation' && e.owners.includes(id.split('.')[0]));
    const add=(kind,payload,actor,visibility,owners,round) => {const e={id:`event-${String(events.length+1).padStart(6,'0')}`,kind,payload:structuredClone(payload),actor,visibility,owners,round};events.push(e);return e;};
    const decision=(a,round,stage,summary,intended_action,action,before,after,linked=[]) => {
      const observations=events.filter(e => e.kind !== 'agent_trace' && !linked.includes(e.id) && canSee(e,a.agent)).slice(-6);
      add('agent_trace',{agent:a.agent,round,stage,backend:'frontend-example',summary_source:'frontend_example',decision_summary:{summary,intended_action,evidence_event_ids:observations.slice(-2).map(e => e.id)},observations,observed_count:observations.length,inputs:{priorities:priorities[a.role],information_boundary:'Only own private state, team records, public events and invited meetings.'},preference_before:before,preference_after:after,action,resolution_event_ids:linked},a.agent,'individual',[a.agent],round);
    };
    const firstTexts={
      head_negotiator:['We need one mandate, not three competing promises. Let’s identify a commitment we can defend together.','I propose a middle position, with further reductions conditional on a credible finance package.'],
      economic_minister:['A faster reduction path needs an implementation plan. I want finance and competitiveness addressed before we promise more.','I can support this compromise if the financing condition is written into the mandate.'],
      climate_adviser:['The current timetable leaves too much climate risk. I recommend earlier reductions and stronger forest protection.','This is a workable starting point, but we should keep an earlier peak available as a concession.']};
    const secondTexts={
      head_negotiator:['The floor conversations opened a possible coalition. Let’s decide what we can actually authorize, rather than treating an informal chat as an agreement.','I’ll present the revised package and keep unapproved promises off the table.'],
      economic_minister:['The finance discussion improves the offer, but delivery still matters. I support a measured concession, not an unconditional transfer.','I approve the revised mandate with a clear implementation review.'],
      climate_adviser:['We now have a path toward stronger coordination. Let’s move our reduction timetable forward and preserve that condition in the final proposal.','I support the stronger package. My remaining concern is that the finance commitments are not yet secured.']};
    for(const r of [1,2]) {
      const teams={};
      for(const d of delegations) {
        const agents=roster.filter(a => a.delegation === d.id),speeches=[],starting={},positions={};
        agents.forEach(a => {starting[a.agent]=structuredClone(a.initial_preference);const c=structuredClone(a.initial_preference);c.annual_reduction_rate_percent+=r === 2 ? .35 : 0;positions[a.agent]=c;decision(a,r,'prepare',a.private_strategy,'Bring a preferred proposal to the team.',{commitment:c},a.initial_preference,c);});
        for(let turn=0;turn<2;turn++)for(const a of agents) {
          const before=structuredClone(positions[a.agent]);const center=baselines[d.id][2]+(r === 2 ? .35 : 0);positions[a.agent].annual_reduction_rate_percent=Number(((before.annual_reduction_rate_percent+center)/2).toFixed(2));
          const speech=(r === 1 ? firstTexts : secondTexts)[a.role][turn];
          const e=add('internal_speech',{speech,commitment:positions[a.agent]},a.agent,'delegation',[d.id],r);speeches.push(e);
          decision(a,r,'internal',a.role === 'climate_adviser' ? 'The team is moving toward a compromise. Keep climate risk visible while supporting an implementable shared position.' : a.role === 'economic_minister' ? 'The proposal is closer to the team’s position. A financing condition makes a limited concession acceptable.' : 'Both advisers accept a conditional compromise. Keep the team together and retain room for coalition-building.',a.role === 'head_negotiator' ? 'Propose the shared compromise.' : 'Share a revised preference with the team.',{speech,commitment:positions[a.agent]},before,positions[a.agent],[e.id]);
        }
        const head=agents[0],commitment=positions[head.agent];
        const mandate={decision_rule:'leader',winning_candidate:head.agent,mandate:{delegation:d.id,approved_commitment:commitment,priorities:['Implementation capacity','Credible climate finance','Earlier reductions'],red_lines:['No unapproved unconditional concessions'],permitted_concessions:['Conditional timing improvements'],desired_partners:['eu','china'],internal_support:Object.fromEntries(agents.map(a => [a.agent,true]))}};
        const m=add('national_mandate',mandate,head.agent,'delegation',[d.id],r);
        decision(head,r,'mandate','The advisers support a conditional common position. Record their approvals and keep the finance condition explicit.','Approve the national mandate.',{mandate:mandate.mandate},head.initial_preference,commitment,[m.id]);
        teams[d.id]={speeches,starting_positions:starting,positions,mandate,disagreement_before:.04,disagreement_after:.012,pairwise_distance:[]};
      }
      for(const d of delegations) {
        const head=roster.find(a => a.agent === d.members[0]);const c=teams[d.id].positions[head.agent];
        const e=add('summit_speech',{speech:`${d.name} proposes ${c.annual_reduction_rate_percent.toFixed(2)}% annual reductions. We are open to a coalition linking credible finance with earlier action.`,commitment:c},head.agent,'public',[],r);
        decision(head,r,'summit','Present the approved position, not the stronger private preference of any single adviser.','Announce the national commitment.',{speech:e.payload.speech,commitment:c},c,c,[e.id]);
      }
      const participants=r === 1 ? ['china.climate_adviser','eu.economic_minister','india.climate_adviser'] : ['china.head_negotiator','eu.head_negotiator','usa.economic_minister'];
      const meeting={id:`meeting-${String(r).padStart(4,'0')}`,round:r,sender:participants[0],invitees:participants.slice(1),topic:r === 1 ? 'Finance ↔ earlier action' : 'A coordinated final package',status:'pending',created_tick:(r-1)*6,expires_tick:(r-1)*6+5,acknowledgements:Object.fromEntries(participants.slice(1).map(id => [id,'pending'])),messages:[],reports:[]};
      const record=(kind,actor,extra={}) => add(kind,{meeting,tick:meeting.created_tick,...extra},actor,'individual',participants,r);
      let e=record('meeting_requested',meeting.sender);
      decision(roster.find(a => a.agent === meeting.sender),r,'member_floor','A cross-country conversation could connect implementation finance with stronger reductions. Invite complementary roles.','Request a private group meeting.',{meeting_requests:[{invitees:meeting.invitees,topic:meeting.topic}]},teams.china.positions[meeting.sender],teams.china.positions[meeting.sender],[e.id]);
      participants.slice(1).forEach(id => {meeting.acknowledgements[id]='accepted';if(Object.values(meeting.acknowledgements).every(a => a === 'accepted'))meeting.status='active';e=record('meeting_acknowledged',id);const a=roster.find(a => a.agent === id);decision(a,r,'member_floor','The topic is relevant to my role. Accept a discussion without authorizing a national concession.','Accept the meeting invitation.',{meeting_acknowledgements:{[meeting.id]:'accepted'}},teams[a.delegation].positions[id],teams[a.delegation].positions[id],[e.id]);});
      const messages=r === 1 ? ['Could we link an earlier reduction timetable to a credible finance package?','I can discuss a financing floor, but our delegation must approve the actual contribution. What implementation milestones would you offer?','Let’s put access to implementation support in the package too. I will bring the proposal back to my team.','Agreed to explore it—not a binding commitment. We should each seek a mandate before the final summit.'] : ['Our team can consider a coordinated package. Which concessions are already authorized?','We can propose stronger finance conditional on verifiable implementation. I will distinguish that from a secured agreement.','A shared implementation review could make participation more credible. I will ask my team to assess it.'];
      messages.forEach((text,i) => {const actor=participants[i%participants.length];const message={id:`${meeting.id}-message-${i+1}`,actor,text,tick:meeting.created_tick+2+Math.floor(i/3)};meeting.messages.push(message);e=record('meeting_message',actor,{message});const a=roster.find(a => a.agent === actor);decision(a,r,'member_floor','The other participants are discussing conditional options. Share an exploratory proposal without disclosing the private team transcript.','Discuss a conditional package with the invitees.',{meeting_messages:[{meeting_id:meeting.id,text}]},teams[a.delegation].positions[actor],teams[a.delegation].positions[actor],[e.id]);});
      for(const author of participants) {
        const report={id:`${meeting.id}-report-${author}`,meeting_id:meeting.id,author,round:r,tick:meeting.created_tick+4,summary:'The group explored linking earlier action with credible implementation finance. No national concession was authorized in the room.',agreements:['Continue exploring a conditional package—not a binding national agreement.'],next_steps:['Ask our delegation to review the package before the final summit.'],notified_delegation:author.split('.')[0]};
        meeting.reports.push(report);e=record('meeting_outcome_reported',author,{report});add('team_meeting_report',report,author,'delegation',[report.notified_delegation],r);
        const a=roster.find(a => a.agent === author);decision(a,r,'member_floor','An informal conversation is useful only if the team can evaluate it. Share an explicit summary and flag what is still unapproved.','Notify my team of the conversation outcome.',{meeting_reports:[report]},teams[a.delegation].positions[author],teams[a.delegation].positions[author],[e.id]);
      }
      meeting.status='closed';record('meeting_closed',meeting.sender);meetings.push(structuredClone(meeting));
      const roundEvents=events.filter(e => e.round === r);
      rounds[r]={teams,traces:roundEvents.filter(e => e.kind === 'agent_trace'),timeline:roundEvents,notifications:roundEvents.filter(e => e.kind === 'team_meeting_report'),public_events:roundEvents.filter(e => e.visibility === 'public'),meetings:[structuredClone(meeting)],climate_outcome:null};
    }
    const hub={tick:12,acknowledgement_ticks:5,human_role:null,participants:roster.map(a => ({id:a.agent,delegation:a.delegation,role:a.role,model:a.backend,controller:'agent'})),meetings,notifications:events.filter(e => e.kind === 'team_meeting_report')};
    return {replay:{run:'frontend-demo',config:{backend:'frontend_demo',composition:'diverse'},roster,delegations,rounds},hub,events,canSee};
  }
  function demoApi(path,options) {
    const u=new URL(path,location.href),scope=demo.hub.human_role || u.searchParams.get('scope') || 'public';
    if(options) {
      const a=JSON.parse(options.body),hub=demo.hub;
      if(hub.human_role && !['release','advance'].includes(a.operation) && a.actor !== hub.human_role)throw Error('You control only your selected human role.');
      const m=hub.meetings.find(m => m.id === a.meeting_id);
      const participant=m && [m.sender,...m.invitees].includes(a.actor);
      if(['respond','message','report','close'].includes(a.operation) && !participant)throw Error('This room is participant-only.');
      switch(a.operation) {
        case 'claim':hub.human_role=a.actor;hub.participants.find(p => p.id === a.actor).controller='human';break;
        case 'release':if(hub.human_role)hub.participants.find(p => p.id === hub.human_role).controller='agent';hub.human_role=null;break;
        case 'advance':hub.tick++;hub.meetings.forEach(m => {if(m.status === 'pending' && m.expires_tick <= hub.tick)m.status='expired';});break;
        case 'request':{if(!a.invitees.length || a.invitees.includes(a.actor))throw Error('Invite another member.');const id=`demo-request-${hub.meetings.length+1}`;hub.meetings.push({id,round:a.round,sender:a.actor,invitees:a.invitees,topic:a.topic,status:'pending',created_tick:hub.tick,expires_tick:hub.tick+5,acknowledgements:Object.fromEntries(a.invitees.map(id => [id,'pending'])),messages:[],reports:[]});s.meeting=id;break;}
        case 'respond':if(m.status !== 'pending' || m.acknowledgements[a.actor] !== 'pending')throw Error('This invitation cannot be acknowledged.');m.acknowledgements[a.actor]=a.accepted ? 'accepted' : 'declined';if(!a.accepted)m.status='declined';else if(Object.values(m.acknowledgements).every(v => v === 'accepted'))m.status='active';break;
        case 'message':if(m.status !== 'active')throw Error('Wait until every invitee accepts.');m.messages.push({id:`${m.id}-${m.messages.length+1}`,actor:a.actor,text:a.text,tick:hub.tick});break;
        case 'close':if(!['pending','active'].includes(m.status))throw Error('This room is already closed.');m.status='closed';break;
        case 'report':{if(!['active','closed'].includes(m.status))throw Error('The conversation has not started.');if(m.reports.some(r => r.author === a.actor))throw Error('You already reported this conversation.');const report={id:`${m.id}-report-${a.actor}`,meeting_id:m.id,round:m.round,author:a.actor,tick:hub.tick,summary:a.summary,agreements:[],next_steps:[],notified_delegation:a.actor.split('.')[0]};m.reports.push(report);hub.notifications.push({id:`demo-notification-${hub.notifications.length+1}`,kind:'team_meeting_report',round:m.round,owners:[report.notified_delegation],visibility:'delegation',actor:a.actor,payload:report});break;}
      }
      return {human_role:hub.human_role,tick:hub.tick};
    }
    if(u.pathname === '/api/meetings')return {...structuredClone(demo.hub),meetings:structuredClone(demo.hub.meetings.filter(m => scope === 'researcher' || [m.sender,...m.invitees].includes(scope))),notifications:structuredClone(demo.hub.notifications.filter(e => demo.canSee(e,scope)))};
    if(u.pathname === '/api/trace'){const e=demo.events.find(e => e.id === u.searchParams.get('id') && e.kind === 'agent_trace');if(!e || !demo.canSee(e,scope))throw Error('Decision trace is private.');return structuredClone(e);}
    const replay=structuredClone(demo.replay);replay.scope=scope;
    replay.roster.forEach(a => {if(scope !== 'researcher' && scope !== a.agent){delete a.private_strategy;delete a.initial_preference;}});
    for(const r of Object.values(replay.rounds)) {
      ['traces','timeline','notifications','public_events'].forEach(k => {r[k]=r[k].filter(e => demo.canSee(e,scope));});
      r.meetings=r.meetings.filter(m => scope === 'researcher' || [m.sender,...m.invitees].includes(scope));
      for(const [region,team] of Object.entries(r.teams)) {
        if(scope !== 'researcher' && scope.split('.')[0] !== region){team.speeches=[];team.starting_positions={};team.positions={};team.mandate=null;}
        else if(scope !== 'researcher')team.starting_positions=Object.fromEntries(Object.entries(team.starting_positions).filter(([id]) => id === scope));
      }
    }
    return replay;
  }
  let demo;
  if (s.demo) demo=createDemo();
  load().catch(error => {showError(error);$('#mode-notice').innerHTML='Replay service unavailable. <a href="?demo=1">Open the standalone frontend demo ↗</a>';});
})();
