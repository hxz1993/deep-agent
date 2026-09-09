/* 设计原型：完全虚构样本，无网络、无业务写入、无持久存储。 */
'use strict';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  bot:'<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 3v4M8 12v3m8-3v3M1 12h3m16 0h3"/>',
  chat:'<path d="M21 11a9 9 0 0 1-9 9H4l-3 2 2-6a9 9 0 1 1 18-5Z"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 17l10 5 10-5"/>',
  book:'<path d="M12 5v15M12 5C8 2 4 3 2 4v15c3-1 6-1 10 1 4-2 7-2 10-1V4c-3-1-6-2-10 1Z"/>',
  plug:'<path d="m8 3 3 3m5-3 3 3M9 6l9 9m-12-3 6 6m-2-9-5 5a5 5 0 0 0 7 7l5-5M3 21l3-3"/>',
  channel:'<circle cx="12" cy="12" r="2"/><path d="M7 7a7 7 0 0 0 0 10m10-10a7 7 0 0 1 0 10M4 4a11 11 0 0 0 0 16M20 4a11 11 0 0 1 0 16"/>',
  settings:'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
  pulse:'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  search:'<circle cx="10" cy="10" r="7"/><path d="m16 16 5 5"/>',
  logs:'<path d="M5 3h14v18H5zM8 7h8M8 11h8M8 15h5"/>',
  stats:'<path d="M4 3v18h17M8 17v-5m5 5V7m5 10V4"/>',
  person:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v2"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.logs}</svg>`;
const pages = {logs:{label:'日志',title:'运行日志',icon:'logs'},sessions:{label:'会话排查',title:'会话排查',icon:'search'},stats:{label:'会话统计',title:'会话统计与用户问题',icon:'stats'}};
const variants = {
  A:{name:'平铺页签',note:'单一诊断入口，顶部三页签。改动最少，适合第一版。',defaults:{logs:'table',sessions:'table',stats:'overview'}},
  B:{name:'专属侧栏',note:'诊断内独立导航，列表与详情分工明确。更适合长期扩展。',defaults:{logs:'bottom',sessions:'dialogue',stats:'questions'}},
  C:{name:'导航内展开',note:'主导航直接展开三个子入口，省去第二侧栏。适合高频直达。',defaults:{logs:'timeline',sessions:'bottom',stats:'compare'}},
  D:{name:'Agent 工作台',note:'先确定 Agent，再看日志、会话与提问。更适合使用反馈和运营。',defaults:{logs:'bottom',sessions:'dialogue',stats:'questions'}},
};
const modes = {logs:{table:'表格 + 右侧详情',bottom:'列表 + 下方字段',timeline:'事件时间线'},sessions:{table:'会话表格 + 轨迹',bottom:'会话列表 + 下方排查',dialogue:'问题列表 + 对话详情'},stats:{overview:'概览 + 提问分布',questions:'原始问题明细',compare:'Agent 对比 + 问题样例'}};
const sessions = [
  {id:'demo-session-001',run:'demo-run-001',agent:'企微助手',user:'陈同学',source:'企微 · 私聊',time:'09-09 10:24',day:6,title:'数据流节点查询',question:'数据流里有哪些查询节点？它们有什么区别？',answer:'查询类节点包括数据集查询、连接器查询与对象查询。数据集查询读取已有数据集；连接器查询执行指定数据源查询；对象查询面向业务对象。选择时先明确数据来源和权限。',status:'完成',duration:'18.4 秒',tools:2},
  {id:'demo-session-002',run:'demo-run-002',agent:'DeepCube助手',user:'林同学',source:'平台客户端',time:'09-09 10:18',day:6,title:'月度费用变动分析',question:'帮我对比 8 月和 7 月的管理费用，指出变化最大的科目。',answer:'本次查询未完成：模型服务连接超时。已完成的工具结果保留在轨迹中，需要排查对应请求阶段。',status:'失败',duration:'61.2 秒',tools:3},
  {id:'demo-session-003',run:'demo-run-003',agent:'电子表格助手',user:'赵同学',source:'平台客户端',time:'09-09 09:56',day:6,title:'预算表公式说明',question:'这张预算表的同比公式应该怎么写？',answer:'先确定本期值和上年同期值的单元格位置，再使用（本期值 - 同期值）/ 同期值。同期为零时需要单独处理，避免除零错误。',status:'完成',duration:'12.1 秒',tools:1},
  {id:'demo-session-004',run:'demo-run-004',agent:'企微助手',user:'王同学',source:'企微 · 群聊',time:'09-08 17:42',day:5,title:'电子表格使用说明',question:'文档中电子表格的数据权限是如何设置的？',answer:'文档读取仍在等待。最后确认的阶段是 docs_read 调用，尚未返回最终答案。',status:'等待中',duration:'35.0 秒',tools:2},
  {id:'demo-session-005',run:'demo-run-005',agent:'DeepCube助手',user:'陈同学',source:'控制台',time:'09-08 15:20',day:5,title:'利润指标口径',question:'毛利率和营业利润率的口径有什么区别？',answer:'毛利率通常以营业收入减营业成本后的毛利为分子；营业利润率以营业利润为分子，还受到期间费用等项目影响。具体以你们模型中的指标定义为准。',status:'完成',duration:'9.8 秒',tools:1},
  {id:'demo-session-006',run:'demo-run-006',agent:'榕树工单助手',user:'赵同学',source:'控制台',time:'09-07 14:33',day:4,title:'数据权限故障排查',question:'同事打不开这个数据集，需要补充哪些排查信息？',answer:'建议先补充环境、数据集标识、报错时间和完整错误提示，再检查所属空间与数据访问权限。不要提供密码或 Token。',status:'完成',duration:'15.2 秒',tools:2},
  {id:'demo-session-007',run:'demo-run-007',agent:'电子表格助手',user:'赵同学',source:'平台客户端',time:'09-09 10:02',day:6,title:'预算表公式说明',question:'那如果去年是零，今年有值呢？',answer:'上年同期为零时增长率不可按普通除法计算。可显示“新增”或约定标记，具体按报表口径处理。',status:'完成',duration:'7.2 秒',tools:0,followup:'demo-session-003'},
  {id:'demo-session-008',run:'demo-run-008',agent:'企微助手',user:'王同学',source:'企微 · 私聊',time:'09-05 16:40',day:2,title:'查询文档目录',question:'先帮我查一下文档中心的目录。',answer:'用户主动取消了本次执行。已完成的工具调用仍可在轨迹中查看。',status:'取消',duration:'5.4 秒',tools:1},
  {id:'demo-session-009',run:'demo-run-009',agent:'DeepCube助手',user:'陈同学',source:'平台客户端',time:'09-04 11:15',day:1,title:'费用分析口径',question:'管理费用通常包括哪些科目？',answer:'一般包括管理人员薪酬、办公费、折旧摊销等，但不同企业科目归集口径有差异。请以当前模型的科目维度与指标定义为准。',status:'完成',duration:'11.3 秒',tools:1},
  {id:'demo-session-010',run:'demo-run-010',agent:'企微助手',user:'赵同学',source:'企微 · 私聊',time:'09-03 10:09',day:0,title:'查找文档示例',question:'哪里有数据流多分支的配置示例？',answer:'可以从数据流文档的分支节点与合并节点章节开始，先确认每条分支的输入结构和最终合并方式。',status:'完成',duration:'16.7 秒',tools:2},
];
// 两条提问属于同一会话：问题明细与会话数采用不同统计口径。
sessions[6].id = sessions[2].id;
const agentNames = [...new Set(sessions.map(s=>s.agent))];
const logs = [
  {id:'log-01',s:1,time:'10:19:02.419',level:'ERROR',service:'API',event:'model.request_failed',summary:'模型请求失败：连接超时',phase:'model',duration:60120,classification:'provider_timeout'},
  {id:'log-02',s:1,time:'10:18:32.300',level:'WARN',service:'API',event:'model.waiting',summary:'模型请求仍在等待，已持续 30 秒',phase:'model',duration:30000},
  {id:'log-03',s:0,time:'10:24:19.224',level:'INFO',service:'网关',event:'channel.final_delivered',summary:'最终答案投递完成',phase:'delivery',duration:84},
  {id:'log-04',s:0,time:'10:24:19.140',level:'INFO',service:'API',event:'execution.completed',summary:'执行完成',phase:'execution',duration:18400},
  {id:'log-05',s:0,time:'10:24:12.705',level:'INFO',service:'API',event:'tool.completed',summary:'文档工具调用完成',phase:'tool',duration:845},
  {id:'log-06',s:0,time:'10:24:11.860',level:'INFO',service:'API',event:'tool.started',summary:'开始调用文档读取工具',phase:'tool',duration:0},
  {id:'log-07',s:0,time:'10:24:00.740',level:'INFO',service:'网关',event:'channel.dispatched',summary:'渠道消息已分发至 Agent',phase:'dispatch',duration:43},
  {id:'log-08',s:0,time:'10:24:00.697',level:'INFO',service:'网关',event:'channel.received',summary:'收到渠道消息',phase:'receive',duration:0},
  {id:'log-09',s:3,time:'17:42:35.100',level:'WARN',service:'API',event:'tool.waiting',summary:'文档读取仍在等待，已持续 30 秒',phase:'tool',duration:30000},
  {id:'log-10',s:2,time:'09:56:12.100',level:'INFO',service:'API',event:'execution.completed',summary:'执行完成',phase:'execution',duration:12100},
];
let state = {variant:'A',page:'logs',mode:'table',agent:'all',status:'all',query:'',selected:'log-01',detail:'trajectory',demo:'ready'};
let toastTimer;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500);}
function status(s){return `<span class="status ${s==='失败'?'bad':s==='等待中'?'wait':s==='取消'?'neutral':''}">${escape(s)}</span>`;}
function pageButtons(cls=''){return Object.entries(pages).map(([key,p])=>`<button class="${cls} ${state.page===key?'active':''}" data-page="${key}" ${state.page===key?'aria-current="page"':''}>${icon(p.icon)}<span>${p.label}</span></button>`).join('');}
function matches(s){if(state.agent!=='all'&&s.agent!==state.agent)return false;if(state.status!=='all'&&s.status!==state.status)return false;const q=state.query.toLowerCase();return !q||s.id===q||s.run===q||[s.question,s.title,s.user,s.agent].some(v=>v.toLowerCase().includes(q));}
function filtered(){return state.demo==='empty'?[]:sessions.filter(matches);}
function uniqueSessions(rows){return [...new Map([...rows].sort((a,b)=>a.time.localeCompare(b.time)).map(s=>[s.id,s])).values()].sort((a,b)=>b.time.localeCompare(a.time));}
function filteredLogs(){return state.demo==='empty'?[]:logs.filter(l=>{const s=sessions[l.s];if(state.agent!=='all'&&s.agent!==state.agent)return false;if(state.status!=='all'&&s.status!==state.status)return false;const q=state.query.toLowerCase();return !q||s.id===q||s.run===q||[l.summary,l.event,l.level,l.service].some(v=>v.toLowerCase().includes(q));}).sort((a,b)=>(sessions[b.s].time.slice(0,5)+b.time).localeCompare(sessions[a.s].time.slice(0,5)+a.time));}
function fields(values){return `<dl class="fields">${values.map(([k,v])=>`<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join('')}</dl>`;}
function renderNav(){
  $('variants').innerHTML=Object.entries(variants).map(([key,v])=>`<button class="variant ${state.variant===key?'active':''}" data-variant="${key}" aria-pressed="${state.variant===key}"><em>${key}</em>${v.name}</button>`).join('');
  $('variantNote').innerHTML=`<strong>方案 ${state.variant}</strong>${variants[state.variant].note}`;
  $('app').className='app variant-'+state.variant;
  const railLinks=[['chat','对话测试'],['bot','Agent'],['caption','设置'],['layers','Provider'],['book','Skill'],['plug','MCP'],['layers','内置工具'],['channel','渠道'],['caption','系统'],['settings','全局配置']];
  $('rail').innerHTML=`<div class="brand"><span class="brand-icon">${icon('bot')}</span><span class="brand-name">DeepAgent</span></div>`+railLinks.map(([i,label])=>i==='caption'?`<div class="rail-caption">${label}</div>`:`<button class="rail-link" data-demo="保留现有 ${label} 入口；此原型只演示诊断中心" title="${label}">${icon(i)}<span class="label">${label}</span></button>`).join('')+`<div class="rail-spacer"></div><button class="rail-link active" data-page="${state.page}" title="诊断中心">${icon('pulse')}<span class="label">诊断中心</span></button>${state.variant==='C'?`<div class="rail-sub">${Object.entries(pages).map(([key,p])=>`<button class="${state.page===key?'active':''}" data-page="${key}">${p.label}</button>`).join('')}</div>`:''}<div class="user">${icon('person')}<span>管理员 · 只读排查</span></div>`;
  $('subnav').className='subnav'+(state.variant==='B'?' visible':'');
  $('subnav').innerHTML=`<h2>诊断中心</h2>${pageButtons('rail-link')}<div class="sub-note">当前部署实例<br>API + 渠道网关<br><br>运行日志与会话事实分开查询，按 Session / Run 关联。</div>`;
  $('pageTabs').innerHTML=['A','D'].includes(state.variant)?pageButtons():'';
  $('breadcrumb').textContent=state.variant==='D'?`诊断中心 / ${state.agent==='all'?'全部 Agent':state.agent}`:'诊断中心 / 当前部署实例';
  $('pageTitle').textContent=pages[state.page].title;
  $('contentMode').innerHTML=Object.entries(modes[state.page]).map(([key,label])=>`<option value="${key}" ${state.mode===key?'selected':''}>${label}</option>`).join('');
  $('agentFilter').innerHTML='<option value="all">全部 Agent</option>'+agentNames.map(n=>`<option ${state.agent===n?'selected':''}>${n}</option>`).join('');
  $('agentScope').innerHTML=state.variant==='D'?`<div class="agent-scope">${['all',...agentNames].map(a=>`<button class="scope-option ${state.agent===a?'active':''}" data-agent="${a}"><strong>${a==='all'?'全部 Agent':a}</strong><small>${sessions.filter(s=>a==='all'||s.agent===a).length} 条用户提问 · 示例</small></button>`).join('')}</div>`:'';
  $('search').placeholder=state.page==='sessions'?'名称模糊 / 完整会话 ID 精确':state.page==='stats'?'搜索用户问题 / 用户 / 会话 ID':'日志关键词 / Session ID / Run ID';
  $('searchIcon').innerHTML=icon('search');
}
function empty(title,text,i='search'){return `<div class="empty-state">${icon(i)}<h2>${title}</h2><p>${text}</p></div>`;}
function logDetail(l){const s=sessions[l.s];return `<header><div><div class="eyebrow">LOG DETAIL</div><h3>日志详情</h3></div><span class="level ${l.level}">${l.level}</span></header><p>${escape(l.summary)}</p>${fields([['事件',l.event],['服务',l.service+' / demo-instance'],['Agent',s.agent],['Session',s.id],['Run',s.run],['阶段',l.phase],['耗时',l.duration+' ms'],['分类',l.classification||'—']])}<button class="button" data-open-session="${s.id}">查看关联会话 →</button><p class="safe-note">仅安全字段，不展示正文、工具参数、凭据或原始堆栈。以上均为模拟值。</p>`;}
function logTable(rows){return `<table><thead><tr><th>时间</th><th>等级 / 来源</th><th>事件摘要</th><th>Agent</th></tr></thead><tbody>${rows.map(l=>`<tr tabindex="0" data-select-log="${l.id}" class="${state.selected===l.id?'selected':''}" aria-selected="${state.selected===l.id}"><td class="nowrap"><code>${l.time}</code></td><td><span class="level ${l.level}">${l.level}</span><small class="idline">${l.service}</small></td><td class="log-summary"><span class="row-main">${l.summary}</span><code class="idline">${l.event}</code></td><td>${sessions[l.s].agent}</td></tr>`).join('')}</tbody></table>`;}
function renderLogs(){const rows=filteredLogs();if(!rows.length)return empty('没有匹配的日志','可以更换关键词或 Agent 范围；不会把查询异常当作无结果。');let selected=rows.find(l=>l.id===state.selected)||rows[0];state.selected=selected.id;
  const title=`<div class="result-heading"><span class="muted">${rows.length} 条模拟记录 · 最新在前</span><small>日志不是会话事件</small></div>`;
  if(state.mode==='bottom')return title+`<div class="bottom-split"><div class="listpane">${logTable(rows)}</div><div class="bottom-detail"><div><div class="eyebrow">SELECTED EVENT</div><h3>${selected.summary}</h3>${fields([['Session',sessions[selected.s].id],['Run',sessions[selected.s].run],['服务',selected.service]])}<button class="click-link" data-open-session="${sessions[selected.s].id}">打开对应会话 →</button></div><div><div class="eyebrow">STRUCTURED FIELDS / SAFE</div><pre class="console-code">${escape(JSON.stringify({event:selected.event,phase:selected.phase,elapsed_ms:selected.duration,error_category:selected.classification||null,instance:'demo-instance'},null,2))}</pre></div></div></div>`;
  if(state.mode==='timeline')return title+`<div class="timeline-layout"><div class="timeline"><div class="day-label">按所选范围倒序排列 · 示例事件</div>${rows.map(l=>`<button class="timeline-item ${state.selected===l.id?'selected':''}" data-select-log="${l.id}"><span class="time">${l.time.slice(0,8)}</span><span class="timeline-line"></span><span class="event-block"><header><strong>${l.summary}</strong><span class="level ${l.level}">${l.level}</span></header><p>${sessions[l.s].agent} · ${l.service} · ${l.duration} ms</p><code>${l.event}</code></span></button>`).join('')}</div><aside class="inspector">${logDetail(selected)}</aside></div>`;
  return title+`<div class="split"><div class="listpane">${logTable(rows)}<div class="page-size">当前为完整模拟样本；正式接口采用有界扫描与游标分页。</div></div><aside class="inspector">${logDetail(selected)}</aside></div>`;
}
function trace(s){return `<div class="trace"><div class="trace-step"><strong>用户提问已保存</strong><small>${s.time} · ${s.user} · ${s.source}</small></div><div class="trace-step"><strong>助手正文</strong><small>开始查询相关资料，确认数据口径。</small></div>${s.tools?`<div class="trace-step"><strong>工具调用与结果</strong><code>docs_search → docs_read</code><small>${s.tools} 次调用 · 可展开已有事件字段</small></div>`:''}<div class="trace-step ${s.status==='失败'?'error':''}"><strong>${s.status==='失败'?'执行异常：Provider 连接超时':s.status==='等待中'?'工具等待中':s.status==='取消'?'主动取消':'最终答案'}</strong><small>${s.status==='完成'?'最终正文保留，过程中间内容可查轨迹。':s.answer}</small></div></div>`;}
function sessionDetail(s){let body;
  if(state.detail==='answer')body=sessions.filter(item=>item.id===s.id).sort((a,b)=>a.time.localeCompare(b.time)).map(item=>`<div class="question-label"><span class="avatar">${item.user[0]}</span>${item.user} · ${item.time}</div><p class="question">${escape(item.question)}</p><div class="question-label">${icon('bot')} ${item.agent}</div><div class="answer">${escape(item.answer)}</div>${item.followup?'<p class="inline-note">这是追问，需结合会话前文理解，不单独判断质量。</p>':'<div class="inline-note"></div>'}`).join('');
  else if(state.detail==='logs'){const related=logs.filter(l=>sessions[l.s].id===s.id);body=related.length?`<div class="trace">${related.map(l=>`<div class="trace-step ${l.level==='ERROR'?'error':''}"><strong>${l.summary}</strong><code>${l.event}</code><small>${l.time} / ${l.service}</small></div>`).join('')}</div>`:'<p class="inline-note">该模拟会话没有关联运行日志；历史消息仍然可以查看。</p>';}
  else body=trace(s);
  return `<header><div><div class="eyebrow">SESSION DETAIL</div><h3>${escape(s.title)}</h3></div>${status(s.status)}</header><code>${s.id}</code><div class="inline-note">${s.agent} · ${s.user} · ${s.source}</div><div class="mini-tabs">${[['answer','问答'],['trajectory','轨迹'],['logs','关联日志']].map(([k,n])=>`<button data-detail="${k}" class="${state.detail===k?'active':''}">${n}</button>`).join('')}</div>${body}<button class="button" data-demo="安全轨迹导出：正式版需管理员接口支持；此原型不下载真实数据">导出安全轨迹 JSON</button><p class="safe-note">管理员只读，不代发、不停止、不删除他人会话。</p>`;
}
function sessionTable(rows){return `<table><thead><tr><th>会话 / ID</th><th>Agent / 用户</th><th>执行结果</th><th>最后提问</th></tr></thead><tbody>${rows.map(s=>`<tr tabindex="0" data-select-session="${s.id}" class="${state.selected===s.id?'selected':''}"><td><span class="row-main">${s.title}</span><code class="idline">${s.id}</code></td><td>${s.agent}<small class="idline">${s.user} · ${s.source}</small></td><td>${status(s.status)}<small class="idline">${s.duration}</small></td><td class="nowrap">${s.time}</td></tr>`).join('')}</tbody></table>`;}
function renderSessions(){const rows=uniqueSessions(filtered());if(!rows.length)return empty('没有匹配的会话','示例：输入“预算”模糊查名称，或输入 demo-session-003 精确查会话。');const s=rows.find(s=>s.id===state.selected)||rows[0];state.selected=s.id;
  const title=`<div class="result-heading"><span class="muted">${rows.length} 个模拟会话 · 跨用户只读</span><small>示例 ID：demo-session-003</small></div>`;
  if(state.mode==='dialogue')return title+`<div class="split conversation-split"><div class="listpane">${rows.map(item=>`<button class="list-item ${s.id===item.id?'selected':''}" data-select-session="${item.id}"><header><span>${item.agent}</span>${status(item.status)}</header><p>${item.question}</p><footer><span>${item.user} · ${item.source}</span><span>${item.time}</span></footer></button>`).join('')}</div><aside class="inspector">${sessionDetail(s)}</aside></div>`;
  if(state.mode==='bottom')return title+`<div class="bottom-split"><div class="listpane">${sessionTable(rows)}</div><div class="bottom-detail"><div><div class="eyebrow">QUESTION & FINAL ANSWER</div><p class="question">${s.question}</p><div class="answer">${s.answer}</div></div><div><div class="detail-title"><h3>本次执行轨迹</h3>${status(s.status)}</div>${trace(s)}<button class="click-link" data-session-logs="${s.id}">按此 Session 查询日志 →</button></div></div></div>`;
  return title+`<div class="split"><div class="listpane">${sessionTable(rows)}</div><aside class="inspector">${sessionDetail(s)}</aside></div>`;
}
function metrics(rows){return `<div class="metrics">${[['用户提问',rows.length,'一条真实用户消息计一次'],['会话',new Set(rows.map(s=>s.id)).size,'同一会话追问不重复计数'],['活跃用户',new Set(rows.map(s=>s.user)).size,'按稳定用户标识去重'],['执行失败',rows.filter(s=>s.status==='失败').length,'执行完成不等于回答正确']].map(([n,v,d])=>`<div class="metric"><span>${n}</span><strong>${v}</strong><small>${d}</small></div>`).join('')}</div>`;}
function questionTable(rows){return `<table class="question-table"><thead><tr><th>时间 / 用户</th><th>原始用户问题</th><th>Agent / 来源</th><th>最终回复摘要</th><th>执行结果</th></tr></thead><tbody>${rows.map((s)=>`<tr tabindex="0" data-open-session="${s.id}"><td class="nowrap">${s.time}<small class="idline">${s.user}</small></td><td><span>${s.question}</span>${s.followup?'<small class="idline">追问 · 点击查看完整上下文</small>':''}<code class="idline">${s.id}</code></td><td>${s.agent}<small class="idline">${s.source}</small></td><td class="answer-preview">${s.answer.slice(0,62)}${s.answer.length>62?'…':''}</td><td>${status(s.status)}</td></tr>`).join('')}</tbody></table>`;}
function renderStats(){const rows=filtered();if(!rows.length)return empty('没有匹配的用户问题','更换 Agent、关键词或执行结果即可查看其它模拟提问。');const counts=agentNames.map(a=>({a,n:rows.filter(s=>s.agent===a).length})).filter(a=>a.n).sort((a,b)=>b.n-a.n);
  if(state.mode==='compare')return metrics(rows)+`<p class="inline-note">按 Agent 对比使用情况，展示真实问题样例，不自动生成业务分类或满意度。</p><div class="comparison-grid">${counts.map(({a,n})=>{const r=rows.filter(s=>s.agent===a);return `<section class="agent-card"><header><h3>${icon('bot')} ${a}</h3><button class="click-link" data-agent-questions="${a}">查看全部提问 →</button></header><div class="counts"><div><b>${n}</b><small>用户提问</small></div><div><b>${new Set(r.map(s=>s.user)).size}</b><small>活跃用户</small></div><div><b>${r.filter(s=>s.status==='失败').length}</b><small>执行失败</small></div></div><p>最近用户在问</p>${r.slice(0,3).map(s=>`<div tabindex="0" class="sample-question" data-open-session="${s.id}">${s.question}</div>`).join('')}</section>`;}).join('')}</div>`;
  const listing=`<section class="panel"><div class="panel-head"><h3>用户问题明细</h3><small>${rows.length} 条 · 点击一行查看问答与轨迹</small></div><div class="listpane">${questionTable(rows)}</div></section>`;
  if(state.mode==='questions')return `<div class="mini-metric"><span>用户提问 <b>${rows.length}</b></span><span>会话 <b>${new Set(rows.map(s=>s.id)).size}</b></span><span>活跃用户 <b>${new Set(rows.map(s=>s.user)).size}</b></span></div><p class="inline-note">从已有会话事实读取，不另建问答事实库。用户原话不经过自动改写；执行成功不代表答案正确。</p>`+listing;
  const days=Array.from({length:7},(_,i)=>rows.filter(s=>s.day===i).length);const max=Math.max(...days,1);
  return metrics(rows)+`<div class="chart-row"><section class="panel"><div class="panel-head"><h3>提问量</h3><small>当前筛选 · 7 天模拟样本</small></div><div class="chart">${days.map((n,i)=>`<div class="barcol" style="--h:${n/max*85}%"><b>${n}</b><i style="height:${n/max*85}%"></i><span>09.${String(i+3).padStart(2,'0')}</span></div>`).join('')}</div></section><section class="panel"><div class="panel-head"><h3>Agent 使用分布</h3><small>按用户提问条数</small></div><div class="ranking">${counts.map(({a,n})=>`<div class="ranking-row"><span>${a}</span><span class="meter"><i style="width:${n/Math.max(...counts.map(x=>x.n))*100}%"></i></span><span>${n}</span></div>`).join('')}</div></section></div>`+listing;
}
function renderContent(){
  $('rangeNotice').innerHTML=state.demo==='partial'?'<div class="notice">仅查到部分范围：达到单次扫描预算。正式实现应返回游标与已扫描范围，不能标成完整结果。此处演示样本。</div>':'';
  if(state.demo==='denied')$('content').innerHTML=empty('无权查看诊断数据','需要企业管理员权限；不回退读取其他用户会话，也不将权限错误显示为无数据。','lock');
  else if(state.demo==='unavailable')$('content').innerHTML=empty('日志源暂不可用','当前实例诊断目录不可读。不能据此判断业务没有执行；正式会话事实查询与日志源分开处理。','logs');
  else $('content').innerHTML=state.page==='logs'?renderLogs():state.page==='sessions'?renderSessions():renderStats();
  const n=state.page==='logs'?filteredLogs().length:state.page==='sessions'?uniqueSessions(filtered()).length:filtered().length;
  $('resultCount').textContent=['denied','unavailable'].includes(state.demo)?'查询未完成':`${n} ${state.page==='sessions'?'个会话':'条记录'} · 模拟样本`;
}
function render(){renderNav();renderContent();}
function setPage(page){state.page=page;state.mode=variants[state.variant].defaults[page];state.selected='';state.detail=state.mode==='dialogue'?'answer':'trajectory';state.demo='ready';$('demoState').value='ready';render();}
function applySearch(){state.query=$('search').value.trim();state.selected='';renderContent();}
document.addEventListener('click',e=>{
  const el=e.target.closest('button,[data-open-session],[data-select-session],[data-select-log]');if(!el)return;
  if(el.dataset.variant){state.variant=el.dataset.variant;state.mode=variants[state.variant].defaults[state.page];state.detail=state.mode==='dialogue'?'answer':'trajectory';render();}
  else if(el.dataset.page)setPage(el.dataset.page);
  else if(el.dataset.selectLog){state.selected=el.dataset.selectLog;renderContent();}
  else if(el.dataset.selectSession){state.selected=el.dataset.selectSession;renderContent();}
  else if(el.dataset.detail){state.detail=el.dataset.detail;renderContent();}
  else if(el.dataset.agent){state.agent=el.dataset.agent;state.selected='';render();}
  else if(el.dataset.agentQuestions){state.agent=el.dataset.agentQuestions;state.page='stats';state.mode='questions';render();}
  else if(el.dataset.openSession){state.page='sessions';state.mode='dialogue';state.selected=el.dataset.openSession;state.detail='answer';state.query='';state.agent='all';state.status='all';state.demo='ready';$('search').value='';$('statusFilter').value='all';$('demoState').value='ready';render();}
  else if(el.dataset.sessionLogs){state.page='logs';state.mode='table';state.query=el.dataset.sessionLogs;$('search').value=state.query;render();}
  else if(el.dataset.demo)toast(el.dataset.demo);
});
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-open-session],[data-select-session],[data-select-log]')&&!e.target.matches('button')){e.preventDefault();e.target.click();}});
$('contentMode').addEventListener('change',e=>{state.mode=e.target.value;state.detail=state.mode==='dialogue'?'answer':'trajectory';renderContent();});
$('demoState').addEventListener('change',e=>{state.demo=e.target.value;renderContent();});
$('agentFilter').addEventListener('change',e=>{state.agent=e.target.value;state.selected='';render();});
$('statusFilter').addEventListener('change',e=>{state.status=e.target.value;state.selected='';renderContent();});
$('queryBtn').addEventListener('click',applySearch);
$('search').addEventListener('keydown',e=>{if(e.key==='Enter')applySearch();});
$('resetBtn').addEventListener('click',()=>{state.query='';state.agent='all';state.status='all';state.demo='ready';$('search').value='';$('statusFilter').value='all';$('demoState').value='ready';render();});
$('refreshBtn').addEventListener('click',()=>{renderContent();toast('示例已刷新；原型未发送网络请求。');});
render();
