// Dependency-free DOM/render smoke test. Browser visual QA is a separate check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const rootPath = path.resolve(__dirname, '..');
const fixture = JSON.parse(execFileSync(path.join(rootPath,'.venv/bin/python'), ['-c',
  'import json,tempfile; from pathlib import Path; from climate_sim.models import SimulationConfig; from climate_sim.simulation import Simulation; from climate_sim.lab import Lab; tasktemp=tempfile.TemporaryDirectory(); p=Path(tasktemp.name)/"run"; Simulation(SimulationConfig(seed=42),output=p).run(); lab=Lab(p); print(json.dumps({"researcher":lab.replay("researcher"),"public":lab.replay("public"),"china.climate_adviser":lab.replay("china.climate_adviser")})); tasktemp.cleanup()'
], {cwd:rootPath,encoding:'utf8'}));
const html = fs.readFileSync(path.join(rootPath,'frontend/index.html'),'utf8');
const script = fs.readFileSync(path.join(rootPath,'frontend/app.js'),'utf8');
const elements = new Map();
const documentEvents = {};
let invitees = [];
class Element {
  constructor(id) {this.id=id;this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.dataset={};this.handlers={};this.attributes={};this.classes=new Set();this.classList={toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c)};this._html='';}
  get innerHTML() {return this._html;}
  set innerHTML(value) {this._html=value;if(this.id==='invitees') invitees=[...value.matchAll(/name="invitee" value="([^"]+)"/g)].map(m=>({value:m[1],checked:false}));}
  setAttribute(k,v) {this.attributes[k]=v;}
  addEventListener(event,handler) {this.handlers[event]=handler;}
  showModal() {this.open=true;}
  close() {this.open=false;}
}
for(const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1],new Element(match[1]));
const tabs=['delegation','floor'].map(workspace=>{const b=new Element();b.dataset={workspace};return b;});
const posts=[];
const hub={tick:0,acknowledgement_ticks:5,human_role:null,participants:fixture.researcher.roster.map(a=>({id:a.agent,delegation:a.delegation,role:a.role,model:a.backend,controller:'agent'})),meetings:[]};
const document={querySelector(selector){assert.ok(selector.startsWith('#'));const found=elements.get(selector.slice(1));assert.ok(found,`Missing ${selector}`);return found;},querySelectorAll(selector){if(selector==='.workspace-tabs button')return tabs;if(selector==='#invitees input:checked')return invitees.filter(i=>i.checked);throw Error(`Unexpected ${selector}`);},addEventListener(event,handler){documentEvents[event]=handler;}};
async function fetch(url,options){
  if(options){const body=JSON.parse(options.body);posts.push(body);if(body.operation==='claim'){hub.human_role=body.actor;hub.participants.find(p=>p.id===body.actor).controller='human';}if(body.operation==='release'){hub.participants.find(p=>p.id===hub.human_role).controller='agent';hub.human_role=null;}if(body.operation==='advance')hub.tick+=1;return{ok:true,json:async()=>({human_role:hub.human_role,tick:hub.tick})};}
  const scope=hub.human_role || new URL(url,'http://localhost').searchParams.get('scope') || 'public';
  if(url.startsWith('/api/replay'))return{ok:true,json:async()=>({...structuredClone(fixture[scope] || fixture.public),scope,human_role:hub.human_role})};
  return{ok:true,json:async()=>structuredClone(hub)};
}
const context=vm.createContext({document,fetch,URL,console});
vm.runInContext(script,context);
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};
const el=id=>elements.get(id);
const click=async dataset=>{documentEvents.click({target:{closest:()=>({dataset,disabled:false})}});await settle();};
const change=async(id,value)=>{el(id).value=value;await el(id).handlers.change({target:el(id)});await settle();};

(async()=>{
  await settle();
  assert.match(el('workspace-title').textContent,/China/);
  assert.equal((el('countries').innerHTML.match(/data-country=/g)||[]).length,6);
  assert.equal((el('agent-cards').innerHTML.match(/data-agent=/g)||[]).length,3);
  assert.equal((el('internal-transcript').innerHTML.match(/class="speech /g)||[]).length,6);
  assert.match(el('internal-transcript').innerHTML,/2\.07%/);
  assert.match(el('agent-perspective').innerHTML,/Individual-private preparation strategy/);
  assert.match(el('alignment').innerHTML,/0\.024/);
  assert.equal(posts.length,0,'A replay must not trigger agent actions or automatic ACKs');

  await click({round:'2'});
  assert.match(el('discussion-access').textContent,/Round 2/);
  assert.match(el('agent-perspective').innerHTML,/Starting shared position from prior round/);
  await click({country:'eu'});
  assert.match(el('workspace-title').textContent,/European Union/);
  assert.match(el('mandate').innerHTML,/Team priorities/);

  await change('viewer','public');
  assert.match(el('internal-transcript').innerHTML,/Private delegation room/);
  assert.match(el('mandate').innerHTML,/National mandate is delegation-private/);
  assert.doesNotMatch(el('mandate').innerHTML,/Team priorities/,'Old private mandate must clear on scope change');
  assert.doesNotMatch(el('agent-perspective').innerHTML,/Prioritize /);
  assert.equal(el('claim-role').disabled,true);

  await change('viewer','china.climate_adviser');
  assert.match(el('workspace-title').textContent,/China/);
  assert.match(el('agent-perspective').innerHTML,/Individual-private preparation strategy/);
  await click({agent:'china.economic_minister'});
  assert.match(el('agent-perspective').innerHTML,/restricted to its owner/);
  await click({workspace:'floor'});
  assert.equal((el('floor-roster').innerHTML.match(/data-acting=/g)||[]).length,18);
  assert.equal(el('acting-member').disabled,true);
  assert.match(el('clock').textContent,/5-tick ACK window/);

  await change('viewer','researcher');
  await el('open-request').handlers.click();
  assert.equal(el('meeting-dialog').open,true);
  assert.equal(invitees.length,17);
  assert.ok(invitees.every(i=>i.value!==el('request-sender').value));
  invitees.find(i=>i.value==='eu.economic_minister').checked=true;
  invitees.find(i=>i.value==='india.climate_adviser').checked=true;
  el('request-topic').value='Cross-role coalition';
  await el('request-form').handlers.submit({preventDefault(){}});await settle();
  const request=posts.find(p=>p.operation==='request');
  assert.deepEqual(request.invitees,['eu.economic_minister','india.climate_adviser']);
  assert.equal(el('meeting-dialog').open,false);

  await change('viewer','china.climate_adviser');
  await el('claim-role').handlers.click();await settle();
  assert.equal(el('viewer').disabled,true);
  assert.match(el('mode-notice').textContent,/You control China/);
  assert.match(el('floor-roster').innerHTML,/HUMAN/);
  await el('claim-role').handlers.click();await settle();
  assert.equal(el('viewer').disabled,false);

  console.log('Agent lab UI passed: roster, rounds, discussions, private-scope clearing, member types, invitations and human role controls.');
})().catch(error=>{console.error(error);process.exitCode=1;});
