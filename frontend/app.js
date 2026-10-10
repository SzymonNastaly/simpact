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
  const s = {scope:'researcher',country:'china',round:'1',workspace:'delegation',agent:'china.climate_adviser',acting:'china.climate_adviser',meeting:null,replay:null,hub:null,busy:false};
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

  async function api(path, options) {
    const response = await fetch(path, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }
  function showError(error) { $('#error').textContent = error.message || String(error); $('#error').hidden = false; }
  async function load() {
    const [replay,hub] = await Promise.all([api(`/api/replay?scope=${encodeURIComponent(s.scope)}`),api(`/api/meetings?scope=${encodeURIComponent(s.scope)}`)]);
    s.replay = replay; s.hub = hub; s.scope = replay.scope;
    if (hub.human_role) s.acting = hub.human_role;
    if (!s.replay.rounds[s.round]) s.round = Object.keys(s.replay.rounds)[0];
    if (s.scope !== 'researcher' && s.scope !== 'public' && s.workspace === 'delegation') {
      s.country = member(s.scope).delegation;
      s.agent = s.scope;
    }
    render();
  }
  async function act(operation, extras = {}) {
    if (s.busy) return;
    s.busy = true; $('#error').hidden = true;
    try { await api('/api/actions',{method:'POST',headers:{'Content-Type':'application/json','X-Simpact-Lab':'1'},body:JSON.stringify({operation,actor:s.acting,...extras})}); await load(); }
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
    $('#run-label').textContent = `${members.length} members · ${s.replay.config.backend} replay`;
    $('#viewer').innerHTML = `<option value="researcher">Researcher · full audit</option><option value="public">Public summit</option>${options(s.scope)}`;
    $('#viewer').value = s.scope;
    $('#viewer').disabled = !!s.hub.human_role;
    $('#claim-role').disabled = !s.hub.human_role && ['researcher','public'].includes(s.scope);
    $('#claim-role').textContent = s.hub.human_role ? 'Leave human role' : 'Play this role';
    $('#claim-role').classList.toggle('primary',!!s.hub.human_role);
    $('#countries').innerHTML = s.replay.delegations.map(d => `<button class="country-button" type="button" data-country="${d.id}" aria-pressed="${d.id === s.country}"><span class="country-code">${codes[d.id]}</span><span class="country-copy"><span class="country-name">${esc(d.name)}</span><span class="country-meta">${d.members.length} members</span></span><span class="country-size">${d.members.length}</span></button>`).join('');
    $('#round-switch').innerHTML = Object.keys(s.replay.rounds).map(r => `<button type="button" data-round="${r}" aria-pressed="${r === s.round}">Round ${r}</button>`).join('');
    $('#workspace-title').textContent = s.workspace === 'delegation' ? `${country(s.country).name} delegation` : 'The negotiation floor';
    $('#workspace-eyebrow').textContent = s.workspace === 'delegation' ? 'PRIVATE DELIBERATION → NATIONAL MANDATE' : 'MEMBER-LEVEL MEETING PROTOCOL';
    $('#workspace-description').textContent = s.workspace === 'delegation' ? `${country(s.country).members.length} members · select a role to inspect its position and strategy.` : 'Every member can request private or group discussions—not just the head negotiators.';
    $$('.workspace-tabs button').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.workspace === s.workspace)));
    $('#delegation-workspace').hidden = s.workspace !== 'delegation';
    $('#floor-workspace').hidden = s.workspace !== 'floor';
    const offline = s.replay.config.backend === 'offline' ? 'Saved offline run · scripted agents, not Gemini.' : 'Saved Gemini run · not a live session.';
    $('#mode-notice').classList.toggle('human-notice',!!s.hub.human_role);
    $('#mode-notice').textContent = s.hub.human_role ? `You control ${memberName(s.hub.human_role)} in the manual meeting sandbox. Only your authorized records and meeting actions are available. No AI replies are generated.` : `${offline} ${s.scope === 'researcher' ? 'Researcher audit: includes private member preferences and team records.' : s.scope === 'public' ? 'Public view: private deliberations, preferences and meetings are unavailable.' : `Participant view: only ${memberName(s.scope)}’s private state, their team records and addressed meetings.`}`;
    const outcome = roundData().climate_outcome;
    $('#climate-summary').textContent = outcome ? `${outcome.is_mock ? 'MOCK, not C-ROADS. ' : ''}Round ${s.round}: ${outcome.warming_celsius.toFixed(2)}°C. ${outcome.explanation}` : 'No climate outcome recorded.';
  }
  function renderAgents() {
    const team = teamData(), members = s.replay.roster.filter(a => a.delegation === s.country);
    $('#team-summary').textContent = `${members.length} members · ${s.replay.config.composition} priorities`;
    if (!members.some(a => a.agent === s.agent)) s.agent = members[0].agent;
    $('#agent-cards').innerHTML = members.map(a => {
      const position = team.positions[a.agent], support = team.mandate?.mandate.internal_support[a.agent];
      return `<button type="button" class="agent-card" data-agent="${esc(a.agent)}" aria-pressed="${a.agent === s.agent}"><span class="agent-card-header">${avatar(a)}<span class="controller-pill ${controller(a.agent) === 'human' ? 'human' : ''}">${controller(a.agent) === 'human' ? 'HUMAN' : 'AGENT'}</span></span><h3>${esc(roleNames[a.role] || a.role)}</h3><span class="agent-priority">${esc(priorities[a.role] || a.personality)}</span><span class="agent-model">${esc(a.backend)}</span><span class="agent-position"><span class="agent-position-value">${fmtRate(position)}<span class="agent-position-label">latest shared preference</span></span>${support === undefined ? '<span class="support-label">Private team record</span>' : `<span class="support-label ${support ? '' : 'dissent'}">${support ? '✓ Approved mandate' : 'Did not approve'}</span>`}</span></button>`;
    }).join('');
  }
  function renderDiscussion() {
    const team = teamData();
    $('#discussion-access').textContent = team.speeches.length ? `Round ${s.round} · ${team.speeches.length} recorded turns · team-only` : 'Not available in this information view';
    $('#internal-transcript').innerHTML = team.speeches.length ? team.speeches.map((e,i) => {
      const a = member(e.actor), c = e.payload.commitment;
      return `<article class="speech ${e.actor === s.agent ? 'selected-speech' : ''}">${avatar(a)}<div class="speech-body"><div class="speech-heading"><strong>${esc(roleNames[a.role] || a.role)}</strong><span>Turn ${i+1} · ${esc(e.id)}</span></div><p class="speech-text">${esc(e.payload.speech)}</p><div class="speech-policy"><span>${fmtRate(c)} cuts/yr</span><span>Peak ${c.emissions_peak_year}</span><span>Start ${c.reduction_start_year}</span><span>${fmtMoney(c.climate_fund_billion_per_year)}</span></div></div></article>`;
    }).join('') : empty('Private delegation room','Choose the researcher audit or a member of this delegation to inspect its internal discussion. Foreign members cannot access this transcript.');
    const a = member(s.agent);
    $('#inspector-role').textContent = roleNames[a.role] || a.role;
    const preparation = a.private_strategy ? `<div class="inspector-title">Individual-private preparation strategy</div><p class="strategy">${esc(a.private_strategy)}</p>` : '<div class="private-empty">Individual-private strategy is restricted to its owner and the researcher. Team colleagues only see what was shared.</div>';
    $('#agent-perspective').innerHTML = `<div class="inspector-content">${preparation}<div class="table-subheading">${s.round === '1' ? 'Starting private preference' : 'Starting shared position from prior round'}</div>${policy(team.starting_positions[a.agent])}<div class="table-subheading">Last position shared in this round</div>${policy(team.positions[a.agent])}</div>`;
  }
  function renderAlignment() {
    const team = teamData(), entries = Object.entries(team.positions);
    if (!entries.length) {$('#alignment').innerHTML = empty('Alignment is private','No internal positions are available in this view.');renderMandate();return;}
    const max = Math.ceil(Math.max(...entries.map(([,c]) => c.annual_reduction_rate_percent),team.mandate?.mandate.approved_commitment.annual_reduction_rate_percent || 0));
    $('#alignment').innerHTML = `<div class="inspector-content"><div class="alignment-summary"><div><div class="inspector-title">Mean pairwise policy distance</div><div class="distance-values">${team.disagreement_before == null ? '—' : team.disagreement_before.toFixed(3)}<span>→</span>${team.disagreement_after == null ? '—' : team.disagreement_after.toFixed(3)}</div></div></div><p class="alignment-caption">Lower means closer positions across all six commitment fields. Not a consensus probability. A missing start value means private preferences are unavailable.</p>${entries.map(([id,c]) => {const a = member(id), start = team.starting_positions[id];return `<div class="alignment-row"><div class="alignment-row-label"><span>${esc(roleNames[a.role] || a.role)}</span><span>${start ? `${fmtRate(start)} → ` : ''}${fmtRate(c)}</span></div><div class="alignment-track ${roleClass(a.role)}"><span class="current-position" style="width:${Math.max(0,Math.min(100,c.annual_reduction_rate_percent/Math.max(1,max)*100))}%"></span></div></div>`;}).join('')}<div class="alignment-axis"><span>0% annual reduction</span><span>${max}%</span></div></div>`;
    renderMandate();
  }
  function renderMandate() {
    const mandate = teamData().mandate;
    $('#mandate-rule').textContent = mandate ? `${mandate.decision_rule === 'vote' ? 'Ranked-choice vote' : 'Leader decision'} · ${Object.values(mandate.mandate.internal_support).filter(Boolean).length}/${Object.values(mandate.mandate.internal_support).length} approve` : 'Private record';
    if (!mandate) {$('#mandate').innerHTML = empty('National mandate is delegation-private','Public commitments are in the summit record; internal approvals and red lines are not public.');return;}
    $('#mandate').innerHTML = `<div class="mandate-content"><div>${policy(mandate.mandate.approved_commitment)}<div class="mandate-support">${Object.entries(mandate.mandate.internal_support).map(([id,yes]) => `<span>${yes ? '✓' : '–'} ${esc(roleNames[member(id).role] || member(id).role)}</span>`).join('')}</div><p class="mandate-explanation">Selected candidate: ${esc(mandate.winning_candidate)}. Approvals were recorded individually; the leader did not invent them.</p></div><div><h3>Team priorities</h3><ul class="mandate-goals">${mandate.mandate.priorities.map(g => `<li>${esc(g)}</li>`).join('')}</ul><h3 class="table-subheading">Red lines</h3><ul class="mandate-goals">${mandate.mandate.red_lines.map(g => `<li>${esc(g)}</li>`).join('')}</ul></div></div>`;
  }
  function renderFloor() {
    $('#floor-roster').innerHTML = s.replay.delegations.map(d => `<section class="floor-country"><h3>${esc(d.name)} · ${d.members.length}</h3>${d.members.map(id => {const a = member(id);return `<button type="button" class="floor-member" data-acting="${esc(id)}" aria-pressed="${s.acting === id}" ${s.hub.human_role && s.hub.human_role !== id ? 'disabled' : ''}><span class="role-dot ${roleClass(a.role)}"></span>${esc(roleNames[a.role] || a.role)}<span class="controller-pill">${controller(id) === 'human' ? 'HUMAN' : 'AGENT'}</span></button>`;}).join('')}</section>`).join('');
    $('#acting-member').innerHTML = options(s.acting); $('#acting-member').value = s.acting;
    $('#acting-member').disabled = !!s.hub.human_role || !['researcher','public'].includes(s.scope);
    $('#open-request').disabled = s.scope === 'public';
    $('#clock').textContent = `Tick ${s.hub.tick} · ${s.hub.acknowledgement_ticks}-tick ACK window`;
    $('#acting-note').textContent = s.hub.human_role ? 'You control only this member. Other controllers are not automated in this sandbox.' : s.scope === 'researcher' ? 'Researcher mode: manually act as any of the 18 members to test invitations and replies.' : s.scope === 'public' ? 'Select a participant or researcher perspective to access meeting actions.' : 'Actions use this member; private invitations and room messages are filtered server-side.';
    const meetings = s.hub.meetings;
    $('#meeting-count').textContent = `${meetings.length} visible meeting${meetings.length === 1 ? '' : 's'}`;
    if (!meetings.some(m => m.id === s.meeting)) s.meeting = meetings[0]?.id || null;
    $('#meeting-list').innerHTML = meetings.length ? [...meetings].reverse().map(m => {
      const canRespond = m.status === 'pending' && m.acknowledgements[s.acting] === 'pending';
      const canClose = ['pending','active'].includes(m.status) && [m.sender,...m.invitees].includes(s.acting);
      const deadline = m.status === 'pending' ? `${Math.max(0,m.expires_tick-s.hub.tick)} ticks left to acknowledge` : m.status === 'expired' ? 'ACK deadline missed. Choose a new action or request another meeting.' : m.status === 'active' ? 'All invitees acknowledged · room open' : m.status === 'declined' ? 'An invitee declined. Choose another action.' : 'Conversation closed.';
      return `<article class="meeting-item ${m.id === s.meeting ? 'selected' : ''}"><button class="meeting-select" type="button" data-meeting="${m.id}"><span class="meeting-item-top"><h3>${esc(m.topic)}</h3><span class="meeting-state ${m.status}">${m.status.toUpperCase()}</span></span><span class="meeting-names">${esc(shortName(m.sender))} → ${m.invitees.map(id => esc(shortName(id))).join(', ')}</span></button><div class="ack-list">${Object.entries(m.acknowledgements).map(([id,state]) => `<span class="ack-chip ${state}">${state === 'accepted' ? '✓' : state === 'declined' ? '×' : '…'} ${esc(shortName(id))}</span>`).join('')}</div><div class="meeting-deadline">${esc(deadline)}</div><div class="meeting-actions">${canRespond ? `<button type="button" class="button primary" data-ack="${m.id}">Acknowledge as ${esc(codes[member(s.acting).delegation])}</button><button type="button" class="button danger" data-decline="${m.id}">Decline</button>` : ''}${canClose ? `<button type="button" class="button secondary" data-close="${m.id}">Close</button>` : ''}</div></article>`;
    }).join('') : empty('No visible meeting requests','Request a one-to-one conversation or invite a cross-delegation group. Only the sender, invitees and researcher can see a private request.');
    renderRoom();
    const recorded = roundData().meetings || [];
    $('#agent-meetings-summary').textContent = `Agent meetings from the saved run · round ${s.round} · ${recorded.length} visible`;
    $('#agent-meetings').innerHTML = recorded.length ? recorded.map(m => `<article class="meeting-item"><span class="meeting-item-top"><h3>${esc(m.topic)}</h3><span class="meeting-state ${m.status}">${m.status.toUpperCase()}</span></span><span class="meeting-names">${esc(shortName(m.sender))} → ${m.invitees.map(id => esc(shortName(id))).join(', ')}</span>${m.messages.map(x => `<article class="room-message"><div class="room-message-heading"><span>${esc(memberName(x.actor))}</span><span>Tick ${x.tick}</span></div><div class="room-message-text">${esc(x.text)}</div></article>`).join('')}</article>`).join('') : empty('No agent meetings visible', s.scope === 'public' ? 'Meetings are private to their participants.' : 'None were recorded in this round for this perspective.');
    $('#public-transcript').innerHTML = roundData().public_events.filter(e => e.kind === 'summit_speech').map(e => {const a = member(e.actor);return `<article class="speech">${avatar(a)}<div class="speech-body"><div class="speech-heading"><strong>${esc(memberName(e.actor))}</strong><span>PUBLIC · ${esc(e.id)}</span></div><p class="speech-text">${esc(e.payload.speech)}</p></div></article>`;}).join('') || empty('No public statements','No public statements were recorded in this round.');
  }
  function renderRoom() {
    const meeting = s.hub.meetings.find(m => m.id === s.meeting);
    $('#room-title').textContent = meeting ? meeting.topic : 'Private room';
    $('#room-status').textContent = meeting ? `${meeting.id} · ${meeting.status}` : 'Select a meeting';
    $('#room-members').innerHTML = meeting ? `<div class="room-members">${[meeting.sender,...meeting.invitees].map(id => esc(memberName(id))).join('<br>')}<br><strong>Participant-only visibility</strong></div>` : '';
    $('#room-messages').innerHTML = meeting?.messages.length ? `<div class="room-chat">${meeting.messages.map(m => `<article class="room-message"><div class="room-message-heading"><span>${esc(memberName(m.actor))}</span><span>Tick ${m.tick}</span></div><div class="room-message-text">${esc(m.text)}</div></article>`).join('')}</div>` : empty(meeting?.status === 'pending' ? 'Waiting for acknowledgements' : meeting?.status === 'expired' ? 'Request expired' : meeting?.status === 'active' ? 'The private room is open' : 'No conversation selected',meeting?.status === 'pending' ? 'Conversation cannot begin until every invitee accepts. Switch the acting participant to acknowledge, or advance the clock to test expiry.' : meeting?.status === 'active' ? 'Send a message as a participant. Outsiders, including teammates who were not invited, cannot read this room.' : 'Choose an active meeting to discuss with its participants.');
    const canSend = meeting?.status === 'active' && [meeting.sender,...meeting.invitees].includes(s.acting) && s.scope !== 'public';
    $('#meeting-message').disabled = !canSend; $('#send-message').disabled = !canSend;
    $('#message-note').textContent = canSend ? `Sending as ${shortName(s.acting)}` : 'Only acknowledged meeting participants can send.';
  }
  function render() {renderChrome();renderAgents();renderDiscussion();renderAlignment();renderFloor();}
  function renderInvitees() {
    const sender = $('#request-sender').value;
    $('#invitees').innerHTML = s.replay.delegations.map(d => d.members.filter(id => id !== sender).map(id => `<label class="invitee-row"><input type="checkbox" name="invitee" value="${esc(id)}"><span class="role-dot ${roleClass(member(id).role)}"></span>${esc(shortName(id))}</label>`).join('')).join('');
    $('#invitation-count').textContent = 'No invitees selected';
  }
  document.addEventListener('click', event => {
    const b = event.target.closest('button'); if (!b || b.disabled) return;
    if (b.dataset.country) {s.country = b.dataset.country;s.workspace = 'delegation';s.agent = country(s.country).members[0];render();}
    if (b.dataset.round) {s.round = b.dataset.round;render();}
    if (b.dataset.agent) {s.agent = b.dataset.agent;render();}
    if (b.dataset.workspace) {s.workspace = b.dataset.workspace;render();}
    if (b.dataset.acting) {s.acting = b.dataset.acting; if (!['researcher','public'].includes(s.scope)) {s.scope = s.acting;load().catch(showError);} else renderFloor();}
    if (b.dataset.meeting) {s.meeting = b.dataset.meeting;renderFloor();}
    if (b.dataset.ack) act('respond',{meeting_id:b.dataset.ack,accepted:true});
    if (b.dataset.decline) act('respond',{meeting_id:b.dataset.decline,accepted:false});
    if (b.dataset.close) act('close',{meeting_id:b.dataset.close});
  });
  $('#viewer').addEventListener('change', async event => {
    s.scope = event.target.value;
    if (!['researcher','public'].includes(s.scope)) s.acting = s.scope;
    try {await load();} catch(error) {showError(error);}
  });
  $('#acting-member').addEventListener('change', event => {s.acting = event.target.value;renderFloor();});
  $('#advance-tick').addEventListener('click', () => act('advance'));
  $('#claim-role').addEventListener('click', async () => {
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
  load().catch(showError);
})();
