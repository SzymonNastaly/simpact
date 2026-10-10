'use strict';
// Mock simulation content shared by the five landing concepts.
// Round 1 lines for US/EU/China/India are trimmed from runs/eight-gemini-20261010; the rest,
// and every Round 2 line, are placeholders written to show the layout.
window.DATA = (() => {
  const ROLES = {head_negotiator: 'Head negotiator', economic_minister: 'Economic minister', climate_adviser: 'Climate adviser'};
  const ROLE_SHORT = {head_negotiator: 'HEAD', economic_minister: 'ECON', climate_adviser: 'CLIMATE'};
  const ROLE_KEYS = Object.keys(ROLES);

  // rate = annual reduction %, peak = emissions peak year, fund = $bn/yr (+ contributes, − requests)
  const DELEGATIONS = [
    {id: 'usa', name: 'United States', code: 'US', accent: '#3c6fd6', rate: [1.0, 2.2], peak: 2030, fund: 25, status: 'Mandate agreed', votes: '2 of 3',
      thread: [
        ['head_negotiator', 'We cannot accept agreements that impose disproportionate burdens on the American economy while major competitors expand their emissions.'],
        ['economic_minister', 'Peaking by 2030 and cutting 2% a year from 2033 poses substantial risk to manufacturing and energy reliability.'],
        ['climate_adviser', 'Climate damage is already actively costing us. If we retreat to 2033 we forfeit our leverage.'],
        ['head_negotiator', 'A 2032 start at 2.2% — reciprocal, verifiable, and defensible at home.'],
      ]},
    {id: 'eu', name: 'European Union', code: 'EU', accent: '#2f55c4', rate: [3.5, 4.0], peak: 2026, fund: 80, status: 'Mandate agreed', votes: '3 of 3',
      thread: [
        ['head_negotiator', 'Our objective is credible European leadership while safeguarding competitiveness, energy security and cost of living.'],
        ['economic_minister', 'The trajectory must stay aligned with industry. I can support 4.0% from 2026 with $80 billion a year.'],
        ['climate_adviser', 'We must ground this in scientific reality. A 2026 peak at 4.0% firmly anchors our credibility.'],
      ]},
    {id: 'china', name: 'China', code: 'CN', accent: '#d8402f', rate: [2.0, 2.0], peak: 2030, fund: 0, status: 'Mandate agreed', votes: '3 of 3',
      thread: [
        ['head_negotiator', 'We must harmonise national development goals with constructive global climate leadership.'],
        ['economic_minister', 'A 2030 peak and a gradual 2.0% from 2035 gives our industrial base room to modernise.'],
        ['climate_adviser', 'Domestic vulnerability is real — heatwaves, drought, coastal risk. Our clean-tech lead is leverage.'],
      ]},
    {id: 'india', name: 'India', code: 'IN', accent: '#e8862a', rate: [1.7, 1.9], peak: 2040, fund: -60, status: 'Mandate agreed', votes: '2 of 3',
      thread: [
        ['head_negotiator', 'Our core mission is to protect rapid development and lift hundreds of millions out of poverty.'],
        ['economic_minister', 'Sustaining growth and affordable power for every household comes first.'],
        ['climate_adviser', 'India is exceptionally vulnerable. Finance for adaptation must be part of any deal.'],
      ]},
    {id: 'brazil', name: 'Brazil', code: 'BR', accent: '#1e9b57', rate: [2.0, 2.5], peak: 2030, fund: -40, status: 'Mandate agreed', votes: '3 of 3',
      thread: [
        ['head_negotiator', 'As host in Belém we bridge camps: strong forest targets, fair finance.'],
        ['economic_minister', 'Agribusiness competitiveness and non-debt finance are non-negotiable.'],
        ['climate_adviser', 'Amazon dieback is the tipping point. Eighty percent deforestation prevention, at least.'],
      ]},
    {id: 'saudi_arabia', name: 'Saudi Arabia', code: 'SA', accent: '#0f7a4a', rate: [0.5, 0.8], peak: 2040, fund: 0, status: 'Mandate agreed', votes: '2 of 3',
      thread: [
        ['head_negotiator', 'Energy security for the world depends on an orderly transition, not an abrupt one.'],
        ['economic_minister', 'Diversification under Vision 2030 needs time and revenue.'],
        ['climate_adviser', 'Carbon capture and afforestation let us move without stranding our economy.'],
      ]},
    {id: 'other_developed', name: 'Other Developed Nations', code: 'OD', accent: '#7a5cc4', rate: [2.6, 2.9], peak: 2026, fund: 45, status: 'Mandate agreed', votes: '3 of 3',
      thread: [
        ['head_negotiator', 'We can move faster than the US, but not alone — reciprocity from the large emitters.'],
        ['economic_minister', 'Carbon border measures keep our industry whole while we decarbonise.'],
        ['climate_adviser', 'Our coastlines and fire seasons already price the risk for us.'],
      ]},
    {id: 'other_developing', name: 'Other Developing Nations', code: 'OG', accent: '#c9a227', rate: [1.7, 1.9], peak: 2035, fund: -150, status: 'Mandate agreed', votes: '3 of 3',
      thread: [
        ['head_negotiator', 'Those who caused the crisis must finance the response. That is the starting point.'],
        ['economic_minister', 'Debt relief and grant finance — not loans — or our budgets break.'],
        ['climate_adviser', 'For small islands, 1.5 degrees is survival, not a slogan.'],
      ]},
  ];

  const BY_ID = Object.fromEntries(DELEGATIONS.map(d => [d.id, d]));
  const AGENTS = DELEGATIONS.flatMap(d => ROLE_KEYS.map(role => ({id: `${d.id}.${role}`, delegation: d.id, role, name: SIM.CAST[d.id][role]})));
  const agent = id => AGENTS.find(a => a.id === id);
  const photo = id => SIM.PHOTOS[agent(id).name];

  // Round 2: one-to-one meetings. t = start minute, d = length in minutes.
  const BILATERALS = [
    {a: 'usa.head_negotiator', b: 'china.head_negotiator', t: 0, d: 6, outcome: 'offer', topic: 'Reciprocal reduction start',
      lines: [['a', 'Move your start to 2032 and we match 2.4%.'], ['b', 'Only if the finance gap is acknowledged in writing.'], ['a', 'Put it on paper. Verifiable on both sides.']]},
    {a: 'eu.head_negotiator', b: 'india.head_negotiator', t: 1, d: 5, outcome: 'agreed', topic: 'Adaptation finance for faster peak',
      lines: [['a', 'Eighty-two billion if India brings its peak forward.'], ['b', 'Bring the floor to eighty-two and we move to 2.1%.'], ['a', 'Agreed. Let our ministers draft it.']]},
    {a: 'brazil.climate_adviser', b: 'other_developed.climate_adviser', t: 2, d: 4, outcome: 'agreed', topic: 'Forest protection fund',
      lines: [['a', 'Fund the forest and we hold 80% deforestation prevention.'], ['b', 'We can pledge ten billion toward it this round.']]},
    {a: 'saudi_arabia.economic_minister', b: 'usa.economic_minister', t: 2, d: 3, outcome: 'stalled', topic: 'Transition timelines',
      lines: [['a', 'An abrupt phase-down risks price shocks for everyone.'], ['b', 'Then we keep the timeline open. No deal today.']]},
    {a: 'other_developing.head_negotiator', b: 'eu.economic_minister', t: 3, d: 5, outcome: 'offer', topic: 'Grant-based finance',
      lines: [['a', 'Loans deepen the crisis. We need grants.'], ['b', 'A grant share of forty percent is on the table.']]},
    {a: 'china.climate_adviser', b: 'india.climate_adviser', t: 4, d: 4, outcome: 'agreed', topic: 'Joint solar deployment',
      lines: [['a', 'Our manufacturing, your deployment scale.'], ['b', 'A joint line in our announcements, then.']]},
    {a: 'usa.climate_adviser', b: 'eu.climate_adviser', t: 5, d: 3, outcome: 'agreed', topic: 'Methane coalition',
      lines: [['a', 'A methane pledge is quick and measurable.'], ['b', 'We co-sponsor it.']]},
    {a: 'brazil.head_negotiator', b: 'china.economic_minister', t: 6, d: 4, outcome: 'offer', topic: 'Green trade corridor',
      lines: [['a', 'Deforestation-free supply chains, priced in.'], ['b', 'Our buyers need certainty — a pilot first.']]},
    {a: 'other_developed.head_negotiator', b: 'saudi_arabia.head_negotiator', t: 7, d: 3, outcome: 'stalled', topic: 'Carbon border measures',
      lines: [['a', 'Border adjustments are coming either way.'], ['b', 'Then we will see you at the WTO.']]},
    {a: 'india.economic_minister', b: 'usa.head_negotiator', t: 8, d: 4, outcome: 'offer', topic: 'Technology transfer',
      lines: [['a', 'Licences for clean tech lower our costs and your targets.'], ['b', 'We can open a bilateral fund — conditions apply.']]},
    {a: 'other_developing.climate_adviser', b: 'brazil.climate_adviser', t: 9, d: 3, outcome: 'agreed', topic: 'Loss and damage bloc',
      lines: [['a', 'One voice on loss and damage.'], ['b', 'Brazil stands with you.']]},
    {a: 'eu.head_negotiator', b: 'china.head_negotiator', t: 10, d: 5, outcome: 'offer', topic: 'Peak year',
      lines: [['a', 'A 2028 peak would change the whole room.'], ['b', 'And a 2028 peak needs a partner who pays.']]},
  ];

  const OUTCOMES = {agreed: 'Agreement', offer: 'Offer on table', stalled: 'Stalled'};
  const GLOBAL = {warming: [2.15, 2.10], finance: {pledged: 202, requested: 375}, bilaterals: BILATERALS.length, messages: 72, agreements: 8};

  return {ROLES, ROLE_SHORT, ROLE_KEYS, DELEGATIONS, BY_ID, AGENTS, agent, photo, BILATERALS, OUTCOMES, GLOBAL};
})();
