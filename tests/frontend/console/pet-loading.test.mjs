import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
const source=await readFile(new URL('../../../console/assets/deepagent-pet/sprite-pet.js',import.meta.url),'utf8');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
 const images=[],registered=new Map(),attributes=new Map(),drawn=[];
 class Element {
  dataset={};isConnected=true;
  attachShadow(){return this.shadowRoot={innerHTML:'',querySelector(){return {setAttribute(...args){drawn.push(args);}};}};}
  hasAttribute(key){return attributes.has(key);}addEventListener(){}dispatchEvent(){}
 }
 const context=vm.createContext({HTMLElement:Element,Image:class {constructor(){images.push(this);}},URL,AbortController,CustomEvent:class{},matchMedia:()=>({matches:false,addEventListener(){}}),document:{hidden:false,addEventListener(){}},performance:{now:()=>100},setTimeout(){return 1;},clearTimeout(){},customElements:{get:name=>registered.get(name),define:(name,value)=>registered.set(name,value)}});
 vm.runInContext(source.replace("import.meta.url","'http://console.test/console/assets/deepagent-pet/sprite-pet.js'").replace('export class','class'),context);
 return {images,drawn,attributes,Pet:registered.get('deepagent-sprite-pet')};
}
test('pet loads only the initial pose, then the requested animation',async()=>{
 const h=harness();assert.equal(h.images.length,0);const pet=new h.Pet();pet.connectedCallback();assert.equal(h.images.length,1);assert.match(h.images[0].src,/standing-blink/);h.images[0].onload();await turn();assert.equal(pet.dataset.ready,'true');pet.perform('walk');assert.equal(h.images.length,2);assert.match(h.images[1].src,/walk/);h.images[1].onload();await turn();assert.ok(h.drawn.some(x=>x[0]==='href'&&x[1].includes('walk')));pet.disconnectedCallback();
});
test('animation load failure restores the existing fallback and detached callbacks cannot restart it',async()=>{
 const h=harness();const pet=new h.Pet();pet.connectedCallback();h.images[0].onload();await turn();pet.perform('walk');h.images[1].onerror();await turn();assert.equal(pet.dataset.error,'assets');assert.equal(pet.dataset.ready,undefined);pet.disconnectedCallback();
 const second=harness();const detached=new second.Pet();detached.connectedCallback();detached.isConnected=false;detached.disconnectedCallback();second.images[0].onload();await turn();assert.equal(detached.dataset.ready,undefined);
});

test('switching the neutral pose lazily checks its asset and failure keeps the fallback visible',async()=>{
 const h=harness();const pet=new h.Pet();pet.connectedCallback();h.images[0].onload();await turn();h.attributes.set('collapsed',true);pet.reset();assert.equal(h.images.length,2);assert.match(h.images[1].src,/sitting-blink/);h.images[1].onerror();await turn();assert.equal(pet.dataset.ready,undefined);assert.equal(pet.dataset.error,'assets');pet.disconnectedCallback();
});

test('collapsing during initial loading waits for the current pose before hiding the fallback',async()=>{
 const h=harness();const pet=new h.Pet();pet.connectedCallback();h.attributes.set('collapsed',true);pet.reset();h.images[0].onload();await turn();assert.equal(h.images.length,2);assert.equal(pet.dataset.ready,undefined);h.images[1].onload();await turn();assert.equal(pet.dataset.ready,'true');pet.disconnectedCallback();
});
