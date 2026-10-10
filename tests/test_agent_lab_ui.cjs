// Interaction checks against an actual run; visual browser QA is separate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname,'..');
const fixture = JSON.parse(execFileSync(path.join(root,'.venv/bin/python'),['-c',
  'import json,tempfile; from pathlib import Path; from climate_sim.models import SimulationConfig; from climate_sim.simulation import Simulation; from climate_sim.lab import Lab; tmp=tempfile.TemporaryDirectory(); p=Path(tmp.name)/"run"; Simulation(SimulationConfig(seed=42,team_size=5),output=p).run(); lab=Lab(p); view=lab.round_one("researcher"); print(json.dumps({"view":view,"details":{a["agent"]:lab.round_one_agent(a["agent"],"researcher") for c in view["countries"] for a in c["members"]}})); tmp.cleanup()'
],{cwd:root,encoding:'utf8',maxBuffer:16*1024*1024}));
const html = fs.readFileSync(path.join(root,'frontend/index.html'),'utf8');
const script = fs.readFileSync(path.join(root,'frontend/app.js'),'utf8');
const elements = new Map();
const handlers = {};
let focused = null;
class Element {
  constructor(id){this.id=id;this.innerHTML='';this.textContent='';this.hidden=false;this.open=false;this.disabled=false;this.dataset={};this.handlers={};this.isConnected=true;}
  addEventListener(kind,fn){this.handlers[kind]=fn;}
  focus(){focused=this.id;}
  showModal(){this.open=true;}
  close(){this.open=false;this.handlers.close?.();}
}
for(const match of html.matchAll(/\bid="([^"]+)"/g))elements.set(match[1],new Element(match[1]));
elements.get('discussion-view').hidden=true;
const requests=[];
let failNext=false;
let delayedAgent=null;
let resolveDelayed;
async function fetch(url,options){
  assert.equal(options,undefined,'The viewer must never trigger simulation actions');
  requests.push(url);
  if(failNext){failNext=false;return{ok:false};}
  const parsed=new URL(url,'http://localhost');
  assert.equal(parsed.searchParams.get('scope'),'researcher');
  let data;
  if(parsed.pathname==='/api/round-one')data=fixture.view;
  else{
    assert.equal(parsed.pathname,'/api/round-one/agent');
    const id=parsed.searchParams.get('agent');
    data=fixture.details[id];
    assert.ok(data);
    if(id===delayedAgent)await new Promise(resolve=>{resolveDelayed=resolve;});
  }
  return{ok:true,json:async()=>structuredClone(data)};
}
const document={getElementById(id){assert.ok(elements.has(id),`Missing #${id}`);return elements.get(id);},
  querySelector(selector){assert.match(selector,/^\[data-country=/);return new Element('country-card');},
  addEventListener(kind,fn){handlers[kind]=fn;}};
const context=vm.createContext({document,window:{scrollTo(){}},fetch,URL,console,Set,encodeURIComponent});
vm.runInContext(script,context);
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};
const el=id=>elements.get(id);
async function click(dataset){const target=new Element('trigger');target.dataset=dataset;handlers.click({target:{closest:()=>target}});await settle();}

(async()=>{
  await settle();
  assert.equal((el('countries').innerHTML.match(/data-country=/g)||[]).length,6);
  assert.match(el('roster-total').textContent,/6 delegations · 30 agents/);
  assert.equal(el('discussion-view').hidden,true);
  assert.match(el('run-caption').textContent,/Recorded scripted session/);
  assert.doesNotMatch(html,/<aside|sidebar|round-switch|workspace-tabs|meeting-dialog/);

  await click({country:'china'});
  assert.equal(el('country-view').hidden,true);
  assert.equal(el('discussion-view').hidden,false);
  assert.match(el('country-title').textContent,/China/);
  assert.equal((el('agents').innerHTML.match(/data-agent=/g)||[]).length,5);
  assert.equal((el('discussion').innerHTML.match(/class="message"/g)||[]).length,10);
  const country=fixture.view.countries.find(c=>c.id==='china');
  assert.ok(el('discussion').innerHTML.includes(country.discussion[0].payload.speech));
  assert.equal(focused,'country-title');

  await click({agent:'china.climate_adviser'});
  assert.equal(el('agent-dialog').open,true);
  assert.equal(el('agent-title').textContent,'Climate adviser 1');
  assert.match(el('agent-details').innerHTML,/Private preparation/);
  assert.match(el('agent-details').innerHTML,/Decisions in Round 1/);
  assert.match(el('agent-details').innerHTML,/What the agent observed/);
  assert.ok(el('agent-details').innerHTML.includes(fixture.details['china.climate_adviser'].agent.private_strategy));
  assert.equal((el('agent-details').innerHTML.match(/class="decision"/g)||[]).length,fixture.details['china.climate_adviser'].traces.length);
  el('close-agent').handlers.click();
  assert.equal(el('agent-dialog').open,false);
  assert.equal(el('agent-details').innerHTML,'');

  // A late reply for a previously selected member cannot replace the current member's notes.
  delayedAgent='china.climate_adviser';
  await click({agent:delayedAgent});
  await click({agent:'china.economic_minister'});
  const current=el('agent-details').innerHTML;
  resolveDelayed();await settle();
  assert.equal(el('agent-title').textContent,'Economic minister 1');
  assert.equal(el('agent-details').innerHTML,current);
  el('close-agent').handlers.click();
  delayedAgent=null;

  // Actual transcript strings must render as text, including HTML-like model output.
  const old=country.discussion[0].payload.speech;
  country.discussion[0].payload.speech='<script>bad()</script>';
  await el('retry').handlers.click();await settle();
  await click({country:'china'});
  assert.match(el('discussion').innerHTML,/&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(el('discussion').innerHTML,/<script>/);
  country.discussion[0].payload.speech=old;

  el('back-to-countries').handlers.click();
  assert.equal(el('country-view').hidden,false);
  assert.equal(el('discussion-view').hidden,true);
  await click({country:'eu'});
  assert.match(el('country-title').textContent,/European Union/);
  assert.ok(requests.every(url=>url.startsWith('/api/round-one')));

  failNext=true;await el('retry').handlers.click();await settle();
  assert.equal(el('error').hidden,false);
  await el('retry').handlers.click();await settle();
  assert.equal(el('error').hidden,true);
  console.log('Round 1 UI passed: actual rosters, country navigation, discussion, agent records, stale-request protection and escaped dialogue.');
})().catch(error=>{console.error(error);process.exitCode=1;});
