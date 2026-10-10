'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roles = {head_negotiator:'Head negotiator',economic_minister:'Economic minister',climate_adviser:'Climate adviser',observer:'Observer'};
  const codes = {usa:'US',eu:'EU',china:'CN',india:'IN',other_developed:'OD',other_developing:'OG',brazil:'BR',saudi_arabia:'SA'};
  const colors = {usa:'#e9ede3',eu:'#e1e8ee',china:'#efe2da',india:'#f3e8d4',other_developed:'#e9e6ef',other_developing:'#e2ece4',brazil:'#e5edda',saudi_arabia:'#ece6d6'};
  const stages = {prepare:'Preparation',internal:'Team discussion',mandate:'Mandate proposal',vote:'Team vote',announce:'Opening announcement',summary:'End-of-round summary',plan:'Next-round action plan',nominate:'Choosing partners',bilateral:'Bilateral message',debrief:'Report to team'};
  let session = null;
  let selectedCountry = null;
  let selectedAgent = null;
  let agentRequest = 0;
  let agentTrigger = null;
  let selectedTrace = null;
  let polling = false;
  let pollTimer = null;
  let controlPending = false;
  let bilaterals = null;
  let bilateralView = false;
  let selectedMeeting = null;

  const roleName = agent => {
    const name = roles[agent.role] || agent.role;
    const peers = session?.countries.find(country => country.id === agent.delegation)?.members.filter(member => member.role === agent.role) || [];
    return peers.length > 1 ? `${name} ${peers.findIndex(member => member.agent === agent.agent) + 1}` : name;
  };
  const roleClass = agent => agent.role.startsWith('economic') ? 'economic' : agent.role.startsWith('climate') ? 'climate' : agent.role === 'observer' ? 'observer' : 'head';
  const initials = agent => roleName(agent).split(' ').map(word => word[0]).slice(0,2).join('').toUpperCase();
  const avatar = agent => `<span class="agent-avatar ${roleClass(agent)}" aria-hidden="true">${esc(initials(agent))}</span>`;
  const empty = text => `<p class="empty-state">${esc(text)}</p>`;
  const countryById = id => session.countries.find(country => country.id === id);
  // Any agent, observers included: countries list their members, the bilaterals directory everyone.
  const agentById = id => session?.countries.flatMap(country => country.members).find(member => member.agent === id)
    || bilaterals?.participants.find(member => member.agent === id);
  const groupName = agent => countryById(agent.delegation)?.name || bilaterals?.participants.find(member => member.agent === agent.agent)?.delegation_name || agent.delegation;
  const fullName = id => { const agent = agentById(id); return agent ? (agent.role === 'observer' ? groupName(agent) : `${groupName(agent)} · ${roleName(agent)}`) : id; };

  async function read(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error('The session could not be loaded. Please try again.');
    return response.json();
  }

  function showError(error) {
    $('error-text').textContent = error.message;
    $('error').hidden = false;
  }

  function renderCountries() {
    const focused = document.activeElement;
    const focusCountry = focused?.dataset.country;
    $('countries').innerHTML = session.countries.map(country => {
      const members = country.members;
      const dots = members.slice(0,6).map(agent => `<span class="member-dot ${roleClass(agent)}" title="${esc(roleName(agent))}">${esc(initials(agent))}</span>`).join('');
      const live = country.live;
      const completed = country.completed || live?.phase === 'completed';
      const status = completed ? 'Meeting concluded' : live?.status === 'error' ? 'Retry needed' : live?.active ? 'Conversation running' : live?.messages ? 'Conversation paused' : 'Ready to begin';
      return `<article class="country-card${completed ? ' country-completed' : ''}" style="--country-color:${colors[country.id] || '#edf1e9'}"><button type="button" class="country-open" data-country="${esc(country.id)}" style="--country-color:${colors[country.id] || '#edf1e9'}" aria-label="Open ${esc(country.name)}, ${members.length} agents">
        <span class="country-card-top"><span class="country-code" aria-hidden="true">${esc(codes[country.id] || country.id.slice(0,2).toUpperCase())}</span><span class="card-arrow" aria-hidden="true">↗</span></span>
        <span class="country-name">${esc(country.name)}</span><span class="country-meta">${members.length} ${members.length === 1 ? 'agent' : 'agents'}</span>
        <span class="member-dots" aria-hidden="true">${dots}${members.length > 6 ? `<span class="member-overflow">+${members.length - 6}</span>` : ''}</span></button>
        <div class="country-actions"><span class="country-status" role="status">${esc(status)}</span></div></article>`;
    }).join('');
    if (focusCountry) document.querySelector(`[data-country="${focusCountry}"]`)?.focus();
    renderRoundControl();
    const total = session.countries.reduce((sum, country) => sum + country.members.length, 0);
    $('roster-total').textContent = `${session.countries.length} delegations · ${total} agents`;
    $('run-caption').textContent = `${session.mode === 'live' ? (session.backend === 'offline' ? 'Live scripted session' : 'Live Gemini conversation') : (session.backend === 'offline' ? 'Recorded scripted session' : 'Recorded Gemini session')} · ${session.run}`;
  }

  function openCountry(id) {
    const country = countryById(id);
    if (!country) return;
    selectedCountry = id;
    $('country-view').hidden = true;
    $('discussion-view').hidden = false;
    $('country-title').textContent = country.name;
    $('country-description').textContent = `${country.members.length} agents in this delegation. Select an agent to read its thinking traces.`;
    $('agents').innerHTML = country.members.map(agent => `<button type="button" class="agent-button" data-agent="${esc(agent.agent)}" aria-label="Inspect ${esc(roleName(agent))}">${avatar(agent)}<span><strong>${esc(roleName(agent))}</strong><small>View thinking traces</small></span><span class="small-arrow" aria-hidden="true">↗</span></button>`).join('');
    renderDiscussion(country);
    renderResults(country);
    renderLive(country);
    $('country-title').focus();
    window.scrollTo({top:0});
  }

  function renderDiscussion(country) {
    $('message-count').textContent = `${country.discussion.length} ${country.discussion.length === 1 ? 'message' : 'messages'}`;
    $('discussion').innerHTML = country.discussion.length ? country.discussion.map((event,index) => {
      const agent = country.members.find(member => member.agent === event.actor);
      const name = agent ? roleName(agent) : event.actor;
      return `<article class="message">${agent ? avatar(agent) : ''}<div class="message-body"><button type="button" class="speaker" data-agent="${esc(event.actor)}"${event.trace_id ? ` data-trace="${esc(event.trace_id)}"` : ''}>${esc(name)}</button><span class="message-number">${String(index + 1).padStart(2,'0')}</span><p class="message-text">${esc(event.payload.speech)}</p></div></article>`;
    }).join('') : empty(session.mode === 'live' ? 'Start the conversation. Agents will prepare, then respond to one another here.' : 'No Round 1 discussion was recorded for this delegation.');
  }

  function renderLive(country) {
    const live = country.live;
    $('live-controls').hidden = !live;
    if (!live) return;
    const agent = country.members.find(member => member.agent === live.thinking_agent);
    const statuses = {idle:'Ready to begin',queued:live.phase === 'summaries' ? 'Preparing agent summaries' : live.phase === 'plan' ? 'Preparing the next-round action plan' : 'Waiting for the next agent',paused:'Conversation paused',limited:'Message limit reached',error:live.error,completed:'Meeting concluded · summaries and next-round plan ready'};
    $('live-status').textContent = live.status === 'thinking' ? `${agent ? roleName(agent) : 'Agent'} is ${live.stage === 'prepare' ? 'preparing' : live.stage === 'summary' ? 'writing a summary' : live.stage === 'plan' ? 'preparing the action plan' : 'thinking'}…` : live.status === 'pausing' ? 'Pausing after the current response…' : statuses[live.status] || live.status;
    $('live-budget').textContent = live.phase === 'completed' ? 'Round 1 is complete.' : live.phase === 'summaries' || live.phase === 'plan' ? 'The team is wrapping up this round.' : `Up to ${session.message_budget} new messages per start · ${live.remaining} remaining`;
    $('finish-conversation').hidden = live.phase && live.phase !== 'discussion';
    $('finish-conversation').textContent = live.finish_requested ? 'Head assessing closure…' : 'Ask head to conclude';
    $('finish-conversation').disabled = controlPending || !live.can_finish || live.finish_requested;
  }

  function goBack() {
    closeAgent();
    $('discussion-view').hidden = true;
    $('country-view').hidden = false;
    const card = document.querySelector(`[data-country="${selectedCountry}"]`);
    selectedCountry = null;
    if (card) card.focus();
    window.scrollTo({top:0});
  }

  function renderResults(country) {
    const summaries = country.summaries || [];
    const plan = country.action_plan;
    $('round-results').hidden = !summaries.length && !plan && !country.ending;
    const bullets = (title,items) => items?.length ? `<div><h4>${esc(title)}</h4><ul>${items.map(text => `<li>${esc(text)}</li>`).join('')}</ul></div>` : '';
    $('round-results').innerHTML = `${country.ending ? `<p class="closing-note">${esc(country.ending.reason)}</p>` : ''}
      ${summaries.length ? `<h2>End of Round 1</h2><div class="round-summaries">${summaries.map(event => {
        const agent = country.members.find(member => member.agent === event.actor);
        const summary = event.payload.summary;
        return `<article class="round-summary"><h3>${esc(agent ? roleName(agent) : event.actor)}</h3><p>${esc(summary.summary)}</p>${summary.agreements?.length || summary.unresolved_issues?.length || summary.responsibilities?.length ? `<details><summary>Agreements and remaining work</summary>${bullets('Agreements',summary.agreements)}${bullets('Unresolved issues',summary.unresolved_issues)}${bullets('My follow-up work',summary.responsibilities)}</details>` : ''}</article>`;
      }).join('')}</div>` : ''}
      ${plan ? `<h2>Action plan for Round ${esc(plan.next_round)}</h2><div class="action-plan"><table><thead><tr><th>Who</th><th>What to work on</th><th>Deliverable</th></tr></thead><tbody>${plan.assignments.map(task => {
        const agent = country.members.find(member => member.agent === task.agent_id);
        return `<tr><th scope="row">${esc(agent ? roleName(agent) : task.agent_id)}</th><td>${esc(task.task)}</td><td>${esc(task.deliverable)}</td></tr>`;
      }).join('')}</tbody></table></div>` : ''}`;
  }

  function thoughtText(value) {
    const inline = text => esc(text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
    return String(value).split(/\n\s*\n/).filter(Boolean).map(block => {
      const lines = block.split('\n').filter(line => line.trim());
      if (lines.every(line => /^\s*[-*]\s+/.test(line))) return `<ul>${lines.map(line => `<li>${inline(line.replace(/^\s*[-*]\s+/,''))}</li>`).join('')}</ul>`;
      return `<p>${inline(block.replace(/^#{1,6}\s+/gm,''))}</p>`;
    }).join('');
  }

  function thinkingRounds(data) {
    const rounds = data.thinking_rounds || [{round:1,traces:(data.traces || []).map(event => {
      const calls = event.payload.generation?.calls || [];
      const call = [...calls].reverse().find(item => item.accepted);
      const thoughts = call?.attempt > 1 ? [] : call?.thought_summaries || [];
      const summary = event.payload.decision_summary?.summary;
      return {id:event.id,stage:event.payload.stage,thoughts:thoughts.length ? thoughts : summary ? [summary] : [],source:thoughts.length ? 'provider_summary' : event.payload.summary_source};
    })}];
    return rounds.map((round,roundIndex) => {
      let contribution = 0;
      let message = 0;
      return `<section class="thinking-round"><h3>${esc(round.title || `Round ${round.round}`)}</h3>${round.traces.length ? round.traces.map((trace,index) => {
        const label = trace.stage === 'internal' ? `Contribution ${++contribution}` : trace.stage === 'bilateral' ? `Bilateral message ${++message}` : stages[trace.stage] || trace.stage;
        const selected = trace.id === selectedTrace;
        const expanded = selected || (!selectedTrace && roundIndex === rounds.length - 1 && index === round.traces.length - 1);
        const source = trace.source === 'provider_summary' ? 'Provider thought summary' : trace.source === 'instrumented_scripted_policy' ? 'Scripted policy summary' : 'Recorded decision summary';
        return `<details class="thinking-trace${selected ? ' selected-decision' : ''}" id="decision-${esc(trace.id)}"${expanded ? ' open' : ''}><summary>${esc(label)}</summary><div class="trace-text"><small>${esc(source)}</small>${trace.thoughts.length ? trace.thoughts.map(thoughtText).join('') : '<p>No thinking summary was returned.</p>'}</div></details>`;
      }).join('') : empty('No thinking traces yet.')}</section>`;
    }).join('');
  }

  async function openAgent(id,trigger,traceId = null,refresh = false) {
    const agent = agentById(id);
    if (!agent) return;
    selectedAgent = id;
    if (!refresh) { agentTrigger = trigger; selectedTrace = traceId; }
    const request = ++agentRequest;
    $('agent-country').textContent = groupName(agent);
    $('agent-title').textContent = roleName(agent);
    if (!refresh) $('agent-details').innerHTML = '<p class="loading">Loading thinking traces…</p>';
    if (!$('agent-dialog').open) $('agent-dialog').showModal();
    try {
      const data = await read(`/api/round-one/agent?scope=researcher&agent=${encodeURIComponent(id)}&view=thinking`);
      if (request !== agentRequest || selectedAgent !== id) return;
      const signature = JSON.stringify(data.thinking_rounds || data.traces);
      if (refresh && $('agent-details').dataset.signature === signature) return;
      const expanded = refresh ? [...$('agent-details').querySelectorAll('details[open]')].map(item => item.id) : [];
      $('agent-details').dataset.signature = signature;
      $('agent-details').innerHTML = thinkingRounds(data);
      for (const traceId of expanded) { const detail = document.getElementById(traceId); if (detail) detail.open = true; }
      if (!refresh && selectedTrace) document.getElementById(`decision-${selectedTrace}`)?.scrollIntoView?.({block:'nearest'});
    } catch (error) {
      if (request === agentRequest && selectedAgent === id) $('agent-details').innerHTML = empty(error.message);
    }
  }

  function renderBilateralEntry() {
    const live = bilaterals?.live;
    const meetings = (bilaterals?.rounds || []).flatMap(round => round.slots).flatMap(slot => slot.meetings).length;
    $('bilaterals-entry').hidden = !bilaterals || (!live?.enabled && !meetings);
    if ($('bilaterals-entry').hidden) return;
    $('bilaterals-entry-status').textContent = live && !live.open
      ? `Opens once every delegation has concluded Round 1 · ${live.concluded} of ${live.countries} concluded`
      : `${meetings} ${meetings === 1 ? 'conversation' : 'conversations'}${live ? ` · ${live.done ? 'all slots complete' : `slot ${live.slot} of ${live.slots}`}` : ''}`;
  }

  function openBilaterals() {
    bilateralView = true;
    document.querySelector('.round-label').textContent = 'ROUND 02';
    $('country-view').hidden = true;
    $('bilaterals-view').hidden = false;
    renderBilaterals();
    $('bilaterals-title').focus();
    window.scrollTo({top:0});
  }

  function closeBilaterals() {
    closeAgent();
    bilateralView = false;
    document.querySelector('.round-label').textContent = 'ROUND 01';
    $('bilaterals-view').hidden = true;
    $('country-view').hidden = false;
    $('bilaterals-entry').focus();
    window.scrollTo({top:0});
  }

  const meetingsOf = () => (bilaterals?.rounds || []).flatMap(round => round.slots.flatMap(slot => slot.meetings));
  const person = id => { const agent = agentById(id); return `${agent ? avatar(agent) : ''}<span class="person-name"><strong>${esc(agent && agent.role !== 'observer' ? groupName(agent) : fullName(id))}</strong><small>${esc(agent ? roleName(agent) : '')}</small></span>`; };
  const meetingState = meeting => meeting.status === 'active' ? 'Talking' : meeting.close_reason === 'limit' ? 'Message limit' : meeting.messages.length <= 1 && meeting.closed_by === meeting.partner ? 'Declined' : 'Ended';

  function renderBilaterals() {
    renderBilateralControls();
    const rounds = bilaterals?.rounds || [];
    if (!meetingsOf().some(meeting => meeting.id === selectedMeeting)) selectedMeeting = meetingsOf()[0]?.id || null;
    $('bilateral-slots').innerHTML = rounds.length ? rounds.map(round => round.slots.map(slot => {
      const requests = Object.entries(slot.nominations);
      return `<section class="bilateral-slot"><h2>${rounds.length > 1 ? `Before Round ${esc(round.round)} · ` : ''}Slot ${esc(slot.slot)}</h2><p class="slot-meta">${slot.paired ? `${slot.meetings.length} ${slot.meetings.length === 1 ? 'conversation' : 'conversations'}${slot.unmatched.length ? ` · ${slot.unmatched.length} without a partner` : ''}` : `${requests.length} of ${bilaterals.participants.length} agents have chosen partners`}</p>
        ${slot.meetings.map(meeting => `<button type="button" class="meeting-card${meeting.id === selectedMeeting ? ' selected' : ''}" data-meeting="${esc(meeting.id)}" aria-pressed="${meeting.id === selectedMeeting}"><span class="meeting-people">${person(meeting.initiator)}<span class="meeting-link" aria-hidden="true">↔</span>${person(meeting.partner)}</span><span class="meeting-topic">${esc(meeting.topic)}</span><span class="meeting-meta"><span class="chip${meeting.status === 'active' ? ' live' : ''}">${esc(meetingState(meeting))}</span>${meeting.mutual ? '<span class="chip">Both asked</span>' : ''}<span>${meeting.messages.length} ${meeting.messages.length === 1 ? 'message' : 'messages'}</span></span></button>`).join('')}
        ${requests.length ? `<details class="requests"><summary>Who asked for whom</summary><ul>${requests.map(([id,item]) => `<li><button type="button" class="speaker" data-agent="${esc(id)}"${item.trace_id ? ` data-trace="${esc(item.trace_id)}"` : ''}>${esc(fullName(id))}</button> → ${item.requests.length ? item.requests.map(request => `${esc(fullName(request.member))} <span class="request-topic">(${esc(request.topic)})</span>`).join(', ') : '<span class="request-topic">nobody</span>'}</li>`).join('')}</ul></details>` : ''}</section>`;
    }).join('')).join('') : empty(bilaterals?.live?.open ? 'Start the bilaterals. Agents first choose who they want to talk to.' : 'Bilaterals start after every delegation has concluded Round 1.');
    renderMeeting();
  }

  function renderBilateralControls() {
    const live = bilaterals?.live;
    $('bilateral-controls').hidden = !live;
    if (!live) return;
    const thinking = live.thinking_agents.length;
    const statuses = {idle:'Ready to begin',queued:'Waiting for the next step',paused:'Bilaterals paused',slot_done:`Slot ${live.slot - 1} complete · read the conversations, then start slot ${live.slot}`,completed:'All bilateral slots are complete',error:live.error};
    $('bilateral-status').textContent = !live.open ? `Opens once every delegation has concluded Round 1 · ${live.concluded} of ${live.countries} concluded`
      : live.status === 'thinking' ? `${thinking} ${thinking === 1 ? 'agent is' : 'agents are'} thinking…` : live.status === 'pausing' ? 'Pausing after the current step…' : statuses[live.status] || live.status;
    $('bilateral-detail').textContent = `${live.slots} ${live.slots === 1 ? 'slot' : 'slots'} · up to ${live.message_limit} messages per conversation · each start runs one slot`;
    $('bilateral-control').textContent = live.done ? 'Bilaterals complete' : live.active ? 'Pause bilaterals' : live.status === 'idle' ? 'Start bilaterals' : `Continue slot ${live.slot}`;
    $('bilateral-control').disabled = controlPending || !live.open || live.done || live.status === 'pausing';
  }

  function renderMeeting() {
    const meeting = meetingsOf().find(item => item.id === selectedMeeting);
    if (!meeting) { $('bilateral-conversation').innerHTML = ''; return; }
    const side = id => id === meeting.initiator ? 'left' : 'right';
    const asked = meeting.mutual ? 'Both asked for this meeting.' : `${fullName(meeting.initiator)} asked for this meeting.`;
    const ended = meeting.status === 'active' ? '' : meeting.close_reason === 'limit' ? 'Ended at the message limit.' : `${fullName(meeting.closed_by)} ended the conversation.`;
    $('bilateral-conversation').innerHTML = `<p class="eyebrow">SLOT ${esc(meeting.slot)} · PRIVATE</p><h2>${esc(meeting.topic)}</h2><p class="conversation-note">${esc(asked)} Only these two agents see this conversation.</p>
      <div class="bilateral-messages">${meeting.messages.map(message => { const agent = agentById(message.actor); return `<article class="bubble ${side(message.actor)}">${agent ? avatar(agent) : ''}<div class="bubble-body"><button type="button" class="speaker" data-agent="${esc(message.actor)}"${message.trace_id ? ` data-trace="${esc(message.trace_id)}"` : ''}>${esc(fullName(message.actor))}</button><p class="message-text">${esc(message.text)}</p></div></article>`; }).join('') || empty('The opener is preparing the first message.')}</div>
      ${ended ? `<p class="conversation-note">${esc(ended)}</p>` : ''}
      ${Object.keys(meeting.reports).length ? `<h3 class="reports-title">What each side told its own team</h3><div class="meeting-reports">${Object.entries(meeting.reports).map(([id,report]) => `<article class="round-summary"><h3><button type="button" class="speaker" data-agent="${esc(id)}"${report.trace_id ? ` data-trace="${esc(report.trace_id)}"` : ''}>${esc(fullName(id))}</button></h3><p>${esc(report.summary)}</p>${report.agreements.length ? `<h4>Understandings</h4><ul>${report.agreements.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : ''}${report.next_steps.length ? `<h4>Next steps</h4><ul>${report.next_steps.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : ''}</article>`).join('')}</div>` : ''}`;
  }

  async function controlBilaterals() {
    if (!bilaterals?.live || controlPending) return;
    controlPending = true;
    renderBilateralControls();
    try {
      const response = await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json','X-Simpact-Lab':'1'},body:JSON.stringify({track:'bilaterals',operation:bilaterals.live.active ? 'pause' : 'start'})});
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || 'The bilaterals control failed. Please try again.');
      Object.assign(bilaterals.live, await response.json());
    } catch (error) { showError(error); }
    finally { controlPending = false; renderBilateralControls(); }
  }

  function closeAgent() {
    ++agentRequest;
    selectedAgent = null;
    selectedTrace = null;
    if ($('agent-dialog').open) $('agent-dialog').close();
    $('agent-details').innerHTML = '';
    if (agentTrigger?.isConnected) agentTrigger.focus();
    agentTrigger = null;
  }

  async function load() {
    $('error').hidden = true;
    $('retry').disabled = true;
    try {
      session = await read('/api/round-one?scope=researcher');
      if (session.round !== 1 || !Array.isArray(session.countries)) throw new Error('The Round 1 record is unavailable.');
      bilaterals = await read('/api/bilaterals?scope=researcher');
      renderCountries();
      renderBilateralEntry();
      if (bilateralView) renderBilaterals();
      if (selectedCountry) openCountry(selectedCountry);
      if (session.mode === 'live' && !pollTimer) pollTimer = window.setTimeout(poll,1200);
    } catch (error) { $('roster-total').textContent = 'Session unavailable'; showError(error); }
    finally { $('retry').disabled = false; }
  }

  async function poll() {
    pollTimer = null;
    if (polling || session?.mode !== 'live') return;
    polling = true;
    try {
      const next = await read('/api/round-one?scope=researcher');
      const previous = selectedCountry ? countryById(selectedCountry) : null;
      session = next;
      renderCountries();
      const nextBilaterals = await read('/api/bilaterals?scope=researcher');
      const changed = JSON.stringify(nextBilaterals) !== JSON.stringify(bilaterals);
      bilaterals = nextBilaterals;
      renderBilateralEntry();
      if (bilateralView && changed) renderBilaterals();
      if (bilateralView && changed && selectedAgent) await openAgent(selectedAgent,agentTrigger,selectedTrace,true);
      if (selectedCountry) {
        const country = countryById(selectedCountry);
        if (country.discussion.length !== previous?.discussion.length) renderDiscussion(country);
        renderLive(country);
        if (JSON.stringify([country.summaries,country.action_plan,country.ending]) !== JSON.stringify([previous?.summaries,previous?.action_plan,previous?.ending])) renderResults(country);
        if (selectedAgent) await openAgent(selectedAgent,agentTrigger,selectedTrace,true);
      }
      $('error').hidden = true;
    } catch (error) { showError(error); }
    finally { polling = false; pollTimer = window.setTimeout(poll,1200); }
  }

  function renderRoundControl() {
    const countries = session.countries.filter(country => country.live && !country.completed && country.live.phase !== 'completed');
    const active = countries.some(country => country.live.active);
    $('round-controls').hidden = session.mode !== 'live';
    $('round-control').textContent = !countries.length ? 'Round 1 finished' : active ? 'Ⅱ Pause all countries' : '▶ Play all countries';
    $('round-control').disabled = controlPending || !countries.length;
    $('round-status').textContent = `${session.countries.length - countries.length} of ${session.countries.length} meetings concluded`;
  }

  async function controlRound() {
    if (controlPending) return;
    const unfinished = session.countries.filter(country => country.live && !country.completed && country.live.phase !== 'completed');
    const operation = unfinished.some(country => country.live.active) ? 'pause' : 'start';
    const targets = unfinished.filter(country => operation === 'pause' ? country.live.active : !country.live.active);
    if (!targets.length) return;
    controlPending = true;
    renderRoundControl();
    try {
      const results = await Promise.allSettled(targets.map(async country => {
        const response = await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json','X-Simpact-Lab':'1'},body:JSON.stringify({operation,country:country.id})});
        if (!response.ok) throw new Error(`Could not ${operation} ${country.name}. Try again.`);
        country.live = await response.json();
      }));
      const failure = results.find(result => result.status === 'rejected');
      if (failure) throw failure.reason;
      $('error').hidden = true;
    } catch (error) { showError(error); }
    finally { controlPending = false; renderCountries(); }
  }

  async function controlConversation(operation = null, countryId = selectedCountry) {
    if (!countryId || controlPending) return;
    const country = countryById(countryId);
    if (!country?.live || country.completed || country.live.phase === 'completed') return;
    controlPending = true;
    renderCountries();
    try {
      const response = await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json','X-Simpact-Lab':'1'},body:JSON.stringify({operation:operation || (country.live.active ? 'pause' : 'start'),country:countryId})});
      if (!response.ok) throw new Error('The conversation control failed. Please try again.');
      country.live = await response.json();
      if (selectedCountry === countryId) renderLive(country);
    } catch (error) { showError(error); }
    finally { controlPending = false; renderCountries(); if (selectedCountry) renderLive(countryById(selectedCountry)); }
  }

  document.addEventListener('click',event => {
    const button = event.target.closest('button[data-country], button[data-agent], button[data-meeting]');
    if (!button) return;
    if (button.dataset.meeting) {
      selectedMeeting = button.dataset.meeting;
      renderBilaterals();
      if (window.matchMedia('(max-width: 850px)').matches) $('bilateral-conversation').scrollIntoView({block:'start'});
    }
    else if (button.dataset.country) openCountry(button.dataset.country);
    else if (button.dataset.agent) openAgent(button.dataset.agent,button,button.dataset.trace || null);
  });
  $('back-to-countries').addEventListener('click',goBack);
  $('bilaterals-entry').addEventListener('click',openBilaterals);
  $('back-from-bilaterals').addEventListener('click',closeBilaterals);
  $('bilateral-control').addEventListener('click',controlBilaterals);
  $('close-agent').addEventListener('click',closeAgent);
  $('agent-dialog').addEventListener('close',() => { ++agentRequest; selectedAgent = null; $('agent-details').innerHTML = ''; });
  $('retry').addEventListener('click',load);
  $('round-control').addEventListener('click',controlRound);
  $('finish-conversation').addEventListener('click',() => controlConversation('finish'));
  load();
})();
