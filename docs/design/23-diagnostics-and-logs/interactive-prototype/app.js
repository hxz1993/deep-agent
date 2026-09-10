import {sessions,logs,agents,users,statuses,NOW,filterSessions,filterLogs,aggregate,questionRows,time,escapeHTML as e} from './data.js';
import {mountReadonlySession} from './readonly-session.js';

const main=document.querySelector('#prototypeMain');
const state={session:{days:'7',agent:'',user:'',channel:'',status:'',q:'',wait:false},log:{hours:'24',source:'',level:'',q:'',sessionId:'',runId:''},
  page:1,logLimit:70,selectedLog:logs.find(l=>l.level==='ERROR')?.id,drawer:true,idFilters:false,lastSessionView:'overview',detailOrigin:null};
let disposeDetail=null,toastTimer;
const icon=name=>`<i data-lucide="${name}" aria-hidden="true"></i>`;
const pill=(label,type)=>`<span class="pill ${e(type)}">${e(label)}</span>`;
const statusPill=s=>pill(statuses[s],s);
const button=(label,action,cls='',attributes='')=>`<button type="button" class="pbtn ${cls}" data-action="${action}" ${attributes}>${label}</button>`;
const textButton=(label,action,attributes='')=>`<button type="button" class="text-link" data-action="${action}" ${attributes}>${label}</button>`;
const sample='<span class="demo-label">示例数据</span>';
const logTime=value=>new Date(value).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).slice(5);
function icons(){window.lucide?.createIcons();}
function toast(message){clearTimeout(toastTimer);const el=document.querySelector('#prototypeToast');el.textContent=message;el.hidden=false;toastTimer=setTimeout(()=>el.hidden=true,2600);}
function route(path){if(location.hash===`#${path}`)render();else location.hash=path;}
function getRoute(){const parts=location.hash.replace(/^#\/?/,'').split('/');return {section:parts[0]||'sessions',view:parts[1]||'overview',id:parts[2]};}
function select(name,label,value,options,scope='session'){
  const list=[['',label],...options.map(o=>Array.isArray(o)?o:[o,o])];
  const selected=list.find(([v])=>v===value)?.[1]||label;
  return `<details class="proto-select"><summary aria-label="${e(label)}">${e(selected)}${icon('chevron-down')}</summary><div class="proto-options" role="group" aria-label="${e(label)}选项">${list.map(([v,l])=>`<button type="button" class="${v===value?'selected':''}" data-filter="${name}" data-scope="${scope}" data-value="${e(v)}">${e(l)}${v===value?icon('check'):''}</button>`).join('')}</div></details>`;
}
function empty(text='没有匹配的会话',body='换个筛选条件试试。') {return `<div class="empty-result">${icon('search-x')}<strong>${text}</strong><p>${body}</p></div>`;}
function sessionFilters(){const f=state.session;return `<div class="filter-band">${[
  ['Agent',select('agent','全部 Agent',f.agent,agents)],
  ['时间范围',select('days','全部时间',f.days,[['1','最近 24 小时'],['7','最近 7 天'],['30','最近 30 天']])],
  ['用户',select('user','全部用户',f.user,users)],
  ['渠道',select('channel','全部渠道',f.channel,['企微','API'])],
  ...(getRoute().view==='list'?[['执行状态',select('status','全部状态',f.status,Object.entries(statuses))]]:[])
].map(([label,field])=>`<div class="filter-field"><label>${label}</label>${field}</div>`).join('')}${button(icon('rotate-cw')+'刷新','refresh')}</div>`;}
function chips(scope){const f=state[scope],labels=scope==='session'?{agent:'Agent',user:'用户',channel:'渠道',status:'状态',q:'名称 / ID',wait:'等待超 30 秒'}:{source:'来源',level:'级别',sessionId:'会话',runId:'Run',q:'关键词'};
  const entries=Object.entries(labels).filter(([k])=>f[k]);
  return entries.map(([key,label])=>`<button type="button" data-clear="${key}" data-scope="${scope}" title="清除 ${e(label)}">${e(key==='wait'?label:key==='sessionId'?`会话 ${f[key].slice(0,8)}…${f[key].slice(-4)}`:key==='status'?statuses[f[key]]:f[key])}${icon('x')}</button>`).join('');}
function sessionHeader(view){return `<header class="page-header"><h1>会话</h1><div class="header-right">${sample}<div class="segmented" aria-label="会话页面视图">${['overview','list'].map((v,i)=>`<button type="button" class="${view===v?'active':''}" data-action="session-view" data-view="${v}" aria-pressed="${view===v}">${i?'列表':'概览'}</button>`).join('')}</div></div></header>`;}
function chart(items){
  const bins=Array.from({length:7},(_,i)=>{const start=Date.parse('2026-09-03T00:00:00+08:00')+i*86400000;return questionRows(items).filter(q=>Date.parse(q.at)>=start&&Date.parse(q.at)<start+86400000).length;});
  const max=Math.max(4,...bins),top=Math.ceil(max/4)*4;
  const points=bins.map((n,i)=>[42+i*91,155-n/top*126]);
  return `<div class="chart-wrap"><svg viewBox="0 0 620 190" role="img" aria-label="最近七天提问趋势：${bins.join('、')}">${Array.from({length:5},(_,i)=>{const y=155-i*31.5;return `<line x1="42" y1="${y}" x2="588" y2="${y}" class="chart-grid"/><text x="28" y="${y+3}" text-anchor="end" class="chart-label">${Math.round(top*i/4)}</text>`;}).join('')}
    <path d="M42,155 L${points.map(p=>p.join(',')).join(' L')} L588,155Z" fill="#f1eeff"/>
    <path d="M${points.map(p=>p.join(',')).join(' L')}" fill="none" stroke="#8a7af6" stroke-width="2"/>
    ${points.map(([x,y],i)=>`<circle cx="${x}" cy="${y}" r="3.5" fill="#8a7af6" stroke="white" stroke-width="1.5"/><text x="${x}" y="${y-11}" text-anchor="middle" class="chart-value">${bins[i]}</text><text x="${x}" y="179" text-anchor="middle" class="chart-label">9/${i+3}</text>`).join('')}</svg></div>`;
}
function overview(){const items=filterSessions(state.session),n=aggregate(items),qs=questionRows(items),max=Math.max(1,...agents.map(a=>qs.filter(q=>q.session.agent===a).length));
  return `${sessionHeader('overview')}<div class="session-content">${sessionFilters()}${chips('session')?`<div class="filter-chips" style="margin-bottom:13px">${chips('session')}${textButton('重置','reset-sessions')}</div>`:''}
    <div class="stats-strip">${[
      ['messages-square','会话数',n.sessions,'','all'],['message-circle','提问数',n.questions,'','questions'],['users-round','活跃用户',n.users,'green','users'],['circle-alert','执行失败',n.failed,'red','failed']
    ].map(([ic,label,value,color,kind])=>`<button type="button" class="stat" data-action="stat" data-kind="${kind}"><span class="stat-icon ${color}">${icon(ic)}</span><span><small>${label}</small><strong>${value.toLocaleString()}</strong></span>${icon('chevron-right')}</button>`).join('')}</div>
    <div class="dashboard-grid">
      <section class="panel"><div class="panel-head"><h2>提问趋势 <small>最近 7 天</small></h2><small>提问数</small></div>${chart(items)}</section>
      <section class="panel"><div class="panel-head"><h2>Agent 分布 <small>提问数</small></h2></div><div class="agent-bars">${agents.map(a=>{const count=qs.filter(q=>q.session.agent===a).length;return `<button type="button" class="bar-row" data-action="agent-drill" data-agent="${e(a)}"><span>${e(a)}</span><span class="bar-track"><i style="width:${count/max*100}%"></i></span><b>${count}</b></button>`;}).join('')}</div></section>
      <section class="panel"><div class="panel-head"><h2>最近提问</h2>${textButton('查看全部 '+icon('chevron-right'),'show-questions')}</div><div class="question-table"><div class="question-row thead"><span>问题</span><span>用户</span><span>Agent</span><span>时间</span><span></span></div>${qs.slice(0,7).map(q=>`<button type="button" class="question-row" data-session="${q.session.id}"><strong>${e(q.text)}</strong><span>${e(q.session.user)}</span><span>${e(q.session.agent)}</span><span>${time(q.at,true)}</span>${icon('chevron-right')}</button>`).join('')||empty()}</div></section>
      <div class="dashboard-side"><section class="panel"><div class="panel-head"><h2>需要关注</h2></div><div class="attention-list">${[['circle-alert','执行失败',n.failed,'failed'],['clock-3','等待超过 30 秒',n.longWait,'longWait'],['pause-circle','等待操作',n.waiting,'waiting']].map(([ic,label,count,kind])=>`<button type="button" data-action="attention" data-kind="${kind}">${icon(ic)}<span>${label}</span><b>${count}</b>${icon('chevron-right')}</button>`).join('')}</div></section>
      <section class="panel" id="activeUsers"><div class="panel-head"><h2>活跃用户 <small>当前范围</small></h2>${textButton('查看全部 '+icon('chevron-right'),'all-users')}</div><div class="active-user-list">${users.map(u=>[u,qs.filter(q=>q.session.user===u).length]).filter(([,c])=>c).sort((a,b)=>b[1]-a[1]).slice(0,state.showAllUsers?8:3).map(([u,c],i)=>`<button type="button" data-action="user-drill" data-user="${e(u)}"><span class="person-avatar" data-tone="${i%4}">${e(u[0])}</span><span>${e(u)}</span><b>${c}</b>${icon('chevron-right')}</button>`).join('')||empty('暂无用户')}</div></section></div>
    </div></div>`;
}
function sessionList(){const items=filterSessions(state.session),per=12,total=Math.max(1,Math.ceil(items.length/per));state.page=Math.min(state.page,total);const rows=items.slice((state.page-1)*per,state.page*per);
  return `${sessionHeader('list')}<div class="session-content">${sessionFilters()}<form class="list-controls" data-form="sessions"><label class="search-box">${icon('search')}<input name="q" value="${e(state.session.q)}" placeholder="搜索会话名称或 ID" aria-label="搜索会话名称或 ID"></label><button type="submit" class="pbtn primary">查询</button>${button('重置','reset-sessions')}<span class="result-count">共 ${items.length} 个会话</span></form>${chips('session')?`<div class="filter-chips" style="margin-bottom:12px">${chips('session')}</div>`:''}
    <div class="session-grid"><div class="session-row thead"><span>会话名称 / 最近问题</span><span>Agent</span><span>用户</span><span>渠道</span><span>执行状态</span><span>更新时间 ↓</span><span></span></div>${rows.map((s,i)=>`<button type="button" class="session-row" data-session="${s.id}"><span><strong>${e(s.title)}</strong><small title="${s.id}">${s.id.slice(0,8)}…${s.id.slice(-4)} · ${e(s.questions.at(-1))}</small></span><span>${e(s.agent)}</span><span class="person"><span class="person-avatar" data-tone="${i%4}">${e(s.user[0])}</span>${e(s.user)}</span><span>${pill(s.channel,'source')}</span><span>${statusPill(s.status)}</span><span>${time(s.updated,true)}</span>${icon('chevron-right')}</button>`).join('')||empty()}</div>
    <footer class="pagination"><span>每页 12 条 · 共 ${items.length} 条</span><div>${button(icon('chevron-left'),'page','','data-page="'+(state.page-1)+'" '+(state.page===1?'disabled':''))}${Array.from({length:total},(_,i)=>`<button type="button" class="${i+1===state.page?'active':''}" data-action="page" data-page="${i+1}">${i+1}</button>`).join('')}${button(icon('chevron-right'),'page','','data-page="'+(state.page+1)+'" '+(state.page===total?'disabled':''))}</div></footer></div>`;
}
function highlighted(text){const q=state.log.q.trim();if(!q)return e(text);const i=text.toLowerCase().indexOf(q.toLowerCase());return i<0?e(text):e(text.slice(0,i))+`<mark>${e(text.slice(i,i+q.length))}</mark>`+e(text.slice(i+q.length));}
function logDrawer(log){if(!log)return `<aside class="log-drawer"><div class="drawer-title"><h2>日志详情</h2>${button(icon('x'),'close-drawer','icon','aria-label="关闭日志详情"')}</div>${empty('请选择一条日志','点击内容行查看详情和关联标识。')}</aside>`;
  const s=sessions.find(s=>s.id===log.sessionId),safe={time:log.time,level:log.level,event:log.event,service:log.service,instance:log.instance,session_id:log.sessionId,run_id:log.runId,phase:log.phase,duration_ms:log.duration,error_kind:log.errorKind};
  const copy=(value)=>`<span class="copyable"><span style="font-family:ui-monospace,monospace;font-size:10px">${e(value)}</span>${button(icon('copy'),'copy','icon',`data-value="${e(value)}" aria-label="复制 ${e(value)}"`)}</span>`;
  return `<aside class="log-drawer"><div class="drawer-title"><h2>日志详情</h2>${button(icon('x'),'close-drawer','icon','aria-label="关闭日志详情"')}</div><div class="drawer-tags">${pill(log.level,log.level)}${pill(log.source,'source')}${pill(log.phase,'neutral')}<time>${logTime(log.time)}</time></div><h3>${e(log.summary)}</h3><p class="drawer-reason">${e(log.reason)}</p><dl class="drawer-fields">
  <dt>时间</dt><dd>${new Date(log.time).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'})}</dd><dt>事件</dt><dd>${e(log.event)}</dd><dt>Session ID</dt><dd>${copy(log.sessionId)}</dd><dt>Run ID</dt><dd>${copy(log.runId)}</dd><dt>会话名称</dt><dd>${textButton(e(s.title)+' '+icon('arrow-up-right'),'open-session',`data-id="${s.id}"`)}</dd><dt>用户</dt><dd>${e(s.user)}</dd><dt>Agent</dt><dd>${e(s.agent)}</dd>${log.errorKind?`<dt>错误分类</dt><dd>${e(log.errorKind)}</dd>`:''}${log.duration?`<dt>持续时间</dt><dd>${(log.duration/1000).toFixed(0)} 秒</dd>`:''}<dt>实例</dt><dd>${e(log.instance)}</dd><dt>服务</dt><dd>${e(log.service)}</dd></dl>
  <details class="safe-json"><summary>规范字段</summary><pre>${e(JSON.stringify(safe,null,2))}</pre></details>${button('查看关联会话 '+icon('arrow-right'),'open-session','drawer-link',`data-id="${s.id}"`)}${textButton('只看这一轮的日志','run-logs',`style="margin-top:14px" data-run="${log.runId}" data-id="${s.id}"`)}</aside>`;
}
function logsPage(){const f=state.log,items=filterLogs(f),visible=items.slice(0,state.logLimit);if(!items.some(l=>l.id===state.selectedLog))state.selectedLog=items[0]?.id;const selected=items.find(l=>l.id===state.selectedLog);
  return `<div class="log-page ${state.drawer?'':'drawer-closed'}"><section class="log-workspace"><header class="page-header"><div><h1>日志</h1><p>查看系统运行日志，了解 Agent 的执行过程与状态</p></div><div class="header-right">${sample}</div></header>
    ${state.detailOrigin?`<div style="padding:0 23px 12px">${textButton(icon('arrow-left')+'返回会话','back-detail')}</div>`:''}
    <form class="log-filters" data-form="logs">${select('hours','全部时间',f.hours,[['24','最近 24 小时'],['168','最近 7 天']],'log')}${select('source','全部来源',f.source,['API','网关'],'log')}${select('level','全部级别',f.level,['INFO','WARN','ERROR'],'log')}<label class="search-box">${icon('search')}<input name="q" placeholder="搜索日志内容" aria-label="搜索日志内容" value="${e(f.q)}"></label><button type="submit" class="pbtn primary">查询</button>${button(icon('rotate-cw'),'refresh','', 'aria-label="刷新日志"')}${button(icon('sliders-horizontal'),'id-filters','',`aria-label="按 Session / Run 筛选" aria-expanded="${state.idFilters}"`)}</form>
    ${state.idFilters?`<form class="log-id-fields" data-form="log-ids"><label class="search-box"><input name="sessionId" placeholder="Session ID（精确）" aria-label="Session ID" value="${e(f.sessionId)}"></label><label class="search-box"><input name="runId" placeholder="Run ID（精确）" aria-label="Run ID" value="${e(f.runId)}"></label><button class="pbtn" type="submit">应用</button></form>`:''}
    <div class="log-secondary"><div class="filter-chips">${chips('log')?'已选条件：'+chips('log')+textButton('清空','reset-logs'):'<small>时间倒序 · 点击日志查看详情</small>'}</div><small>当前实例</small></div>
    <div class="log-stream" aria-label="日志内容">${visible.map(l=>`<div class="log-row ${l.id===state.selectedLog&&state.drawer?'selected':''}" tabindex="0" role="button" aria-label="${e(l.summary)}" data-log="${l.id}"><time>${logTime(l.time)}</time>${pill(l.level,l.level)}${pill(l.source,'source')}<span class="log-summary" title="${e(l.summary)}">${highlighted(l.summary)}</span><button type="button" class="log-session" data-session="${l.sessionId}" title="打开会话 ${l.sessionId}">${l.sessionId.slice(0,8)}…${l.sessionId.slice(-8)}</button></div>`).join('')||empty('没有匹配的日志','可以清除条件或扩大时间范围。')}</div><footer class="log-footer"><span>已显示 ${visible.length} / ${items.length} 条 · 当前实例 local-demo-01</span>${visible.length<items.length?textButton('加载更多 '+icon('chevron-down'),'more-logs'):'<span>已到末尾</span>'}</footer></section>${state.drawer?logDrawer(selected):''}</div>`;
}
function openSession(id){state.detailOrigin=null;route(`/sessions/detail/${id}`);}
function render(){disposeDetail?.();disposeDetail=null;document.querySelectorAll('.proto-select[open]').forEach(el=>el.open=false);const r=getRoute();document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===r.section));
  if(r.section==='logs'){main.innerHTML=logsPage();}
  else if(r.view==='detail'){
    const s=sessions.find(s=>s.id===r.id);main.innerHTML='';
    if(!s){main.innerHTML=empty('会话不存在','这是本地原型，请从示例列表进入。');return;}
    const cleanup=mountReadonlySession(main,s,{onBack:()=>route(`/sessions/${state.lastSessionView}`),onLogs:()=>{state.detailOrigin=s.id;state.log={hours:'168',source:'',level:'',q:'',sessionId:s.id,runId:''};state.selectedLog=logs.find(l=>l.sessionId===s.id)?.id;state.drawer=true;route('/logs');}});
    disposeDetail=typeof cleanup==='function'?cleanup:cleanup?.dispose;
  }else{state.lastSessionView=r.view==='list'?'list':'overview';main.innerHTML=`<section class="page-shell">${state.lastSessionView==='list'?sessionList():overview()}</section>`;}
  icons();
}
document.addEventListener('click',async event=>{
  const target=event.target.closest('button,a,[data-log]');
  if(!event.target.closest('.proto-select'))document.querySelectorAll('.proto-select[open]').forEach(el=>el.open=false);
  if(!target)return;
  if(target.dataset.filter){const {scope,filter,value}=target.dataset;state[scope][filter]=value;state.page=1;render();return;}
  if(target.dataset.clear){state[target.dataset.scope][target.dataset.clear]='';state.page=1;render();return;}
  if(target.dataset.session){openSession(target.dataset.session);return;}
  if(target.dataset.log){state.selectedLog=target.dataset.log;state.drawer=true;const top=document.querySelector('.log-stream')?.scrollTop||0;render();document.querySelector('.log-stream').scrollTop=top;return;}
  const {action}=target.dataset;if(!action)return;
  switch(action){
    case 'session-view':route(`/sessions/${target.dataset.view}`);break;
    case 'reset-sessions':state.session={days:'7',agent:'',user:'',channel:'',status:'',q:'',wait:false};state.page=1;render();break;
    case 'refresh':toast('已刷新 · 当前为本地示例数据');break;
    case 'page':state.page=Number(target.dataset.page);render();break;
    case 'show-questions':state.showQuestions=true;showQuestionDialog();break;
    case 'stat':if(target.dataset.kind==='questions'){showQuestionDialog();break;}if(target.dataset.kind==='users'){state.showAllUsers=true;render();document.querySelector('#activeUsers')?.scrollIntoView({behavior:'smooth',block:'center'});break;}state.session.status=target.dataset.kind==='failed'?'failed':'';state.page=1;route('/sessions/list');break;
    case 'agent-drill':state.session.agent=target.dataset.agent;state.page=1;route('/sessions/list');break;
    case 'user-drill':state.session.user=target.dataset.user;state.page=1;route('/sessions/list');break;
    case 'all-users':state.showAllUsers=!state.showAllUsers;render();break;
    case 'attention':state.session.status=target.dataset.kind==='longWait'?'':target.dataset.kind;state.session.wait=target.dataset.kind==='longWait';state.page=1;route('/sessions/list');break;
    case 'close-drawer':state.drawer=false;render();break;
    case 'id-filters':state.idFilters=!state.idFilters;render();break;
    case 'reset-logs':state.log={hours:'24',source:'',level:'',q:'',sessionId:'',runId:''};state.detailOrigin=null;state.logLimit=70;render();break;
    case 'open-session':openSession(target.dataset.id);break;
    case 'run-logs':state.log.sessionId=target.dataset.id;state.log.runId=target.dataset.run;state.log.hours='168';render();break;
    case 'back-detail':route(`/sessions/detail/${state.detailOrigin}`);break;
    case 'more-logs':{const top=document.querySelector('.log-stream').scrollTop;state.logLimit+=70;render();document.querySelector('.log-stream').scrollTop=top;break;}
    case 'copy':try{await navigator.clipboard.writeText(target.dataset.value);toast('已复制');}catch{toast('浏览器未允许复制，请手动选择字段');}break;
    case 'close-questions':document.querySelector('#questionsDialog')?.remove();break;
  }
});
document.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&event.target.matches('[data-log]')){event.preventDefault();event.target.click();}if(event.key==='Escape'){document.querySelectorAll('.proto-select[open]').forEach(el=>el.open=false);document.querySelector('#questionsDialog')?.remove();}});
document.addEventListener('submit',event=>{const form=event.target;if(!form.dataset.form)return;event.preventDefault();const values=Object.fromEntries(new FormData(form));if(form.dataset.form==='sessions'){state.session.q=values.q;state.page=1;}else if(form.dataset.form==='logs'){state.log.q=values.q;state.logLimit=70;}else Object.assign(state.log,values);render();});
function showQuestionDialog(){document.querySelector('#questionsDialog')?.remove();const qs=questionRows(filterSessions(state.session));const dialog=document.createElement('dialog');dialog.id='questionsDialog';dialog.className='questions-dialog';dialog.innerHTML=`<header><div><h2>用户提问</h2><small>当前筛选范围 · ${qs.length} 条原始提问</small></div>${button(icon('x'),'close-questions','icon','aria-label="关闭用户提问"')}</header><div class="questions-dialog-body">${qs.map(q=>`<button type="button" class="question-row" data-session="${q.session.id}"><strong>${e(q.text)}</strong><span>${e(q.session.user)}</span><span>${e(q.session.agent)}</span><span>${time(q.at,true)}</span>${icon('chevron-right')}</button>`).join('')||empty()}</div>`;document.body.append(dialog);dialog.addEventListener('click',ev=>{if(ev.target.closest('[data-session]'))dialog.remove();});dialog.showModal();icons();}
window.addEventListener('hashchange',()=>{document.querySelector('#questionsDialog')?.remove();render();});
render();
