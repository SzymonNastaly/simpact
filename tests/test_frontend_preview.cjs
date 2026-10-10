// Dependency-free interaction smoke test; not a substitute for visual browser QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../previews/climate-room.html'), 'utf8');
const data = html.match(/<script type="application\/json" id="room-public-data">([\s\S]*?)<\/script>/)[1];
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
let width = 680;
const element = () => ({
  innerHTML: '', textContent: '', hidden: false, attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
  getBoundingClientRect() { return {width}; },
});
for (const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], element());
elements.get('room-public-data').textContent = data;
const roundButtons = ['1', '2'].map(round => ({...element(), dataset: {round}}));
const viewButtons = ['teams', 'summit', 'commitments'].map(view => ({...element(), dataset: {view}}));
const handlers = {};
const root = elements.get('simpact-climate-room');
root.querySelector = selector => {
  assert.ok(selector.startsWith('#'), `Unexpected selector: ${selector}`);
  const found = elements.get(selector.slice(1));
  assert.ok(found, `Missing element: ${selector}`);
  return found;
};
root.querySelectorAll = selector => {
  if (selector === '[data-round]') return roundButtons;
  if (selector === '[data-view]') return viewButtons;
  throw new Error(`Unexpected selector: ${selector}`);
};
root.addEventListener = (event, handler) => { handlers[event] = handler; };
root.contains = () => true;
const context = vm.createContext({document: {getElementById: id => elements.get(id)}});
vm.runInContext(script, context);
const content = id => elements.get(id).innerHTML;
const click = dataset => handlers.click({target: {closest: () => ({dataset})}});

assert.match(content('room-warming'), /2\.10/);
assert.match(content('room-fund'), /\$202/);
assert.match(content('room-requests'), /\$375/);
assert.equal(elements.get('room-team-title').textContent, 'European Union');
assert.match(content('room-policy'), /3\.96%/);
assert.equal((content('room-countries').match(/data-country=/g) || []).length, 6);
assert.equal(elements.get('room-view-summit').hidden, true);

click({round: '1'});
assert.match(content('room-warming'), /2\.15/);
assert.match(content('room-fund'), /\$159/);
assert.match(content('room-policy'), /3\.51%/);
assert.equal(elements.get('room-final-legend').hidden, true);
assert.equal(roundButtons[0].attributes['aria-pressed'], 'true');

click({country: 'india'});
assert.equal(elements.get('room-team-title').textContent, 'India');
assert.match(content('room-policy'), /1\.67%/);
assert.match(content('room-policy'), /−\$106B/);

click({view: 'summit'});
assert.equal(elements.get('room-view-teams').hidden, true);
assert.equal(elements.get('room-view-summit').hidden, false);
assert.equal((content('room-messages').match(/room-message-content/g) || []).length, 6);
assert.match(content('room-messages'), /India announces emissions peak 2040/);

click({round: '2'});
assert.match(content('room-messages'), /India submits its unconditional final commitment/);
assert.match(content('room-policy'), /1\.92%/);
click({view: 'commitments'});
assert.equal(elements.get('room-view-commitments').hidden, false);
assert.equal((content('room-commitment-rows').match(/<tr /g) || []).length, 6);
assert.match(content('room-commitment-rows'), /-107/);

// Exercise chart geometry at representative narrow and desktop content widths.
for (width of [260, 312, 688, 976]) {
  click({view: 'teams'});
  assert.equal(elements.get('room-trajectory').attributes.viewBox, `0 0 ${width} 172`);
  assert.doesNotMatch(content('room-trajectory'), /NaN|undefined|Infinity/);
}
assert.doesNotMatch(html, /fetch\(|XMLHttpRequest|WebSocket|GEMINI_API_KEY/);
console.log('Preview smoke test passed: rounds, teams, views, public data and chart geometry.');
