'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roles = {head_negotiator:'Head negotiator',economic_minister:'Economic minister',climate_adviser:'Climate adviser'};
  const codes = {usa:'US',eu:'EU',china:'CN',india:'IN',other_developed:'OD',other_developing:'OG'};
  const colors = {usa:'#e9ede3',eu:'#e1e8ee',china:'#efe2da',india:'#f3e8d4',other_developed:'#e9e6ef',other_developing:'#e2ece4'};
  const stages = {prepare:'Preparation',internal:'Team discussion',mandate:'Mandate proposal',vote:'Team vote',announce:'Opening announcement'};
  let session = null;
  let selectedCountry = null;
  let selectedAgent = null;
  let agentRequest = 0;
  let agentTrigger = null;
  let selectedTrace = null;
  let polling = false;
  let pollTimer = null;
  let controlPending = false;

  const roleName = agent => {
    const name = roles[agent.role] || agent.role;
    const peers = session?.countries.find(country => country.id === agent.delegation)?.members.filter(member => member.role === agent.role) || [];
    return peers.length > 1 ? `${name} ${peers.findIndex(member => member.agent === agent.agent) + 1}` : name;
  };
  const roleClass = agent => agent.role.startsWith('economic') ? 'economic' : agent.role.startsWith('climate') ? 'climate' : 'head';
  const initials = agent => roleName(agent).split(' ').map(word => word[0]).slice(0,2).join('').toUpperCase();
  const avatar = agent => `<span class="agent-avatar ${roleClass(agent)}" aria-hidden="true">${esc(initials(agent))}</span>`;
  const empty = text => `<p class="empty-state">${esc(text)}</p>`;
  const countryById = id => session.countries.find(country => country.id === id);

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
    $('countries').innerHTML = session.countries.map(country => {
      const members = country.members;
      const dots = members.slice(0,6).map(agent => `<span class="member-dot ${roleClass(agent)}" title="${esc(roleName(agent))}">${esc(initials(agent))}</span>`).join('');
      return `<button type="button" class="country-card" data-country="${esc(country.id)}" style="--country-color:${colors[country.id] || '#edf1e9'}" aria-label="Open ${esc(country.name)}, ${members.length} agents">
        <span class="country-card-top"><span class="country-code" aria-hidden="true">${esc(codes[country.id] || country.id.slice(0,2).toUpperCase())}</span><span class="card-arrow" aria-hidden="true">↗</span></span>
        <span class="country-name">${esc(country.name)}</span><span class="country-meta">${members.length} ${members.length === 1 ? 'agent' : 'agents'}</span>
        <span class="member-dots" aria-hidden="true">${dots}${members.length > 6 ? `<span class="member-overflow">+${members.length - 6}</span>` : ''}</span></button>`;
    }).join('');
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
    $('country-description').textContent = `${country.members.length} agents in this delegation. Select an agent to see its perspective.`;
    $('agents').innerHTML = country.members.map(agent => `<button type="button" class="agent-button" data-agent="${esc(agent.agent)}" aria-label="Inspect ${esc(roleName(agent))}">${avatar(agent)}<span><strong>${esc(roleName(agent))}</strong><small>View agent perspective</small></span><span class="small-arrow" aria-hidden="true">↗</span></button>`).join('');
    renderDiscussion(country);
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
    const statuses = {idle:'Ready to begin',queued:'Waiting for the next agent',paused:'Conversation paused',limited:'Message limit reached',error:live.error};
    $('live-status').textContent = live.status === 'thinking' ? `${agent ? roleName(agent) : 'Agent'} is ${live.stage === 'prepare' ? 'preparing' : 'thinking'}…` : live.status === 'pausing' ? 'Pausing after the current response…' : statuses[live.status] || live.status;
    $('live-budget').textContent = `Up to ${session.message_budget} new messages per start · ${live.remaining} remaining`;
    $('conversation-control').textContent = live.active ? 'Pause conversation' : live.messages || live.status !== 'idle' ? 'Continue conversation' : 'Start conversation';
    $('conversation-control').disabled = controlPending || live.status === 'pausing';
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

  function proposal(commitment) {
    if (!commitment) return '';
    const fields = [['Emissions peak',commitment.emissions_peak_year],['Reductions begin',commitment.reduction_start_year],['Annual reductions',`${commitment.annual_reduction_rate_percent}%`],['Climate finance',`${commitment.climate_fund_billion_per_year > 0 ? '+' : ''}$${commitment.climate_fund_billion_per_year}B / year`]];
    return `<dl class="proposal">${fields.map(([label,value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
  }

  function observation(event) {
    const p = event.payload;
    let text = p.speech || p.private_strategy || p.summary;
    if (!text && p.meeting) text = p.meeting.topic;
    if (!text && p.mandate) text = (p.mandate.priorities || []).join(' · ');
    if (!text && p.ranked_candidates) text = `Ranked candidates: ${p.ranked_candidates.join(', ')}`;
    if (!text && p.commitments) text = 'The delegations’ published commitments.';
    return `<div class="observation"><div class="evidence-label">${esc(event.kind.replaceAll('_',' '))} · ${esc(event.id)}</div>${text ? `<p>${esc(text)}</p>` : '<p>This observation has no recorded dialogue.</p>'}</div>`;
  }

  function decision(event,index) {
    const trace = event.payload;
    const summary = trace.decision_summary;
    const action = trace.action || {};
    const cited = new Set(summary?.evidence_event_ids || []);
    const evidence = (trace.observations || []).filter(item => cited.has(item.id));
    const observations = trace.observations || [];
    return `<article class="decision${selectedTrace === event.id ? ' selected-decision' : ''}" id="decision-${esc(event.id)}"><div class="decision-stage">${String(index + 1).padStart(2,'0')} · ${esc(stages[trace.stage] || trace.stage)}${selectedTrace === event.id ? ' · Selected message' : ''}</div>
      <p>${esc(summary?.summary || 'No decision summary was recorded for this action.')}</p>
      ${summary?.decision_type ? `<p class="intention">Decision · ${esc(summary.decision_type)}${summary.confidence != null ? ` · Self-reported confidence ${Math.round(summary.confidence * 100)}%` : ''}</p>` : ''}
      ${summary?.intended_action ? `<p class="intention">Intended action · ${esc(summary.intended_action)}</p>` : ''}
      ${generation(trace.generation)}
      ${evidence.length ? `<details><summary>Evidence behind this decision · ${evidence.length}</summary><div class="evidence">${evidence.map(observation).join('')}</div></details>` : ''}
      <details><summary>What the agent observed · ${observations.length}</summary><div class="evidence">${observations.length ? observations.map(observation).join('') : '<p>No earlier observations were recorded.</p>'}</div></details>
      ${trace.preference_before || trace.preference_after ? `<details><summary>How its proposal changed</summary><div class="evidence">${trace.preference_before ? `<p>Before this decision</p>${proposal(trace.preference_before)}` : '<p>No prior proposal.</p>'}${trace.preference_after ? `<p>After this decision</p>${proposal(trace.preference_after)}` : ''}</div></details>` : ''}
      ${(action.speech || action.commitment || action.mandate || action.ranked_candidates?.length) ? `<details><summary>What the agent decided</summary><div class="evidence">${action.speech ? `<p>${esc(action.speech)}</p>` : ''}${proposal(action.commitment || action.mandate?.approved_commitment)}${action.ranked_candidates?.length ? `<p>Candidate ranking: ${action.ranked_candidates.map(esc).join(' → ')}</p>` : ''}</div></details>` : ''}</article>`;
  }

  function generation(record) {
    if (!record) return '';
    const calls = record.calls || [];
    return `<div class="call-record"><p class="intention">${esc(record.model)} · Thinking level: ${esc(record.thinking_level || 'Model default')} · ${calls.length} ${calls.length === 1 ? 'attempt' : 'attempts'}</p>${calls.map(call => {
      const u = call.usage || {};
      const stats = [['Input',u.prompt_token_count],['Output',u.candidates_token_count],['Thinking',u.thoughts_token_count],['Total',u.total_token_count]].filter(([,v]) => v != null);
      return `<details><summary>Call ${call.attempt} · ${call.accepted ? 'Accepted' : call.error_type ? 'Failed' : 'Validation repair'}${call.latency_ms != null ? ` · ${(call.latency_ms / 1000).toFixed(1)}s` : ''}</summary><div class="evidence"><p>${stats.length ? stats.map(([label,n]) => `${label}: ${n} tokens`).join(' · ') : 'Token usage was not returned.'}</p>${call.thought_summaries?.length ? `<p class="evidence-label">Provider thought summary</p>${call.thought_summaries.map(text => `<p>${esc(text)}</p>`).join('')}` : '<p>No provider thought summary was returned for this call.</p>'}${call.error_type ? `<p>${esc(call.error_type)}${call.http_status ? ` · HTTP ${esc(call.http_status)}` : ''}</p>` : ''}${call.validation_error ? `<p>${esc(call.validation_error)}</p>` : ''}<details><summary>Exact model input</summary><pre>${esc(record.system_instruction)}\n\n${esc(call.input)}</pre></details><details><summary>Model output</summary><pre>${esc(call.output || 'No output was returned.')}</pre></details></div></details>`;
    }).join('')}</div>`;
  }

  async function openAgent(id,trigger,traceId = null,refresh = false) {
    const country = countryById(selectedCountry);
    const agent = country?.members.find(member => member.agent === id);
    if (!agent) return;
    selectedAgent = id;
    if (!refresh) { agentTrigger = trigger; selectedTrace = traceId; }
    const request = ++agentRequest;
    $('agent-country').textContent = `${country.name} · Round 1`;
    $('agent-title').textContent = roleName(agent);
    if (!refresh) $('agent-details').innerHTML = '<p class="loading">Loading this agent’s perspective…</p>';
    if (!$('agent-dialog').open) $('agent-dialog').showModal();
    try {
      const data = await read(`/api/round-one/agent?scope=researcher&agent=${encodeURIComponent(id)}`);
      if (request !== agentRequest || selectedAgent !== id) return;
      const detail = data.agent;
      const traces = data.traces.filter(event => event.round === 1 && event.actor === id && stages[event.payload.stage]);
      const scripted = detail.backend === 'offline-scripted-v1';
      const signature = JSON.stringify([data.traces,data.failures,detail.private_strategy]);
      if (refresh && $('agent-details').dataset.signature === signature) return;
      $('agent-details').dataset.signature = signature;
      $('agent-details').innerHTML = `<p class="agent-source">${scripted ? 'Scripted agent · recorded policy summaries' : 'Gemini agent · decision records and provider call metadata'}</p>
        <section class="detail-section"><h3>Priorities</h3><p>${esc(detail.personality)}</p></section>
        <section class="detail-section preparation"><h3>Private preparation</h3><p>${esc(detail.private_strategy || 'No preparation notes were recorded for this agent.')}</p>${detail.initial_preference ? `<details><summary>Starting proposal</summary>${proposal(detail.initial_preference)}</details>` : ''}</section>
        <section class="detail-section"><h3>Decisions in Round 1</h3>${traces.length ? traces.map(decision).join('') : empty('No decision records yet. Start the conversation to generate them.')}</section>
        ${(data.failures || []).map(event => `<section class="decision"><h3>Failed call · ${esc(event.payload.stage)}</h3><p>${esc(event.payload.error_type)}</p>${generation(event.payload.generation)}</section>`).join('')}`;
      if (!refresh && selectedTrace) document.getElementById(`decision-${selectedTrace}`)?.scrollIntoView?.({block:'nearest'});
    } catch (error) {
      if (request === agentRequest && selectedAgent === id) $('agent-details').innerHTML = empty(error.message);
    }
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
      renderCountries();
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
      if (selectedCountry) {
        const country = countryById(selectedCountry);
        if (country.discussion.length !== previous?.discussion.length) renderDiscussion(country);
        renderLive(country);
        if (selectedAgent) await openAgent(selectedAgent,agentTrigger,selectedTrace,true);
      }
      $('error').hidden = true;
    } catch (error) { showError(error); }
    finally { polling = false; pollTimer = window.setTimeout(poll,1200); }
  }

  async function controlConversation() {
    if (!selectedCountry || controlPending) return;
    const countryId = selectedCountry;
    const country = countryById(selectedCountry);
    const button = $('conversation-control');
    button.disabled = true;
    controlPending = true;
    try {
      const response = await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json','X-Simpact-Lab':'1'},body:JSON.stringify({operation:country.live.active ? 'pause' : 'start',country:selectedCountry})});
      if (!response.ok) throw new Error('The conversation control failed. Please try again.');
      country.live = await response.json();
      if (selectedCountry === countryId) renderLive(country);
    } catch (error) { showError(error); }
    finally { controlPending = false; if (selectedCountry) renderLive(countryById(selectedCountry)); }
  }

  document.addEventListener('click',event => {
    const button = event.target.closest('button[data-country], button[data-agent]');
    if (!button) return;
    if (button.dataset.country) openCountry(button.dataset.country);
    else if (button.dataset.agent) openAgent(button.dataset.agent,button,button.dataset.trace || null);
  });
  $('back-to-countries').addEventListener('click',goBack);
  $('close-agent').addEventListener('click',closeAgent);
  $('agent-dialog').addEventListener('close',() => { ++agentRequest; selectedAgent = null; $('agent-details').innerHTML = ''; });
  $('retry').addEventListener('click',load);
  $('conversation-control').addEventListener('click',controlConversation);
  load();
})();
