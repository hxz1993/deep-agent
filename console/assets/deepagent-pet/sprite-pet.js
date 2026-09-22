// Generated sprite poses + non-periodic playback. No runtime dependencies.
const ROOT = new URL('./final/', import.meta.url);
const ROWS = { 'standing-blink': 6, 'sitting-blink': 6, daydream: 8, collapse: 8, sway: 8, walk: 8, 'sitting-look': 8, 'sitting-shake': 8, 'sitting-updown': 8, 'sitting-sleep': 8, 'standing-updown': 8, 'standing-rest': 8 };
const READY = Promise.all(Object.keys(ROWS).map(row => new Promise((resolve,reject) => {const img=new Image();img.onload=resolve;img.onerror=()=>reject(new Error('Cannot load pet sprite: '+row));img.src=new URL(row+'.webp?v=motions-5',ROOT).href;})));
const random = (min,max) => min + Math.random()*(max-min);
export class DeepAgentSpritePet extends HTMLElement {
  static observedAttributes = ['collapsed', 'paused'];
  constructor() {
    super();
    this.attachShadow({mode:'open'}).innerHTML = `<style>
      :host{display:inline-block;position:relative;width:38px;height:44px;flex:none;vertical-align:middle;contain:layout style}
      .viewport{display:block;width:100%;height:100%;overflow:hidden;pointer-events:none}
      @media(prefers-reduced-motion:reduce){.sprite{transition:none}}
    </style><svg class="viewport" viewBox="0 0 192 208" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><g class="travel"><svg width="192" height="208" overflow="hidden"><image class="sprite" width="1152" height="208"/></svg></g></svg>`;
    this.cell=this.shadowRoot.querySelector('.sprite');
    this.travel=this.shadowRoot.querySelector('.travel');
    this.transitionIndex=null;
  }
  get collapsed(){return this.hasAttribute('collapsed')}
  set collapsed(v){this.toggleAttribute('collapsed',Boolean(v))}
  connectedCallback(){
    this.controller?.abort();this.controller=new AbortController();
    this.reduced=matchMedia('(prefers-reduced-motion: reduce)');
    const opts={signal:this.controller.signal};
    this.reduced.addEventListener('change',()=>this.reset(),opts);
    document.addEventListener('visibilitychange',()=>this.reset(),opts);
    this.addEventListener('pointerenter',()=>{if(!this.busy && performance.now()-(this.hoverAt||0)>2000){this.hoverAt=performance.now();this.blink()}},opts);
    this.ready=false;this.reset();
    READY.then(()=>{if(this.isConnected){this.ready=true;this.dataset.ready='true';this.reset()}}).catch(error=>{this.dataset.error='assets';this.dispatchEvent(new CustomEvent('pet-error',{detail:error}))});
  }
  disconnectedCallback(){this.controller?.abort();clearTimeout(this.timer);this.token=(this.token||0)+1}
  attributeChangedCallback(name,oldValue,newValue){
    if(oldValue===newValue || !this.isConnected)return;
    if(name==='collapsed')this.changePose();else this.reset();
  }
  get inactive(){return !this.isConnected || !this.ready || document.hidden || this.hasAttribute('paused') || this.reduced?.matches}
  stop(){clearTimeout(this.timer);this.token=(this.token||0)+1;this.busy=false}
  draw(row,index,offset=0){
    this.travel.setAttribute('transform',`translate(${offset} 0)`);
    this.cell.setAttribute("href",new URL(row+'.webp?v=motions-5',ROOT).href);
    this.cell.setAttribute("width",String(ROWS[row]*192));
    this.cell.setAttribute("x",String(-index*192));
    this.restIndex=row==='standing-rest'?Math.min(index,4):null;
    this.dataset.action=row;this.dataset.frame=String(index);
  }
  neutral(){this.draw(this.collapsed?'sitting-blink':'standing-blink',0)}
  reset(){this.stop();this.transitionIndex=null;this.neutral();this.schedule()}
  schedule(){
    if(this.inactive)return;
    if(this.collapsed && !Number.isFinite(this.sleepDueAt))this.sleepDueAt=performance.now()+random(18000,28000);
    if(!this.collapsed && !Number.isFinite(this.restDueAt))this.restDueAt=performance.now()+random(25000,45000);
    // Long irregular quiet holds; action lengths do not determine next interval.
    this.timer=setTimeout(()=>{
      if(this.inactive)return;
      if(this.collapsed && performance.now()>=this.sleepDueAt){this.sleep();return}
      if(!this.collapsed && performance.now()>=this.restDueAt){this.rest();return}
      if(Math.random()<.46){this.blink();return}
      const options=(this.collapsed?['sitting-look','sitting-shake','sitting-updown']:['daydream','sway','walk','standing-updown']).filter(action=>action!==this.lastIdle);
      const action=options[Math.floor(Math.random()*options.length)] || 'sitting-look';
      this.lastIdle=action;
      if(action==='daydream')this.daydream();else this.perform(action);
    },random(2300,6800));
  }
  sequence(row,indices,durations,done,offsets=[]){
    const token=this.token;let cursor=0;this.busy=true;
    const tick=()=>{
      if(token!==this.token)return;
      if(this.inactive){this.reset();return}
      if(cursor===indices.length){this.busy=false;done();return}
      this.draw(row,indices[cursor],offsets[cursor] || 0);const delay=durations[cursor];cursor++;
      this.timer=setTimeout(tick,delay);
    };tick();
  }
  blink(){
    if(this.inactive || this.transitionIndex!==null)return;
    if(this.leaveRest(()=>this.blink()))return;
    this.stop();const row=this.collapsed?'sitting-blink':'standing-blink';
    const finish=()=>{this.neutral();this.schedule()};
    this.sequence(row,[1,2,3,4,5],[38,32,random(45,85),55,75],()=>{
      if(Math.random()<.18){this.timer=setTimeout(()=>this.sequence(row,[1,2,3,4,5],[28,28,42,52,70],finish),random(130,230))}
      else finish();
    });
  }
  daydream(){
    if(this.collapsed){this.perform('sitting-look');return}
    if(this.inactive || this.transitionIndex!==null)return;
    if(this.leaveRest(()=>this.daydream()))return;
    this.stop();
    // Let gaze lead, hold attention briefly, then recover; choose either side first.
    const leftFirst=Math.random()<.5;
    const frames=leftFirst?[0,1,2,2,3,0,4,5,5,6,7]:[0,4,5,5,6,0,1,2,2,3,7];
    this.sequence('daydream',frames,[100,100,120,random(300,700),130,220,100,120,random(250,550),140,120],()=>{this.neutral();this.schedule()});
  }
  sway(){this.perform(this.collapsed?'sitting-shake':'sway')}
  lookVertical(){this.perform(this.collapsed?'sitting-updown':'standing-updown')}
  sleep(){
    if(this.inactive || !this.collapsed || this.transitionIndex!==null)return;
    this.stop();
    this.sleepDueAt=performance.now()+random(55000,85000);
    const breath=random(850,1200);
    this.sequence('sitting-sleep',[0,1,2,3,4,5,4,5,4,5,4,5,6,7],
      [180,300,400,700,breath,breath,breath,breath,breath,breath,breath,breath,240,160],
      ()=>{this.neutral();this.schedule()});
  }
  walk(){if(!this.collapsed)this.perform('walk')}
  perform(action){
    if(this.inactive || this.transitionIndex!==null)return;
    if(this.collapsed !== action.startsWith('sitting-'))return;
    if(this.leaveRest(()=>this.perform(action)))return;
    this.stop();
    const finish=()=>{this.neutral();this.schedule()};
    if(action==='standing-updown'){
      this.sequence(action,[0,1,2,3,3,4,5,5,6,7],[120,180,220,500,random(1200,2200),200,220,random(400,700),180,150],finish);
    }else if(action==='sitting-shake'){
      this.sequence(action,[0,1,2,3,4,5,6,7],[100,150,120,150,130,180,220,140],finish);
    }else if(action==='sitting-updown'){
      this.sequence(action,[0,1,2,2,3,4,5,5,6,7],[100,160,200,random(400,700),200,160,200,random(400,700),180,150],finish);
    }else if(action==='walk'){
      // Two small steps out, curious pause, two steps home. Never mirror the D.
      const frames=[0,1,2,3,4,5,6,7,7,6,5,4,3,2,1,0];
      const sign=Math.random()<.5?-1:1;
      const offsets=[0,0,2,5,7,10,12,14,14,12,10,7,5,2,0,0].map(x=>x*sign);
      this.sequence(action,frames,[120,110,110,140,100,110,140,random(350,750),120,120,110,130,100,110,120,140],finish,offsets);
    }else{
      const frames=[0,1,2,2,3,4,5,5,6,7];
      this.sequence(action,frames,[110,140,160,random(220,500),160,140,160,random(200,480),170,160],finish);
    }
  }
  leaveRest(next){
    if(!(this.restIndex>0))return false;
    const index=this.restIndex;this.stop();
    const frames=Array.from({length:index+1},(_,i)=>index-i);
    this.sequence('standing-rest',frames,frames.map(()=>95),()=>{this.restIndex=null;next()});
    return true;
  }
  rest(){
    if(this.inactive || this.collapsed || this.transitionIndex!==null)return;
    if(this.restIndex>0)return;
    this.stop();this.restDueAt=performance.now()+random(65000,100000);
    const hold=random(1100,1700);
    this.sequence('standing-rest',[0,1,2,3,4,5,6,7,4,5,6,7,3,2,1,0],
      [100,130,160,180,hold,hold,110,hold,hold,hold,100,hold,180,160,130,100],
      ()=>{this.restIndex=null;this.neutral();this.schedule()});
  }
  changePose(){
    if(!this.inactive && this.leaveRest(()=>this.changePose()))return;
    this.restDueAt=this.collapsed?Infinity:performance.now()+random(25000,45000);
    this.sleepDueAt=this.collapsed?performance.now()+random(18000,28000):Infinity;
    const previous=this.transitionIndex;
    this.stop();
    if(this.inactive){this.reset();return}
    this.busy=true;
    this.transitionIndex=previous===null?(this.collapsed?0:7):previous;
    const token=this.token;
    const tick=()=>{
      if(token!==this.token)return;
      if(this.inactive){this.reset();return}
      const i=this.transitionIndex;this.draw('collapse',i);
      const target=this.collapsed?7:0;
      if(i===target){this.timer=setTimeout(()=>{this.transitionIndex=null;this.busy=false;this.neutral();this.schedule()},100);return}
      const durations=this.collapsed?[75,90,80,75,65,85,90,100]:[75,75,60,65,70,85,95,100];
      this.timer=setTimeout(()=>{this.transitionIndex+=this.collapsed?1:-1;tick()},durations[i]);
    };tick();
  }
}
if(!customElements.get('deepagent-sprite-pet'))customElements.define('deepagent-sprite-pet',DeepAgentSpritePet);
