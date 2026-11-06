/* Local interaction prototype. No product API calls or persistent configuration writes. */
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = () => window.lucide?.createIcons();
const icon = name => `<i data-lucide="${name}"></i>`;
const copy = value => JSON.parse(JSON.stringify(value));
const labels = {none:'关闭',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'超高',max:'最高',default:'默认'};
const nativeLabel = value => labels[value] || value;
const asset = name => `../../../../console/assets/provider-logos/${name}`;
const logos = {alibaba:'qwen.png',anthropic:'anthropic.png',deepseek:'deepseek.ico',google:'google-gemini.png',minimax:'minimax.ico','minimax-cn':'minimax.ico',moonshotai:'kimi.ico',openai:'openai.svg',stepfun:'stepfun.svg',xai:'xai.svg',zhipuai:'zai.svg',private:'openai-compatible.svg'};
const logo = provider => `<img class="provider-icon" src="${asset(logos[provider] || logos.private)}" alt="">`;
const catalogs = window.reasoningCatalog;
const fromCatalog = (provider, code) => {
  const entry = catalogs.find(m => m.provider === provider && m.code === code);
  if (!entry) throw new Error(`Missing prototype source: ${provider}/${code}`);
  return {...copy(entry), key:provider + ':' + code, source:'catalog', original:copy(entry)};
};
const models = [
  fromCatalog('alibaba','qwen3.8-flash'),fromCatalog('deepseek','deepseek-flash'),
  fromCatalog('openai','gpt-6.1-sol'),fromCatalog('openai','gpt-6.1-terra'),
  fromCatalog('moonshotai','kimi-k2.6'),fromCatalog('moonshotai','kimi-k2.7-code-highspeed'),
  fromCatalog('google','gemini-2.5-pro'),fromCatalog('openai','gpt-4.1'),
  {key:'private:private-model',provider:'private',providerName:'OpenAI Compatible',code:'private-model',name:'私有部署模型',reasoning:null,efforts:[],toggle:false,budget:null,default:null,context:262144,output:32768,input:['text'],source:'manual',original:null},
];
const state = {
  view:'chat',model:models[0].key,effort:'inherit',provider:'alibaba',catalogProvider:'alibaba',search:'',page:0,
  agent:{name:'业务分析助手',model:models[0].key,effort:'low'},agentDraft:null,dialogDraft:null,arrayEditing:false,
  inspecting:'new',chatMode:'conversation',messages:[],
  lastRun:{model:models[0].code,effort:'low',source:'Agent 默认',intent:'inherit',provider:'alibaba',request:{mode:'effort',value:'low'}},
};
state.initialRun=copy(state.lastRun);
const inspectedRun=()=>state.inspecting==='old'?null:state.inspecting==='sample'?state.initialRun:state.inspecting.startsWith('message-')?state.messages[Number(state.inspecting.slice(8))]?.run:state.lastRun;
const findModel = key => models.find(m => m.key === key);
const isValidEffort = (model, value) => value === 'vendor' || (model?.efforts || []).includes(value) || (model?.toggle && ['on','off'].includes(value));
function resolveReasoning(model, effort, agent = state.agent) {
  let selected = effort;
  let source = '本轮指定';
  if (selected === 'inherit') {
    const applicable = model.key === agent.model;
    selected = applicable ? agent.effort : 'vendor';
    source = applicable && selected !== 'vendor' ? 'Agent 默认' : '供应商默认';
  }
  if (selected === 'vendor') return {effort:'vendor',label:'供应商默认',source,request:null};
  if (!isValidEffort(model, selected)) return {effort:selected,label:'档位已失效，请重选',source,error:true,request:null};
  if (selected === 'on' || selected === 'off') return {effort:selected,label:selected === 'on'?'开启思考':'关闭思考',source,request:{mode:'toggle',enabled:selected==='on'}};
  return {effort:selected,label:nativeLabel(selected),source,request:{mode:'effort',value:selected}};
}
function effortChoices(model, inherit = false) {
  const result = [];
  if (inherit) {
    const resolved = resolveReasoning(model, 'inherit');
    result.push({value:'inherit',label:'跟随 Agent',meta:resolved.label});
  }
  result.push({value:'vendor',label:'供应商默认',meta:'不传参数'});
  const options = [];
  if (model.reasoning !== false) {
    if (model.toggle && !model.efforts.includes('none')) options.push({value:'off',label:'关闭思考',meta:'off'});
    if (model.toggle) options.push({value:'on',label:'开启思考',meta:'档位由供应商决定'});
    model.efforts.forEach(value => options.push({value,label:nativeLabel(value),meta:String(value)}));
  }
  if (options.length) result.push({separator:true},...options);
  return result;
}
let menuAnchor = null;
function closeMenu() { $('menuPortal').replaceChildren(); menuAnchor?.setAttribute('aria-expanded','false');menuAnchor=null; }
function showMenu(anchor, options, selected, onSelect, help) {
  if (menuAnchor === anchor) {closeMenu();return;}
  closeMenu();menuAnchor=anchor;anchor.setAttribute('aria-expanded','true');
  const menu=document.createElement('div');menu.className='menu';menu.setAttribute('role','menu');
  menu.innerHTML=options.map((opt,i)=>opt.separator?'<div class="menu-divider"></div>':`<button type="button" role="menuitemradio" aria-checked="${selected===opt.value}" class="menu-item ${selected===opt.value?'selected':''}" data-menu-index="${i}">${opt.provider?logo(opt.provider):''}<span>${esc(opt.label)}</span>${opt.meta?`<small>${esc(opt.meta)}</small>`:''}${selected===opt.value?icon('check'):''}</button>`).join('')+(help?`<div class="menu-help">${esc(help)}</div>`:'');
  // The same portal is inside the native modal when necessary, so it remains in its top layer.
  (anchor.closest('dialog') || $('menuPortal')).append(menu);
  const rect=anchor.getBoundingClientRect();menu.style.minWidth=Math.max(rect.width,240)+'px';
  menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-320))+'px';
  icons();const height=menu.getBoundingClientRect().height;
  menu.style.top=(window.innerHeight-rect.bottom>height+12?rect.bottom+6:Math.max(8,rect.top-height-6))+'px';
  menu.addEventListener('click', event=>{const btn=event.target.closest('[data-menu-index]');if(!btn)return;const opt=options[Number(btn.dataset.menuIndex)];menu.remove();closeMenu();onSelect(opt.value);});
  const buttons=[...menu.querySelectorAll('button')];
  menu.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();let i=buttons.indexOf(document.activeElement);i=event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[i]?.focus();}if(event.key==='Escape'){event.preventDefault();menu.remove();closeMenu();anchor.focus();}});
  buttons.find(b=>b.getAttribute('aria-checked')==='true')?.focus();
}
function modelChoices() {return models.map(model=>({value:model.key,label:model.code,provider:model.provider}));}
function noEffortHelp(model) {
  if (model.reasoning === false) return '该型号未声明支持思考；供应商默认不会发送思考控制参数。';
  if (model.efforts.length || model.toggle) return '仅影响下一次发送，不修改 Agent 默认。';
  if (model.budget) return '该型号仅提供 Token 预算控制，没有档位枚举。此处使用供应商默认。';
  return '未配置可调档位；可在模型配置中声明实际支持的原生值。';
}
function toast(message) {$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3200);}
function navigate(view) {if($('modelDialog').open)$('modelDialog').close();closeMenu();state.view=view;location.hash=view;render();}
function render() {
  closeMenu();document.querySelectorAll('[data-view]').forEach(btn=>btn.classList.toggle('active',btn.dataset.view===state.view));
  $('mainContent').innerHTML=({chat:renderChat,catalog:renderCatalog,models:renderModels,agent:renderAgent,changes:renderChanges}[state.view]||renderChat)();icons();
}
function renderChat() {
  const model=findModel(state.model);const resolved=resolveReasoning(model,state.effort);
  const effortText=state.effort==='inherit'?`跟随 Agent · ${resolved.label}`:resolved.label;
  const run=inspectedRun();
  const displayModel=state.inspecting==='old'?'qwen3.8-flash':run.model;
  return `<div class="chat-workspace">
    <aside class="session-list"><div class="session-agent">${icon('bot')}<strong>${esc(state.agent.name)}</strong><button class="icon-btn muted-action" aria-label="刷新">${icon('refresh-cw')}</button></div><button class="choice muted-action"><span>${icon('layers')}全部应用</span>${icon('chevron-down')}</button><input class="session-search" placeholder="搜索会话名称或 ID" aria-label="搜索会话"><button class="session-card active" data-inspect="new"><span>思考强度选择与业务分析</span><small>2 · 刚刚</small></button><button class="session-card" data-inspect="old"><span>旧会话 · 未记录思考强度</span><small>2 · 昨天</small></button></aside>
    <section class="chat-center"><header class="chat-header"><strong>思考强度选择与业务分析 <span class="badge-note">演示对话</span></strong><div class="segmented"><button data-chat-mode="conversation" class="${state.chatMode==='conversation'?'active':''}">对话</button><button data-chat-mode="trajectory" class="${state.chatMode==='trajectory'?'active':''}">轨迹</button></div></header>
      <div class="chat-scroll"><div class="chat-conversation">${state.chatMode==='trajectory'?renderTrajectory():`<div class="user-message">请比较本月实际与预算的差异，列出需要进一步核实的项目。</div><div class="assistant-message"><div class="run-caption">${icon('chevron-right')}已处理 <span>·</span><button class="run-link" data-inspect="sample">${logo('alibaba')}qwen3.8-flash · 低${icon('info')}</button></div><p>可以按总额、部门和费用项目三个层次核对。先确认实际与预算使用同一期间、组织及币种，再定位差额。</p><ul><li>金额差异：实际金额减预算金额。</li><li>口径核实：检查跨期入账、预算调整与一次性费用。</li><li>待确认内容：数据不完整时保留问题，不直接推断业务原因。</li></ul></div>${state.messages.map((message,index)=>`<div class="user-message" style="margin-top:24px">${esc(message.prompt)}</div><div class="assistant-message"><div class="run-caption">${icon('check')}原型演示 <span>·</span><button class="run-link" data-inspect="message-${index}">${esc(message.run.model)} · ${esc(message.label)}${icon('info')}</button></div><p>本轮选择已记录在右侧概览。这里未调用真实模型。</p></div>`).join('')}`}</div></div>
      <div class="composer-wrap"><div class="composer"><textarea id="chatPrompt" placeholder="输入消息，体验模型和思考强度选择…" aria-label="输入消息"></textarea><div class="composer-footer"><button class="icon-btn muted-action" aria-label="添加附件">${icon('paperclip')}</button><div class="composer-controls"><button id="chatModelPicker" class="composer-picker" aria-haspopup="menu">${logo(model.provider)}${esc(model.code)}${icon('chevron-down')}</button><button id="chatEffortPicker" class="composer-picker" aria-haspopup="menu">${icon('brain-circuit')}<span>${esc(effortText)}</span>${icon('chevron-down')}</button><button id="simulateSend" class="send" aria-label="发送原型消息">${icon('send')}</button></div></div></div><p class="composer-note">${resolved.error?'Agent 默认已失效，请重选档位或调整 Agent 配置。':'本轮选择优先于 Agent 默认；供应商默认不传思考参数。'}</p></div>
    </section>
    <aside class="inspector"><div class="inspector-tabs"><span class="active">概览</span><span>当前</span><span>上下文</span></div><dl class="facts"><dt>会话 ID</dt><dd class="mono">prototype-01</dd><dt>Agent</dt><dd>${esc(state.agent.name)}</dd><dt>模型</dt><dd>${esc(displayModel)}</dd><dt>App / Space</dt><dd>未关联应用 / 未关联空间<small>— / —</small></dd></dl><div class="mini-divider"></div><h4>本轮配置${state.inspecting==='old'?' · 旧记录':''}</h4><dl class="facts"><dt>思考强度</dt><dd>${run?esc(run.effort==='vendor'?'供应商默认':run.effort==='on'?'开启思考':run.effort==='off'?'关闭思考':nativeLabel(run.effort)):'未记录'}${run&& !['vendor','on','off'].includes(run.effort)?`<small class="mono">${esc(run.effort)}</small>`:''}</dd><dt>设置来源</dt><dd>${run?esc(run.source):'—'}</dd><dt>思考参数</dt><dd>${!run?'未记录':run.request?'已显式指定':'未发送'}</dd></dl><p class="history-note">${run?'记录发送该轮时解析的配置，不代表供应商披露了内部实际预算。':'历史未记录的强度保持未知，不从当前 Agent 默认或模型目录推算。'}</p><div class="mini-divider"></div><h4>上下文预算</h4><div class="row spread"><span class="muted">历史用量</span><span>12,400</span></div><div class="budget-bar"><span></span></div><p class="history-note" style="margin-top:12px">此处仅展示现有概览，不增加强度统计。</p></aside>
  </div>`;
}
function renderTrajectory() {
  const run=inspectedRun();
  return `<div class="row spread" style="margin-bottom:18px"><h3>本轮轨迹</h3><span class="tag">演示记录</span></div><div class="trajectory-mini"><div><span>用户提问</span><span class="muted">Completed</span></div><div><span>模型响应</span><span class="muted">${esc(run?.model||'qwen3.8-flash')}</span></div><div><span>思考强度</span><span>${run?esc(run.effort==='vendor'?'供应商默认':nativeLabel(run.effort)):'未记录'}</span></div><div><span>设置来源</span><span>${esc(run?.source||'未记录')}</span></div><div><span>助手回答</span><span class="muted">Completed</span></div></div><p class="field-note">同一 Run 的工具循环沿用本轮配置。查看历史不采用今天的默认值补齐旧记录。</p>`;
}
function renderCatalog() {
  const providers=[...new Set(catalogs.map(m=>m.provider))];
  const group=catalogs.filter(m=>m.provider===state.catalogProvider);
  const filtered=group.filter(m=>`${m.name} ${m.code}`.toLowerCase().includes(state.search.toLowerCase()));
  const selected=filtered.slice(state.page*10,state.page*10+10);
  return `<div class="page"><header class="page-title"><div><h1 class="row">${icon('library')}模型目录</h1><p>选择供应商，浏览模型及其能力。目录来自 <strong>models.dev</strong>。</p></div><div class="page-actions"><button class="btn muted-action">${icon('download')}导出</button><button class="btn primary" id="catalogRefresh">${icon('cloud-download')}在线更新</button></div></header><div class="catalog-toolbar"><div class="search">${icon('search')}<input id="catalogSearch" placeholder="搜索当前供应商的模型" value="${esc(state.search)}" aria-label="搜索当前供应商的模型"></div><button class="btn muted-action">全部输入模态${icon('chevron-down')}</button><span class="count">${group.length} 个模型 · 2026/11/07 目录快照</span></div><div class="catalog-layout"><aside class="catalog-providers"><small>供应商</small>${providers.map(p=>{const items=catalogs.filter(m=>m.provider===p);return `<button class="catalog-provider ${p===state.catalogProvider?'active':''}" data-provider="${p}">${logo(p)}<span>${esc(items[0].providerName)}</span><small>${items.length}</small></button>`;}).join('')}</aside><section><h2 class="catalog-supplier-name">${esc(group[0]?.providerName||state.catalogProvider)}</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>模型</th><th>上下文 / 输出</th><th>思考强度</th><th>其他能力</th><th></th></tr></thead><tbody>${selected.map(m=>`<tr><td><strong>${esc(m.name)}</strong><small class="mono">${esc(m.code)}</small></td><td>${formatTokens(m.context)}<small>${formatTokens(m.output)}</small></td><td>${renderNativeControls(m)}</td><td><div class="chips"><span class="tag">${m.input.includes('image')?'图像':'文本'}</span>${m.tools?'<span class="tag">工具</span>':''}${m.structured?'<span class="tag">结构化</span>':''}</div></td><td><button class="icon-btn" data-add-catalog="${esc(m.provider+':'+m.code)}" aria-label="添加 ${esc(m.code)}" title="从目录添加模型">${icon('plus')}</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty-table">没有匹配的模型</td></tr>'}</tbody></table></div><div class="catalog-bottom"><span>原生档位保留英文；开关、预算单独标注。</span><div class="row"><button class="btn compact" data-page-delta="-1" ${state.page===0?'disabled':''}>上一页</button><span>${state.page+1} / ${Math.max(1,Math.ceil(filtered.length/10))}</span><button class="btn compact" data-page-delta="1" ${(state.page+1)*10>=filtered.length?'disabled':''}>下一页</button></div></div><p class="catalog-explainer">目录仅描述支持范围，不设置默认强度。点击行尾 <b>＋</b> 可预览从目录添加模型的弹框。</p></section></div></div>`;
}
function renderNativeControls(model) {
  if (model.reasoning===false) return '<span class="faint">不支持思考</span>';
  if (model.reasoning==null) return '<span class="faint">未提供</span>';
  const values=model.efforts.length?`<div class="native-options">${model.efforts.map(v=>`<span>${esc(v===null?'null':v)}</span>`).join('')}</div>`:'';
  const extra=[model.toggle?'toggle':null,model.budget?(model.budget.min==null&&model.budget.max==null?'budget':`budget ${model.budget.min??'—'}–${model.budget.max??'—'}`):null].filter(Boolean);
  return (values||(!extra.length?'<span class="faint">未列可调项</span>':''))+(extra.length?`<small class="mono">${esc(extra.join(' · '))}</small>`:'');
}
function formatTokens(number) {return number>=1000000?+(number/1000000).toFixed(2)+'M':number>=1024?Math.round(number/1024)+'K':number;}
function renderModels() {
  const providers=['alibaba','openai','deepseek','private'];
  const available=models.filter(m=>m.provider===state.provider);
  const name=available[0]?.providerName||state.provider;
  return `<div class="page"><header class="page-title"><div><h1>模型</h1><p>管理模型供应商、接入凭据及关联模型。</p></div><button class="btn primary muted-action">${icon('plus')}新建供应商</button></header><div class="models-layout"><aside>${providers.map(p=>{const items=models.filter(m=>m.provider===p);return `<button class="provider-card ${p===state.provider?'active':''}" data-model-provider="${p}">${logo(p)}<div style="flex:1"><strong>${esc(items[0]?.providerName||p)}</strong><small>${p==='private'?'自定义接入':esc(items[0]?.providerName||p)}</small></div><span class="faint">${items.length}</span></button>`;}).join('')}</aside><section><div class="provider-detail-top"><div class="row spread"><div class="provider-title">${logo(state.provider)}<h2>${esc(name)}</h2><span class="tag">${icon('key-round')}已配置</span></div><button class="btn muted-action">${icon('pencil')}编辑</button></div><p class="url-line">${icon('link')}<span class="mono">${state.provider==='private'?'https://model.example.com/v1':'模型服务地址'}</span><span class="tag">自定义</span></p></div><div class="subheading"><h3>关联模型</h3><div class="row"><button class="btn" data-view="catalog">${icon('library')}从目录添加</button><button class="btn" id="newPrivateModel">${icon('plus')}新建模型</button></div></div>${available.map(m=>`<div class="model-row" data-edit-model="${esc(m.key)}" role="button" tabindex="0"><button class="switch" aria-label="${esc(m.code)} 已启用" tabindex="-1"></button><div class="model-row-copy"><strong>${esc(m.code)}</strong><small>${m.input.includes('image')?'文本、图像':'文本'}　${formatTokens(m.context)}</small></div><button class="icon-btn" aria-label="编辑 ${esc(m.code)}">${icon('pencil')}</button></div>`).join('')}<p class="field-note" style="margin-top:20px">列表保持简洁；思考能力在添加／编辑模型的弹框中查看。</p></section></div></div>`;
}
function bilingualChips(values) {return values.map(v=>`<span class="chip">${esc(nativeLabel(v))}<code>${esc(v)}</code></span>`).join('');}
function supplierDefaultText(model) {
  if (!model.default) return '供应商默认：具体档位未提供';
  const d=model.default;
  return '供应商默认：'+(d.type==='effort'?`${nativeLabel(d.value)} ${d.value}`:d.type==='toggle'?(d.value?'开启思考':'关闭思考'):`${d.value} Token`);
}
function openModelDialog(model, adding=false) {closeMenu();state.dialogDraft={...copy(model),adding};state.arrayEditing=false;renderModelDialog();$('modelDialog').showModal();}
function renderModelDialog() {
  const m=state.dialogDraft;if(!m)return;
  $('modelDialogTitle').textContent=(m.adding?'添加模型 · ':'编辑模型 · ')+m.code;
  $('modelDialogBody').innerHTML=`<div class="grid2"><label class="field"><span>模型名称</span><input id="modelCode" value="${esc(m.code)}" required></label><label class="field"><span>描述（可选）</span><input id="modelDescription" value="${esc(m.description||'')}"></label></div><div class="row muted small" style="margin:-2px 0 18px">${logo(m.provider)}${esc(m.providerName)}<span class="tag">${m.provider==='private'?'Chat Completions':'原有 API 协议'}</span></div><div class="divider-label"><span>模型能力配置</span><button type="button" class="icon-btn" id="refreshModelCapabilities" aria-label="从模型目录更新能力" title="从模型目录更新能力">${icon('refresh-cw')}</button></div><div class="grid2"><label class="field"><span>上下文窗口 Token</span><input id="modelContext" type="number" value="${m.context}" min="1" required></label><label class="field"><span>最大输出 Token</span><input id="modelOutput" type="number" value="${m.output}" min="1" required></label></div><div class="option-line"><span>输入模态</span><div class="checkboxes">${[['text','文本'],['image','图像'],['audio','音频'],['video','视频']].map(([v,t])=>`<label><input type="checkbox" ${m.input.includes(v)?'checked':''}>${t}</label>`).join('')}</div></div><div class="option-line"><span>能力标签</span><div class="checkboxes"><label><input type="checkbox" ${m.reasoning===true?'checked':''} id="reasoningEnabled">推理</label><label><input type="checkbox" checked>工具</label><label><input type="checkbox" checked>结构化</label><label><input type="checkbox">开放</label></div></div><section class="reasoning-section" aria-label="思考能力"><div class="reasoning-heading"><strong>思考强度档位</strong><span id="reasoningSource">${m.source==='manual'?'手工配置':'来自 models.dev'}</span></div><div class="levels-row"><div class="chips" id="levelChips">${bilingualChips(m.efforts)||`<span class="muted small">${m.reasoning===false?'未声明支持思考':'未配置档位，使用供应商默认'}</span>`}</div><button type="button" class="btn link" id="editLevels">${icon('pencil')}编辑</button></div><div class="array-editor" id="arrayEditor" ${state.arrayEditing?'':'hidden'}><textarea id="levelArray" aria-label="思考强度原生值数组" spellcheck="false" placeholder='["low", "medium", "high"]'>${esc(JSON.stringify(m.efforts))}</textarea><div class="array-actions"><span class="hint">支持 JSON 字符串数组，或英文逗号分隔；按填写顺序展示。</span><button type="button" class="btn compact" id="cancelArray">取消</button><button type="button" class="btn compact" id="applyArray">应用</button></div><p id="arrayError" class="error" role="alert" hidden></p></div><div class="reasoning-extra"><label><input type="checkbox" id="modelToggle" ${m.toggle?'checked':''}>支持显式开启／关闭思考</label>${m.budget?`<span class="muted small">Token 预算：${m.budget.min??'—'}～${m.budget.max??'—'} <span class="tag">只读</span></span>`:''}</div><p class="field-note">这里只声明模型支持的原生值，不设置默认档位。默认强度在 Agent 中配置。</p><div class="supplier-default">${icon('info')}<span>${esc(supplierDefaultText(m))}${m.default?'（目录资料，仅作回显）':''}</span>${m.original?'<button type="button" class="btn link" id="restoreCatalog">恢复目录值</button>':''}</div></section><p id="modelError" class="error" role="alert" hidden></p><details class="advanced"><summary>高级设置</summary><p>${m.provider==='private'?'合并开头的系统消息　　展开工具参数引用':'当前供应商无额外兼容设置。'}</p></details>`;icons();
}
function applyLevels() {
  const input=$('levelArray').value.trim();let values;
  try {
    if(input.startsWith('{'))throw new Error('请使用字符串数组，不是 JSON 对象。');
    values=input.startsWith('[')?JSON.parse(input):input.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean);
    if(!Array.isArray(values)||values.some(v=>typeof v!=='string'||!v.trim()))throw new Error('请填写字符串数组，例如 ["low", "medium", "high"]。');
    const normalized=[...new Set(values.map(v=>v.trim()))];
    state.dialogDraft.code=$('modelCode').value.trim();state.dialogDraft.description=$('modelDescription').value;
    state.dialogDraft.context=Number($('modelContext').value);state.dialogDraft.output=Number($('modelOutput').value);
    state.dialogDraft.efforts=normalized;state.dialogDraft.toggle=$('modelToggle').checked;
    state.dialogDraft.reasoning=normalized.length?true:$('reasoningEnabled').checked;
    state.dialogDraft.source='manual';state.arrayEditing=false;renderModelDialog();
    return true;
  } catch(error) {$('arrayError').textContent=error.message||'格式不正确，请使用字符串数组或逗号分隔。';$('arrayError').hidden=false;return false;}
}
function saveModel(event) {
  event.preventDefault();if(state.arrayEditing&&!applyLevels())return;
  const m=state.dialogDraft;m.code=$('modelCode').value.trim();m.description=$('modelDescription').value;m.context=Number($('modelContext').value);m.output=Number($('modelOutput').value);
  if(m.output>m.context){$('modelError').hidden=false;$('modelError').textContent='最大输出不能超过上下文窗口。';return;}
  if(!$('reasoningEnabled').checked&&(m.efforts.length||$('modelToggle').checked)){$('modelError').hidden=false;$('modelError').textContent='已有档位或开关时，请同时勾选“推理”，或清空思考能力。';return;}
  if(m.toggle!==$('modelToggle').checked||(m.reasoning===true)!==$('reasoningEnabled').checked)m.source='manual';m.toggle=$('modelToggle').checked;m.reasoning=$('reasoningEnabled').checked?true:m.reasoning==null?null:false;
  delete m.adding;const index=models.findIndex(item=>item.key===m.key);if(index>=0)models[index]=copy(m);else models.push(copy(m));
  $('modelDialog').close();state.dialogDraft=null;render();toast('模型能力已保存到原型；未写入 8000。');
}
function renderAgent() {
  state.agentDraft ||= copy(state.agent);const agent=state.agentDraft;const model=findModel(agent.model);
  const invalid=model&&!isValidEffort(model,agent.effort);
  return `<header class="agent-editor-top"><button class="icon-btn muted-action" aria-label="返回">${icon('arrow-left')}</button><h1>编辑 Agent · ${esc(state.agent.name)}</h1><div class="page-actions"><button class="btn" id="cancelAgent">取消</button><button class="btn primary" id="saveAgent">保存</button></div></header><div class="agent-workbench"><section class="agent-fields"><label class="field"><span>名称</span><input id="agentName" value="${esc(agent.name)}"></label><div class="agent-default-grid"><div class="field"><div class="field-heading"><span>默认模型</span><button class="btn link compact" id="clearAgentModel">清空</button></div><button id="agentModelPicker" class="choice" aria-haspopup="menu"><span>${model?logo(model.provider)+esc(model.code):'<span class="faint">请选择</span>'}</span>${icon('chevron-down')}</button></div><div class="field"><span>默认思考强度</span><button class="choice" id="agentEffortPicker" aria-haspopup="menu" ${model?'':'disabled'}><span>${!model?'先选择默认模型':invalid?'档位已失效，请重选':agent.effort==='vendor'?'供应商默认':agent.effort==='on'?'开启思考':agent.effort==='off'?'关闭思考':nativeLabel(agent.effort)}</span>${icon('chevron-down')}</button></div></div><p class="agent-default-note ${invalid?'error':''}">${invalid?'当前档位不在模型支持范围内，请重新选择。':'客户端不传思考参数时，使用这里的默认值。仅对所选默认模型生效。'}</p><label class="field"><span class="field-heading"><span>System Prompt</span><small class="faint">原型示例</small></span><textarea spellcheck="false">你是业务分析助手。根据用户的问题核对数据、说明口径，并标出需要进一步确认的内容。\n\n需要使用工具时，先核对输入和来源；完成后提供可复核的结果。</textarea></label></section><aside class="agent-resources"><nav class="resource-tabs"><span class="active">${icon('hammer')}内置</span><span>${icon('monitor-check')}UI</span><span>${icon('plug')}MCP</span><span>${icon('book-open-check')}Skill</span><span>${icon('braces')}输出</span></nav><div class="resource-item"><input type="checkbox" checked aria-label="程序编排"><div><strong>程序编排</strong><small>通过代码编排已授权工具</small></div><span class="tag">1 工具</span></div><div class="resource-item"><input type="checkbox" checked aria-label="沙箱"><div><strong>沙箱</strong><small>执行 Python 与 Skill 脚本</small></div><span class="tag">1 工具</span></div><div class="resource-item"><input type="checkbox" aria-label="Web"><div><strong>Web</strong><small>网络搜索与网页抓取</small></div><span class="tag">2 工具</span></div><p class="field-note" style="margin-top:24px">资源关联沿用现有配置。思考强度不会改变工具、Skill 或 MCP 的授权范围。</p></aside></div>`;
}
function renderChanges() {return `<div class="page changes"><header class="page-title"><div><h1>思考强度配置 · 改动汇总</h1><p>四个主要界面，加一处历史回显。原型用于评审，尚未接入产品接口。</p></div></header><div class="table-wrap"><table class="data-table"><thead><tr><th>位置</th><th>当前</th><th>拟增加的内容</th></tr></thead><tbody><tr><td>对话输入框</td><td>选择模型</td><td>模型旁增加中文思考选择；跟随 Agent、供应商默认、模型原生档位及实际支持的开关。</td></tr><tr><td>models.dev 目录</td><td>有推理标签</td><td>新增英文原生档位预览；开关、预算和未列可调项分别展示。</td></tr><tr><td>添加／编辑模型</td><td>模态、Token 限制、能力标签</td><td>展示中英文档位；点“编辑”粘贴数组或逗号文本。默认不配置产品档位。</td></tr><tr><td>Agent 配置</td><td>默认模型独占一行</td><td>默认模型在左、默认思考强度在右；客户端不传时自动使用。</td></tr><tr><td>历史／轨迹</td><td>有模型事实，尚无本功能的强度配置</td><td>建议按 Run 记录解析后的选择、设置来源和是否发送参数；旧记录“未记录”，不回填。</td></tr><tr><td>统计与其他客户端</td><td>既有页面与请求</td><td>本轮不改统计页；业务客户端可以不增加选择器，继续使用 Agent 默认。</td></tr></tbody></table></div><h2>选择的优先级</h2><div class="ui-flow"><span>本轮显式指定</span>${icon('chevron-right')}<span>匹配默认模型的 Agent 配置</span>${icon('chevron-right')}<span>供应商默认 · 不传</span></div><p>“跟随 Agent”与“供应商默认”是不同意图。换到其他模型时，不把原模型的 low／max 迁过去；没有目录或能力声明时，默认仍然可用。默认不是关闭思考。</p><h2>手工声明怎么编辑</h2><p>模型弹框只维护一份有序原生值数组。不逐个添加标签，不强制固定五档，也不自动把 max 换成 high。支持以下两种粘贴格式：</p><pre>["low", "medium", "high", "xhigh"]\nlow, medium, high, xhigh</pre><p>目录刷新与手工声明分开。模型弹框“恢复目录值”是显式操作，取消不改配置。新增档位只影响候选范围，不代表私有服务已支持该请求。</p><h2>别家是否保存思考档位</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>项目</th><th>已核验的记录方式</th><th>本次借鉴</th></tr></thead><tbody><tr><td>pi 1.5.0</td><td>Session JSONL 有 thinking_level_change，与 model_change 并列；恢复时读取路径中的选择。</td><td>档位属于可以追溯的会话执行信息。</td></tr><tr><td>DeepSeek Harness</td><td>request/header 保存生效 config.reasoningEffort，并可根据历史重建。</td><td>按本轮保存配置事实，不只展示当前值。</td></tr></tbody></table></div><p style="margin-top:12px"><a href="https://github.com/earendil-works/pi/blob/v1.5.0/packages/coding-agent/src/core/session-manager.ts" target="_blank" rel="noreferrer">pi 来源</a>　<a href="https://github.com/deepseek-ai/deepseek-harness/blob/66491b05e3f05ad35bcdd4f6cd7ed26041d9e3db/packages/core/session/src/request-header.ts" target="_blank" rel="noreferrer">DeepSeek Harness 来源</a></p><h2>本次建议的最小记录</h2><pre>{\n  "model": "qwen3.8-flash",\n  "reasoning": { "mode": "effort", "value": "low" },\n  "reasoning_source": "agent_default"\n}</pre><p>这是语义示意，不是已定稿的接口字段。供应商默认记录 mode=provider_default，不推断实际档位。沿用现有 Run 事件承载轻量事实，不在原型阶段增加表或迁移，也不引入会话永久偏好。</p></div>`;}

document.addEventListener('click',event=>{
  const target=event.target.closest('button,[data-edit-model]');
  if(!event.target.closest('.menu') && target!==menuAnchor) {document.querySelectorAll('dialog .menu').forEach(m=>m.remove());closeMenu();}
  if(!target)return;
  if(target.dataset.view){navigate(target.dataset.view);return;}
  if(target.matches('.muted-action')){toast('本原型保留原有位置，仅演示思考强度相关交互。');return;}
  if(target.dataset.provider){state.catalogProvider=target.dataset.provider;state.search='';state.page=0;render();return;}
  if(target.dataset.modelProvider){state.provider=target.dataset.modelProvider;render();return;}
  if(target.dataset.editModel){openModelDialog(findModel(target.dataset.editModel));return;}
  if(target.dataset.addCatalog){const m=catalogs.find(m=>m.provider+':'+m.code===target.dataset.addCatalog);openModelDialog({...copy(m),key:target.dataset.addCatalog,source:'catalog',original:copy(m)},true);return;}
  if(target.dataset.pageDelta){state.page+=Number(target.dataset.pageDelta);render();return;}
  if(target.dataset.inspect){state.inspecting=target.dataset.inspect;render();return;}
  if(target.dataset.chatMode){state.chatMode=target.dataset.chatMode;render();return;}
  switch(target.id){
    case 'themeButton':document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';break;
    case 'chatModelPicker':showMenu(target,modelChoices(),state.model,key=>{state.model=key;state.effort='inherit';render();},'切换模型后，思考选择恢复为跟随 Agent。');break;
    case 'chatEffortPicker':{const m=findModel(state.model);showMenu(target,effortChoices(m,true),state.effort,value=>{state.effort=value;render();},noEffortHelp(m));break;}
    case 'simulateSend':{const m=findModel(state.model);const r=resolveReasoning(m,state.effort);if(r.error){toast('Agent 默认档位已失效，请重选。');return;}const prompt=$('chatPrompt').value.trim()||'使用本轮配置进行一次原型演示。';const run={model:m.code,provider:m.provider,effort:r.effort,intent:state.effort,source:r.source,request:r.request};state.messages.push({prompt,run,label:r.label});state.lastRun=run;state.inspecting='new';state.effort='inherit';render();toast('已模拟发送；本轮选择已回显，未调用真实模型。');break;}
    case 'catalogRefresh':toast('原型使用 2026-11-07 快照，不更新本地数据库。');break;
    case 'newPrivateModel':{const providerName=models.find(m=>m.provider===state.provider)?.providerName||state.provider;openModelDialog({...copy(models.find(m=>m.provider==='private')),provider:state.provider,providerName,code:'new-model',key:state.provider+':new-'+models.length,adding:true},true);break;}
    case 'closeModelDialog':case 'cancelModelDialog':$('modelDialog').close();break;
    case 'editLevels':state.arrayEditing=true;$('arrayEditor').hidden=false;$('levelArray').focus();break;
    case 'applyArray':applyLevels();break;
    case 'cancelArray':state.arrayEditing=false;$('arrayEditor').hidden=true;break;
    case 'restoreCatalog':{const m=state.dialogDraft;if(m.original){Object.assign(m,{efforts:copy(m.original.efforts),reasoning:m.original.reasoning,toggle:m.original.toggle,budget:copy(m.original.budget),default:copy(m.original.default),source:'catalog'});state.arrayEditing=false;renderModelDialog();}break;}
    case 'refreshModelCapabilities':if(state.dialogDraft.source==='manual')toast('手工思考能力已保留。需要替换时请点“恢复目录值”。');else toast('目录能力已是本次快照；原型未请求在线更新。');break;
    case 'agentModelPicker':showMenu(target,modelChoices(),state.agentDraft.model,key=>{state.agentDraft.name=$('agentName').value;state.agentDraft.model=key;state.agentDraft.effort='vendor';render();toast('默认模型已切换，请为新模型确认默认强度。');});break;
    case 'agentEffortPicker':{const m=findModel(state.agentDraft.model);if(m)showMenu(target,effortChoices(m),state.agentDraft.effort,value=>{state.agentDraft.name=$('agentName').value;state.agentDraft.effort=value;render();},'这是该 Agent 在此默认模型下的默认选择。');break;}
    case 'clearAgentModel':state.agentDraft.model=null;state.agentDraft.effort='vendor';render();break;
    case 'cancelAgent':state.agentDraft=null;render();toast('已取消原型草稿。');break;
    case 'saveAgent':{const a=state.agentDraft;const m=findModel(a.model);if(m&&!isValidEffort(m,a.effort)){toast('请重新选择有效档位。');return;}a.name=$('agentName').value.trim()||state.agent.name;state.agent=copy(a);state.agentDraft=null;render();toast('Agent 默认已保存到原型，对话“跟随 Agent”将使用它。');break;}
  }
});
document.addEventListener('input',event=>{if(event.target.id==='catalogSearch'){state.search=event.target.value;state.page=0;const start=event.target.selectionStart;render();$('catalogSearch').focus();$('catalogSearch').setSelectionRange(start,start);}});
$('modelForm').addEventListener('submit',saveModel);
$('modelDialog').addEventListener('close',()=>{document.querySelectorAll('dialog .menu').forEach(m=>m.remove());closeMenu();state.dialogDraft=null;});
window.addEventListener('hashchange',()=>{const view=location.hash.slice(1);if(['chat','catalog','models','agent','changes'].includes(view)&&view!==state.view){state.view=view;render();}});
window.addEventListener('resize',closeMenu);
window.addEventListener('keydown',event=>{if(event.key==='Escape')closeMenu();});
state.view=['chat','catalog','models','agent','changes'].includes(location.hash.slice(1))?location.hash.slice(1):'chat';render();
