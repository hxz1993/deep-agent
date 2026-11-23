import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
const source=await readFile(new URL('../../../console/assets/deepagent-pet/sprite-pet.js',import.meta.url),'utf8');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
 const images=[],registered=new Map(),attributes=new Map(),drawn=[],frames=new Map();
 let frameId=0;
 const painter={clearRect(){},save(){},restore(){},drawImage(...args){drawn.push(args);}};
 class Element {
  dataset={};isConnected=true;
  attachShadow(){return this.shadowRoot={innerHTML:'',querySelector(){return {getContext:()=>painter};}};}
  hasAttribute(key){return attributes.has(key);}addEventListener(){}dispatchEvent(){}
 }
 const context=vm.createContext({HTMLElement:Element,Image:class {constructor(){images.push(this);}},URL,AbortController,CustomEvent:class{},matchMedia:()=>({matches:false,addEventListener(){}}),document:{hidden:false,addEventListener(){}},performance:{now:()=>100},setTimeout(){return 1;},clearTimeout(){},requestAnimationFrame(cb){frames.set(++frameId,cb);return frameId;},cancelAnimationFrame(id){frames.delete(id);},customElements:{get:name=>registered.get(name),define:(name,value)=>registered.set(name,value)}});
 vm.runInContext(source.replace("import.meta.url","'http://console.test/console/assets/deepagent-pet/sprite-pet.js'").replace('export class','class'),context);
 return {images,drawn,attributes,frames,paint(time){const callbacks=[...frames.values()];frames.clear();callbacks.forEach(cb=>cb(time));},Pet:registered.get('deepagent-sprite-pet')};
}
test('pet loads only the initial pose, then the requested animation',async()=>{
 const h=harness();assert.equal(h.images.length,0);const pet=new h.Pet();pet.connectedCallback();assert.equal(h.images.length,1);assert.match(h.images[0].src,/standing-blink/);h.images[0].onload();await turn();assert.equal(pet.dataset.ready,'true');pet.perform('walk');assert.equal(h.images.length,2);assert.match(h.images[1].src,/walk/);
 assert.equal(h.drawn.at(-1)[0],h.images[0],'keep displaying the last frame while the next sprite loads');
 h.images[1].onload();await turn();h.paint(136);
 assert.deepEqual(h.drawn.slice(-2).map(([image])=>image),h.images,'blend the old and new sprites on the same canvas');
 h.paint(180);assert.equal(h.drawn.at(-1)[0],h.images[1]);assert.equal(h.frames.size,0);
 pet.neutral();assert.equal(h.frames.size,1);pet.disconnectedCallback();assert.equal(h.frames.size,0);
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
