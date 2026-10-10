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
    if (!response.ok) throw new Error('The recorded session could not be loaded. Please try again.');
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
    $('run-caption').textContent = `${session.backend === 'offline' ? 'Recorded scripted session' : 'Recorded Gemini session'} · ${session.run}`;
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
    $('message-count').textContent = `${country.discussion.length} ${country.discussion.length === 1 ? 'message' : 'messages'}`;
    $('discussion').innerHTML = country.discussion.length ? country.discussion.map((event,index) => {
      const agent = country.members.find(member => member.agent === event.actor);
      const name = agent ? roleName(agent) : event.actor;
      const turn = country.members.length ? Math.floor(index / country.members.length) + 1 : 1;
      return `${index % Math.max(1,country.members.length) === 0 ? `<div class="discussion-turn">Conversation ${turn}</div>` : ''}<article class="message">${agent ? avatar(agent) : ''}<div class="message-body"><button type="button" class="speaker" data-agent="${esc(event.actor)}">${esc(name)}</button><span class="message-number">${String(index + 1).padStart(2,'0')}</span><p class="message-text">${esc(event.payload.speech)}</p></div></article>`;
    }).join('') : empty('No Round 1 discussion was recorded for this delegation.');
    $('country-title').focus();
    window.scrollTo({top:0});
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
    return `<article class="decision"><div class="decision-stage">${String(index + 1).padStart(2,'0')} · ${esc(stages[trace.stage] || trace.stage)}</div>
      <p>${esc(summary?.summary || 'No decision summary was recorded for this action.')}</p>
      ${summary?.intended_action ? `<p class="intention">Intended action · ${esc(summary.intended_action)}</p>` : ''}
      ${evidence.length ? `<details><summary>Evidence behind this decision · ${evidence.length}</summary><div class="evidence">${evidence.map(observation).join('')}</div></details>` : ''}
      <details><summary>What the agent observed · ${observations.length}</summary><div class="evidence">${observations.length ? observations.map(observation).join('') : '<p>No earlier observations were recorded.</p>'}</div></details>
      ${(action.speech || action.commitment || action.mandate || action.ranked_candidates?.length) ? `<details><summary>What the agent decided</summary><div class="evidence">${action.speech ? `<p>${esc(action.speech)}</p>` : ''}${proposal(action.commitment || action.mandate?.approved_commitment)}${action.ranked_candidates?.length ? `<p>Candidate ranking: ${action.ranked_candidates.map(esc).join(' → ')}</p>` : ''}</div></details>` : ''}</article>`;
  }

  async function openAgent(id,trigger) {
    const country = countryById(selectedCountry);
    const agent = country?.members.find(member => member.agent === id);
    if (!agent) return;
    selectedAgent = id;
    agentTrigger = trigger;
    const request = ++agentRequest;
    $('agent-country').textContent = `${country.name} · Round 1`;
    $('agent-title').textContent = roleName(agent);
    $('agent-details').innerHTML = '<p class="loading">Loading this agent’s recorded perspective…</p>';
    if (!$('agent-dialog').open) $('agent-dialog').showModal();
    try {
      const data = await read(`/api/round-one/agent?scope=researcher&agent=${encodeURIComponent(id)}`);
      if (request !== agentRequest || selectedAgent !== id) return;
      const detail = data.agent;
      const traces = data.traces.filter(event => event.round === 1 && event.actor === id && stages[event.payload.stage]);
      const scripted = detail.backend === 'offline-scripted-v1';
      $('agent-details').innerHTML = `<p class="agent-source">${scripted ? 'Scripted agent · recorded policy summaries' : 'Gemini agent · recorded decision summaries'}</p>
        <section class="detail-section"><h3>Priorities</h3><p>${esc(detail.personality)}</p></section>
        <section class="detail-section preparation"><h3>Private preparation</h3><p>${esc(detail.private_strategy || 'No preparation notes were recorded for this agent.')}</p>${detail.initial_preference ? `<details><summary>Starting proposal</summary>${proposal(detail.initial_preference)}</details>` : ''}</section>
        <section class="detail-section"><h3>Decisions in Round 1</h3>${traces.length ? traces.map(decision).join('') : empty('This run has no recorded decision summaries. The preparation notes above are the available record.')}</section>`;
    } catch (error) {
      if (request === agentRequest && selectedAgent === id) $('agent-details').innerHTML = empty(error.message);
    }
  }

  function closeAgent() {
    ++agentRequest;
    selectedAgent = null;
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
    } catch (error) { $('roster-total').textContent = 'Session unavailable'; showError(error); }
    finally { $('retry').disabled = false; }
  }

  document.addEventListener('click',event => {
    const button = event.target.closest('button[data-country], button[data-agent]');
    if (!button) return;
    if (button.dataset.country) openCountry(button.dataset.country);
    else if (button.dataset.agent) openAgent(button.dataset.agent,button);
  });
  $('back-to-countries').addEventListener('click',goBack);
  $('close-agent').addEventListener('click',closeAgent);
  $('agent-dialog').addEventListener('close',() => { ++agentRequest; selectedAgent = null; $('agent-details').innerHTML = ''; });
  $('retry').addEventListener('click',load);
  load();
})();
