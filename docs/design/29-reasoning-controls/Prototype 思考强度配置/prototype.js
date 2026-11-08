/* Local interaction prototype. No product API calls or persistent configuration writes. */
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = () => window.lucide?.createIcons();
const icon = name => `<i data-lucide="${name}"></i>`;
const copy = value => JSON.parse(JSON.stringify(value));
const labels = {minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'超高',max:'最高'};
const modalityLabels = {text:'文本',image:'图像',audio:'音频',video:'视频'};
const tagLabels = {attachment:'附件',reasoning:'推理',tool_call:'工具',structured_output:'结构化',temperature:'温度',open_weights:'开放',interleaved:'交错'};
const nonLevels = new Set(['default','vendor','inherit','none','off','on','enabled','disabled','auto','toggle','null']);
const isLevel = value => typeof value === 'string' && value.trim() && !nonLevels.has(value.toLowerCase());
const effortLevels = model => (model?.efforts || []).filter(isLevel);
const nativeLabel = value => Object.hasOwn(labels, value) ? labels[value] : value;
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
  agent:{name:'业务分析助手',model:models[0].key,effort:'vendor'},agentDraft:null,dialogDraft:null,arrayEditing:false,
  inspecting:'new',chatMode:'conversation',messages:[],modality:'',
  lastRun:{model:models[0].code},
};
const inspectedRun = () => state.inspecting === 'old' ? null : state.lastRun;
const findModel = key => models.find(m => m.key === key);
const isValidEffort = (model, value) => value === 'vendor' || effortLevels(model).includes(value);
const effortLabel = value => value === 'vendor' ? '默认' : nativeLabel(value);
function resolveReasoning(model, effort, agent = state.agent) {
  const selected = effort !== 'inherit' ? effort : model.key === agent.model ? agent.effort : 'vendor';
  return {effort:selected, error:!isValidEffort(model,selected)};
}
let menuAnchor = null;

function closeMenu(restoreFocus = false) {
  $('menuPortal').replaceChildren();
  if (!menuAnchor) return;
  menuAnchor.setAttribute('aria-expanded', 'false');
  if (restoreFocus) menuAnchor.focus();
  menuAnchor = null;
}

function pickerSelection(kind) {
  if (kind === 'agent') {
    const model = findModel(state.agentDraft.model);
    return {model, effort:state.agentDraft.effort, invalid:model && !isValidEffort(model,state.agentDraft.effort)};
  }
  const model = findModel(state.model);
  const resolved = resolveReasoning(model,state.effort);
  return {model, effort:resolved.effort, invalid:resolved.error};
}

function pickerValue(kind) {
  const {model, effort, invalid} = pickerSelection(kind);
  if (!model) return '<span class="picker-value faint">请选择模型</span>';
  return `<span class="picker-value">${logo(model.provider)}<span class="picker-model-code">${esc(model.code)}</span><span class="picker-effort${invalid?' error':''}">${invalid?'重选档位':esc(effortLabel(effort))}</span></span>`;
}

function modelPicker(kind) {
  const triggerClass = kind === 'agent' ? 'choice' : 'composer-picker';
  return `<button type="button" id="${kind}ModelPicker" class="${triggerClass} combined-picker" aria-haspopup="dialog" aria-expanded="false" aria-controls="modelPickerPanel">${pickerValue(kind)}${icon('chevron-down')}</button>`;
}

function updatePickerTrigger(anchor, kind) {
  anchor.innerHTML = pickerValue(kind) + icon('chevron-down');
  const note = $(`${kind}PickerError`);
  if (note) note.hidden = !pickerSelection(kind).invalid;
  icons();
}

function renderPickerThinking(footer, {model, effort, invalid}) {
  footer.hidden = !model;
  if (!model) return;
  const label = invalid ? '重选档位' : effortLabel(effort);
  const content = `<span>Thinking</span><span class="picker-thinking-value">${esc(label)}</span>`;
  footer.innerHTML = effortLevels(model).length
    ? `<button type="button" class="picker-thinking-row" aria-haspopup="menu" aria-expanded="false" aria-controls="pickerEffortMenu">${content}${icon('chevron-right')}</button>`
    : `<div class="picker-thinking-row">${content}</div>`;
}

function positionPickerEffort(panel, menu) {
  const bounds = panel.getBoundingClientRect();
  const {offsetWidth: width, offsetHeight: height} = menu;
  const margin = 8;
  const gap = 5;
  const right = bounds.right + gap;
  const left = bounds.left - width - gap;
  let x = right;
  let y = bounds.bottom - height;
  if (right + width > window.innerWidth - margin) {
    if (left >= margin) {
      x = left;
    } else {
      x = bounds.right - width;
      const below = bounds.bottom + gap;
      y = below + height <= window.innerHeight - margin
        ? below
        : panel.querySelector('.picker-levels').getBoundingClientRect().top - height - gap;
    }
  }
  menu.style.left = Math.max(margin, Math.min(x, window.innerWidth - width - margin)) + 'px';
  menu.style.top = Math.max(margin, Math.min(y, window.innerHeight - height - margin)) + 'px';
}

function groupedPickerModels() {
  const groups = new Map();
  models.forEach((model, index) => {
    if (!groups.has(model.provider)) groups.set(model.provider, []);
    groups.get(model.provider).push({model, index});
  });
  return [...groups].map(([provider, items]) => {
    const name = items[0].model.providerName;
    const options = items.map(({model, index}) => {
      const modalities = model.input.filter(value => value !== 'text').map(value => `<span class="picker-modality">${esc(modalityLabels[value] || value)}</span>`).join('');
      return `<button type="button" class="menu-item" data-picker-model="${index}"><span class="picker-option-name" title="${esc(model.code)}">${esc(model.code)}</span>${modalities}${icon('check')}</button>`;
    }).join('');
    return `<div role="group" aria-label="${esc(name)}"><div class="picker-provider">${logo(provider)}${esc(name)}</div>${options}</div>`;
  }).join('');
}

function showModelPicker(anchor, kind) {
  if (menuAnchor === anchor) {
    closeMenu();
    return;
  }
  closeMenu();
  menuAnchor = anchor;
  anchor.setAttribute('aria-expanded','true');
  const panel = document.createElement('div');
  panel.id = 'modelPickerPanel';
  panel.className = 'menu model-picker-panel';
  panel.setAttribute('role','dialog');
  panel.setAttribute('aria-label','模型与思考强度');
  panel.innerHTML = `<div class="picker-model-list">${groupedPickerModels()}</div><div class="picker-levels"></div><div id="pickerEffortMenu" class="picker-effort-menu" role="menu" aria-label="思考强度" hidden></div>`;
  $('menuPortal').append(panel);

  const buttons = [...panel.querySelectorAll('[data-picker-model]')];
  const footer = panel.querySelector('.picker-levels');
  const effortMenu = panel.querySelector('.picker-effort-menu');

  const closeEffort = (restoreFocus = false) => {
    effortMenu.hidden = true;
    const trigger = footer.querySelector('button');
    trigger?.setAttribute('aria-expanded','false');
    if (restoreFocus) trigger?.focus({preventScroll:true});
  };
  const openEffort = () => {
    const {model, effort} = pickerSelection(kind);
    const values = ['vendor', ...effortLevels(model)];
    const trigger = footer.querySelector('button');
    if (!trigger) return;
    trigger.setAttribute('aria-expanded','true');
    effortMenu.innerHTML = values.map((value, index) => `<button type="button" role="menuitemradio" aria-checked="${effort===value}" tabindex="-1" data-effort-index="${index}"><span>${esc(effortLabel(value))}</span>${icon('check')}</button>`).join('');
    effortMenu.hidden = false;
    positionPickerEffort(panel, effortMenu);
    icons();
    (effortMenu.querySelector('[aria-checked=true]') || effortMenu.firstElementChild).focus();
  };
  footer.addEventListener('click', event => {
    if (!event.target.closest('button')) return;
    if (effortMenu.hidden) openEffort();
    else closeEffort(true);
  });
  effortMenu.addEventListener('click', event => {
    const option = event.target.closest('[data-effort-index]');
    if (!option) return;
    const {model} = pickerSelection(kind);
    const values = ['vendor', ...effortLevels(model)];
    const value = values[Number(option.dataset.effortIndex)];
    if (kind === 'agent') state.agentDraft.effort = value;
    else state.effort = value;
    updatePickerTrigger(anchor,kind);
    closeMenu(true);
  });

  const updateModel = () => {
    const key = pickerSelection(kind).model?.key;
    buttons.forEach(button => {
      const active = key === models[Number(button.dataset.pickerModel)].key;
      button.classList.toggle('selected',active);
      button.setAttribute('aria-pressed',String(active));
    });
    closeEffort();
    renderPickerThinking(footer,pickerSelection(kind));
    positionModelPicker(panel,anchor);
    icons();
  };
  buttons.forEach(button => button.addEventListener('click', () => {
    const key = models[Number(button.dataset.pickerModel)].key;
    if (key === pickerSelection(kind).model?.key) return;
    if (kind === 'agent') {
      state.agentDraft.model = key;
      state.agentDraft.effort = 'vendor';
      $('clearAgentModel').hidden = false;
    } else {
      state.model = key;
      state.effort = 'inherit';
    }
    updatePickerTrigger(anchor,kind);
    updateModel();
  }));
  panel.addEventListener('keydown', event => {
    const active = document.activeElement;
    const inEffort = effortMenu.contains(active);
    if (!effortMenu.hidden && (event.key === 'Escape' || (inEffort && event.key === 'ArrowLeft'))) {
      event.preventDefault();
      event.stopPropagation();
      closeEffort(true);
      return;
    }
    if (event.key === 'ArrowRight' && active === footer.querySelector('button')) {
      event.preventDefault();
      openEffort();
      return;
    }
    const choices = inEffort ? [...effortMenu.querySelectorAll('button')] : buttons;
    const index = choices.indexOf(active);
    if (index < 0 || !['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const last = choices.length - 1;
    const offset = event.key === 'ArrowDown' ? 1 : -1;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? last : (index + offset + choices.length) % choices.length;
    choices[next].focus();
  });
  updateModel();
  (panel.querySelector('.menu-item.selected') || buttons[0])?.focus({preventScroll:true});
  panel.querySelector('.menu-item.selected')?.scrollIntoView({block:'nearest'});
}

function positionModelPicker(panel, anchor) {
  const rect = anchor.getBoundingClientRect();
  const width = Math.min(Math.max(rect.width,288),360,window.innerWidth-16);
  const topSpace = rect.top-14;
  const bottomSpace = window.innerHeight-rect.bottom-14;
  const openAbove = topSpace > bottomSpace;
  panel.style.width = width+'px';
  panel.style.maxHeight = Math.min(392,Math.max(topSpace,bottomSpace))+'px';
  panel.style.left = Math.max(8,Math.min(rect.right-width,window.innerWidth-width-8))+'px';
  panel.style.top = openAbove ? 'auto' : rect.bottom+6+'px';
  panel.style.bottom = openAbove ? window.innerHeight-rect.top+6+'px' : 'auto';
}
function toast(message) {$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3200);}
function navigate(view) {if($('modelDialog').open)$('modelDialog').close();closeMenu();state.view=view;location.hash=view;render();}
function render() {
  closeMenu();document.querySelectorAll('[data-view]').forEach(btn=>btn.classList.toggle('active',btn.dataset.view===state.view));
  $('mainContent').innerHTML=({chat:renderChat,catalog:renderCatalog,models:renderModels,agent:renderAgent,changes:renderChanges}[state.view]||renderChat)();icons();
}
function renderChat() {
  const resolved = resolveReasoning(findModel(state.model),state.effort);
  const run=inspectedRun();
  const displayModel=run?.model || 'qwen3.8-flash';
  const modelEffort = run?.effort && run.effort !== 'vendor' ? ` · ${esc(effortLabel(run.effort))}` : '';
  return `<div class="chat-workspace">
    <aside class="session-list"><div class="session-agent">${icon('bot')}<strong>${esc(state.agent.name)}</strong><button class="icon-btn muted-action" aria-label="刷新">${icon('refresh-cw')}</button></div><button class="choice muted-action"><span>${icon('layers')}全部应用</span>${icon('chevron-down')}</button><input class="session-search" placeholder="搜索会话名称或 ID" aria-label="搜索会话"><button class="session-card ${state.inspecting==='new'?'active':''}" data-inspect="new"><span>思考强度选择与业务分析</span><small>2 · 刚刚</small></button><button class="session-card ${state.inspecting==='old'?'active':''}" data-inspect="old"><span>旧会话</span><small>2 · 昨天</small></button></aside>
    <section class="chat-center"><header class="chat-header"><strong>思考强度选择与业务分析 <span class="badge-note">演示对话</span></strong><div class="segmented"><button data-chat-mode="conversation" class="${state.chatMode==='conversation'?'active':''}">对话</button><button data-chat-mode="trajectory" class="${state.chatMode==='trajectory'?'active':''}">轨迹</button></div></header>
      <div class="chat-scroll"><div class="chat-conversation">${state.chatMode==='trajectory'?renderTrajectory():`<div class="user-message">请比较本月实际与预算的差异，列出需要进一步核实的项目。</div><div class="assistant-message"><div class="run-caption">${icon('chevron-right')}已处理</div><p>可以按总额、部门和费用项目三个层次核对。先确认实际与预算使用同一期间、组织及币种，再定位差额。</p><ul><li>金额差异：实际金额减预算金额。</li><li>口径核实：检查跨期入账、预算调整与一次性费用。</li><li>待确认内容：数据不完整时保留问题，不直接推断业务原因。</li></ul></div>${state.messages.map(message=>`<div class="user-message" style="margin-top:24px">${esc(message.prompt)}</div><div class="assistant-message"><div class="run-caption">${icon('check')}原型演示</div><p>原型消息已发送。</p></div>`).join('')}`}</div></div>
      <div class="composer-wrap"><div class="composer"><textarea id="chatPrompt" placeholder="输入消息，体验模型和思考强度选择…" aria-label="输入消息"></textarea><div class="composer-footer"><button class="icon-btn muted-action" aria-label="添加附件">${icon('paperclip')}</button><div class="composer-controls">${modelPicker('chat')}<button id="simulateSend" class="send" aria-label="发送原型消息">${icon('send')}</button></div></div></div><p id="chatPickerError" class="composer-note error" ${resolved.error?'':'hidden'}>请重新选择思考档位。</p></div>
    </section>
    <aside class="inspector"><div class="inspector-tabs"><span class="active">概览</span><span>当前</span><span>上下文</span></div><dl class="facts"><dt>会话 ID</dt><dd class="mono">prototype-01</dd><dt>Agent</dt><dd>${esc(state.agent.name)}</dd><dt>模型</dt><dd>${esc(displayModel)}${modelEffort}</dd><dt>App / Space</dt><dd>未关联应用 / 未关联空间<small>— / —</small></dd></dl><div class="mini-divider"></div><h4>上下文预算</h4><div class="row spread"><span class="muted">历史用量</span><span>12,400</span></div><div class="budget-bar"><span></span></div></aside>
  </div>`;
}
function renderTrajectory() {
  const run=inspectedRun();
  return `<div class="row spread" style="margin-bottom:18px"><h3>本轮轨迹</h3><span class="tag">演示记录</span></div><div class="trajectory-mini"><div><span>用户提问</span><span class="muted">Completed</span></div><div><span>模型响应</span><span class="muted">${esc(run?.model||'qwen3.8-flash')}</span></div><div><span>助手回答</span><span class="muted">Completed</span></div></div>`;
}
function renderBadges(values, names) {
  if (!values.length) return '<span class="faint">—</span>';
  return `<div class="chips">${values.map(value => `<span class="tag">${esc(names[value] || value)}</span>`).join('')}</div>`;
}

function renderCatalog() {
  const providers = [...new Set(catalogs.map(m => m.provider))];
  const group = catalogs.filter(m => m.provider === state.catalogProvider);
  const filtered = group.filter(m =>
    `${m.name} ${m.code}`.toLowerCase().includes(state.search.toLowerCase()) &&
    (!state.modality || m.input.includes(state.modality))
  );
  const selected = filtered.slice(state.page*10,state.page*10+10);
  const rows = selected.map(model => `<tr>
    <td><strong>${esc(model.name)}</strong><small class="mono">${esc(model.code)}</small></td>
    <td>${formatTokens(model.context)}</td>
    <td>${formatTokens(model.output)}</td>
    <td>${renderBadges(model.input,modalityLabels)}</td>
    <td>${renderBadges(model.tags,tagLabels)}</td>
    <td>${renderNativeControls(model)}</td>
  </tr>`).join('');
  return `<div class="page">
    <header class="page-title">
      <div><h1 class="row">${icon('library')}模型目录</h1><p>选择供应商，浏览模型及其能力。目录来自 <strong>models.dev</strong>。</p></div>
      <div class="page-actions"><button class="btn muted-action">${icon('download')}导出</button><button class="btn primary" id="catalogRefresh">${icon('cloud-download')}在线更新</button></div>
    </header>
    <div class="catalog-toolbar">
      <div class="search">${icon('search')}<input id="catalogSearch" placeholder="搜索当前供应商的模型" value="${esc(state.search)}" aria-label="搜索当前供应商的模型"></div>
      <select class="btn" id="catalogModality" aria-label="筛选输入模态"><option value="">全部输入模态</option>${Object.entries(modalityLabels).map(([value,label])=>`<option value="${value}" ${value===state.modality?'selected':''}>${label}</option>`).join('')}</select>
      <span class="count">${filtered.length} 个模型 · 2026/11/07 目录快照</span>
    </div>
    <div class="catalog-layout">
      <aside class="catalog-providers"><small>供应商</small>${providers.map(provider => {
        const items = catalogs.filter(m => m.provider === provider);
        return `<button class="catalog-provider ${provider===state.catalogProvider?'active':''}" data-provider="${provider}">${logo(provider)}<span>${esc(items[0].providerName)}</span><small>${items.length}</small></button>`;
      }).join('')}</aside>
      <section>
        <h2 class="catalog-supplier-name">${esc(group[0]?.providerName || state.catalogProvider)}</h2>
        <div class="table-wrap"><table class="data-table catalog-table">
          <thead><tr><th>模型</th><th>上下文</th><th>最大输出</th><th>输入模态</th><th>能力</th><th>思考强度</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="6" class="empty-table">没有匹配的模型</td></tr>'}</tbody>
        </table></div>
        <div class="catalog-bottom"><div class="row"><button class="btn compact" data-page-delta="-1" ${state.page===0?'disabled':''}>上一页</button><span>${state.page+1} / ${Math.max(1,Math.ceil(filtered.length/10))}</span><button class="btn compact" data-page-delta="1" ${(state.page+1)*10>=filtered.length?'disabled':''}>下一页</button></div></div>
      </section>
    </div>
  </div>`;
}

function renderNativeControls(model) {
  const values = effortLevels(model);
  return values.length
    ? `<div class="native-options">${values.map(value=>`<span>${esc(value)}</span>`).join('')}</div>`
    : '<span class="faint">—</span>';
}

function formatTokens(number) {
  if (number >= 1000000) return `${(number/1000000).toFixed(number%1000000===0?0:1)}M`;
  return number >= 1000 ? `${Math.round(number/1000)}K` : number;
}
function renderModels() {
  const providers=['alibaba','openai','deepseek','private'];
  const available=models.filter(m=>m.provider===state.provider);
  const name=available[0]?.providerName||state.provider;
  return `<div class="page"><header class="page-title"><div><h1>模型</h1><p>管理模型供应商、接入凭据及关联模型。</p></div><button class="btn primary muted-action">${icon('plus')}新建供应商</button></header><div class="models-layout"><aside>${providers.map(p=>{const items=models.filter(m=>m.provider===p);return `<button class="provider-card ${p===state.provider?'active':''}" data-model-provider="${p}">${logo(p)}<div style="flex:1"><strong>${esc(items[0]?.providerName||p)}</strong><small>${p==='private'?'自定义接入':esc(items[0]?.providerName||p)}</small></div><span class="faint">${items.length}</span></button>`;}).join('')}</aside><section><div class="provider-detail-top"><div class="row spread"><div class="provider-title">${logo(state.provider)}<h2>${esc(name)}</h2><span class="tag">${icon('key-round')}已配置</span></div><button class="btn muted-action">${icon('pencil')}编辑</button></div><p class="url-line">${icon('link')}<span class="mono">${state.provider==='private'?'https://model.example.com/v1':'模型服务地址'}</span><span class="tag">自定义</span></p></div><div class="subheading"><h3>关联模型</h3><div class="row"><button type="button" class="btn" id="openCatalogAdd">${icon('library')}从目录添加</button><button class="btn" id="newPrivateModel">${icon('plus')}新建模型</button></div></div>${available.map(m=>`<div class="model-row" data-edit-model="${esc(m.key)}" role="button" tabindex="0"><button class="switch" aria-label="${esc(m.code)} 已启用" tabindex="-1"></button><div class="model-row-copy"><strong>${esc(m.code)}</strong><small>${m.input.includes('image')?'文本、图像':'文本'}　${formatTokens(m.context)}</small></div><button class="icon-btn" aria-label="编辑 ${esc(m.code)}">${icon('pencil')}</button></div>`).join('')}<p class="field-note" style="margin-top:20px">列表保持简洁；思考能力在添加／编辑模型的弹框中查看。</p></section></div></div>`;
}
function openCatalogAdd() {
  const provider = state.provider;
  const used = new Set(models.filter(model => model.provider === provider).map(model => model.code));
  const choices = catalogs.flatMap((model, index) => {
    if ((provider !== 'private' && model.provider !== provider) || used.has(model.code)) return [];
    const name = provider === 'private' ? `${model.providerName} · ${model.code}` : model.code;
    return [`<option value="${index}">${esc(name)}</option>`];
  });
  $('catalogAddChoice').innerHTML = choices.join('') || '<option value="">暂无可添加模型</option>';
  $('confirmCatalogAdd').disabled = choices.length === 0;
  $('catalogAddDialog').showModal();
}

function bilingualChips(values) {return values.map(v=>`<span class="chip">${esc(nativeLabel(v))}<code>${esc(v)}</code></span>`).join('');}
function openModelDialog(model, adding=false) {closeMenu();state.dialogDraft={...copy(model),adding};state.arrayEditing=false;renderModelDialog();$('modelDialog').showModal();}
function renderModelDialog() {
  const m=state.dialogDraft;if(!m)return;
  $('modelDialogTitle').textContent=(m.adding?'添加模型 · ':'编辑模型 · ')+m.code;
  $('modelDialogBody').innerHTML=`<div class="grid2"><label class="field"><span>模型名称</span><input id="modelCode" value="${esc(m.code)}" required></label><label class="field"><span>描述（可选）</span><input id="modelDescription" value="${esc(m.description||'')}"></label></div><div class="row muted small" style="margin:-2px 0 18px">${logo(m.provider)}${esc(m.providerName)}<span class="tag">${m.provider==='private'?'Chat Completions':'原有 API 协议'}</span></div><div class="divider-label"><span>模型能力配置</span><button type="button" class="icon-btn" id="refreshModelCapabilities" aria-label="从模型目录更新能力" title="从模型目录更新能力">${icon('refresh-cw')}</button></div><div class="grid2"><label class="field"><span>上下文窗口 Token</span><input id="modelContext" type="number" value="${m.context}" min="1" required></label><label class="field"><span>最大输出 Token</span><input id="modelOutput" type="number" value="${m.output}" min="1" required></label></div><div class="option-line"><span>输入模态</span><div class="checkboxes">${Object.entries(modalityLabels).map(([value,label])=>`<label><input type="checkbox" data-model-input="${value}" ${m.input.includes(value)?'checked':''}>${label}</label>`).join('')}</div></div><div class="option-line"><span>能力标签</span><div class="checkboxes">${Object.entries(tagLabels).map(([value,label])=>`<label><input type="checkbox" data-model-tag="${value}" ${value==='reasoning'?'id="reasoningEnabled"':''} ${(value==='reasoning'?m.reasoning===true:(m.tags||[]).includes(value))?'checked':''}>${label}</label>`).join('')}</div></div><div class="option-line reasoning-section"><span>思考强度档位</span><div class="levels-row" id="levelRow">${levelEditor(m)}</div></div><p id="modelError" class="error" role="alert" hidden></p><details class="advanced"><summary>高级设置</summary><p>${m.provider==='private'?'合并开头的系统消息　　展开工具参数引用':'当前供应商无额外兼容设置。'}</p></details>`;icons();
}
const levelFormat = 'JSON 数组或逗号分隔，例如 low, medium, high';
function levelAction(id, symbol, label) {
  return `<button type="button" class="icon-btn" id="${id}" title="${label}" aria-label="${label}">${icon(symbol)}</button>`;
}
function levelEditor(model) {
  const levels = effortLevels(model);
  if (state.arrayEditing) {
    return `<div class="level-value"><input id="levelArray" type="text" value="${esc(levels.join(', '))}" aria-label="思考强度档位" aria-description="${levelFormat}" title="${levelFormat}" spellcheck="false" placeholder="low, medium, high"><span id="arrayError" class="level-error error" role="alert" hidden></span></div><div class="level-actions">${levelAction('cancelArray','x','取消编辑档位')}${levelAction('applyArray','check','确认档位')}</div>`;
  }
  const restore = model.original && model.source === 'manual' ? levelAction('restoreCatalog','rotate-ccw','恢复目录值') : '';
  return `<div class="chips level-value" id="levelChips" title="${esc(levels.join(', '))}">${bilingualChips(levels)||'<span class="faint">—</span>'}</div><div class="level-actions">${restore}${levelAction('editLevels','pencil','编辑档位')}</div>`;
}
function setLevelEditing(editing) {
  state.arrayEditing = editing;
  $('levelRow').innerHTML = levelEditor(state.dialogDraft);
  icons();
  $(editing ? 'levelArray' : 'editLevels').focus({preventScroll:true});
}
function readModelForm() {
  const m = state.dialogDraft;
  m.code = $('modelCode').value.trim();
  m.description = $('modelDescription').value;
  m.context = Number($('modelContext').value);
  m.output = Number($('modelOutput').value);
  m.input = [...$('modelForm').querySelectorAll('[data-model-input]:checked')].map(input=>input.dataset.modelInput);
  m.tags = [...$('modelForm').querySelectorAll('[data-model-tag]:checked')].map(input=>input.dataset.modelTag);
  m.reasoning = m.tags.includes('reasoning') ? true : m.reasoning==null ? null : false;
  return m;
}

function applyLevels() {
  const input = $('levelArray').value.trim();
  try {
    if (input.startsWith('{')) throw new Error('请使用字符串数组。');
    let values;
    try {
      values = input.startsWith('[') ? JSON.parse(input) : input.split(/[,，]/).map(v=>v.trim()).filter(Boolean);
    } catch {
      throw new Error('JSON 格式不正确，也可以用逗号分隔。');
    }
    if (!Array.isArray(values) || values.some(v=>typeof v!=='string'||!isLevel(v.trim()))) {
      throw new Error('请输入有效档位，例如 low, medium, high。');
    }
    const m = readModelForm();
    m.efforts = [...new Set(values.map(v=>v.trim()))];
    if (m.efforts.length) {
      m.reasoning = true;
      $('reasoningEnabled').checked = true;
    }
    m.source = 'manual';
    setLevelEditing(false);
    return true;
  } catch (error) {
    $('arrayError').textContent = error.message || '档位格式不正确。';
    $('arrayError').hidden = false;
    $('levelArray').setAttribute('aria-invalid','true');
    $('levelArray').setAttribute('aria-describedby','arrayError');
    $('levelArray').focus({preventScroll:true});
    return false;
  }
}

function saveModel(event) {
  event.preventDefault();
  if (state.arrayEditing && !applyLevels()) return;
  const m = readModelForm();
  const error = m.output>m.context ? '最大输出不能超过上下文窗口。' :
    m.reasoning!==true && effortLevels(m).length ? '保存档位前请勾选“推理”。' : '';
  if (error) {
    $('modelError').hidden = false;
    $('modelError').textContent = error;
    return;
  }
  delete m.adding;
  const index = models.findIndex(item=>item.key===m.key);
  if (index>=0) models[index]=copy(m);
  else models.push(copy(m));
  $('modelDialog').close();
  state.dialogDraft = null;
  render();
  toast('模型草稿已保存。');
}
function renderAgent() {
  state.agentDraft ||= copy(state.agent);
  const agent = state.agentDraft;
  const {invalid} = pickerSelection('agent');
  return `<header class="agent-editor-top"><button class="icon-btn muted-action" aria-label="返回">${icon('arrow-left')}</button><h1>编辑 Agent · ${esc(state.agent.name)}</h1><div class="page-actions"><button class="btn" id="cancelAgent">取消</button><button class="btn primary" id="saveAgent">保存</button></div></header><div class="agent-workbench"><section class="agent-fields"><label class="field"><span>名称</span><input id="agentName" value="${esc(agent.name)}"></label><div class="field agent-model-field"><div class="field-heading"><span>默认模型</span><button class="btn link compact" id="clearAgentModel" ${agent.model?'':'hidden'}>清空</button></div>${modelPicker('agent')}</div><p class="agent-default-note error" id="agentPickerError" ${invalid?'':'hidden'}>请重新选择思考档位。</p><label class="field"><span class="field-heading"><span>System Prompt</span><small class="faint">原型示例</small></span><textarea spellcheck="false">你是业务分析助手。根据用户的问题核对数据、说明口径，并标出需要进一步确认的内容。\n\n需要使用工具时，先核对输入和来源；完成后提供可复核的结果。</textarea></label></section><aside class="agent-resources"><nav class="resource-tabs"><span class="active">${icon('hammer')}内置</span><span>${icon('monitor-check')}UI</span><span>${icon('plug')}MCP</span><span>${icon('book-open-check')}Skill</span><span>${icon('braces')}输出</span></nav><div class="resource-item"><input type="checkbox" checked aria-label="程序编排"><div><strong>程序编排</strong><small>通过代码编排已授权工具</small></div><span class="tag">1 工具</span></div><div class="resource-item"><input type="checkbox" checked aria-label="沙箱"><div><strong>沙箱</strong><small>执行 Python 与 Skill 脚本</small></div><span class="tag">1 工具</span></div><div class="resource-item"><input type="checkbox" aria-label="Web"><div><strong>Web</strong><small>网络搜索与网页抓取</small></div><span class="tag">2 工具</span></div><p class="field-note" style="margin-top:24px">资源关联沿用现有配置。思考强度不会改变工具、Skill 或 MCP 的授权范围。</p></aside></div>`;
}
function renderChanges() {
  return `<div class="page changes">
    <header class="page-title"><div><h1>思考强度配置 · 改动汇总</h1><p>交互原型</p></div></header>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>位置</th><th>原型交互</th></tr></thead><tbody>
      <tr><td>对话输入框</td><td>按供应商分组选择模型，点击底部 Thinking 打开侧边档位菜单。</td></tr>
      <tr><td>模型目录</td><td>沿用 Console 的五列，追加只读“思考强度”列。</td></tr>
      <tr><td>添加／编辑模型</td><td>点铅笔在原行编辑，支持粘贴数组或逗号分隔的档位。</td></tr>
      <tr><td>Agent 配置</td><td>共用模型与档位选择框，保存后用于新对话。</td></tr>
    </tbody></table></div>
  </div>`;
}
document.addEventListener('click',event=>{
  const target=event.target.closest('button,[data-edit-model]');
  if (!event.target.closest('.model-picker-panel') && target !== menuAnchor) closeMenu();
  if(!target)return;
  if(target.dataset.view){navigate(target.dataset.view);return;}
  if(target.matches('.muted-action')){toast('本原型保留原有位置，仅演示思考强度相关交互。');return;}
  if(target.dataset.provider){state.catalogProvider=target.dataset.provider;state.search='';state.page=0;render();return;}
  if(target.dataset.modelProvider){state.provider=target.dataset.modelProvider;render();return;}
  if(target.dataset.editModel){openModelDialog(findModel(target.dataset.editModel));return;}
  if(target.dataset.pageDelta){state.page+=Number(target.dataset.pageDelta);render();return;}
  if(target.dataset.inspect){state.inspecting=target.dataset.inspect;render();return;}
  if(target.dataset.chatMode){state.chatMode=target.dataset.chatMode;render();return;}
  switch(target.id){
    case 'themeButton':document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';break;
    case 'chatModelPicker':showModelPicker(target,'chat');break;
    case 'simulateSend':{
      const model = findModel(state.model);
      const {effort, error} = resolveReasoning(model,state.effort);
      if (error) {
        toast('Agent 默认档位已失效，请重选。');
        return;
      }
      const prompt = $('chatPrompt').value.trim() || '使用所选模型进行一次原型演示。';
      state.messages.push({prompt});
      state.lastRun = {model:model.code, effort};
      state.inspecting = 'new';
      state.effort = 'inherit';
      render();
      toast('已模拟发送。');
      break;
    }
    case 'openCatalogAdd':openCatalogAdd();break;
    case 'cancelCatalogAdd':$('catalogAddDialog').close();break;
    case 'catalogRefresh':toast('原型使用 2026-11-07 快照，不更新本地数据库。');break;
    case 'newPrivateModel':{const providerName=models.find(m=>m.provider===state.provider)?.providerName||state.provider;openModelDialog({...copy(models.find(m=>m.provider==='private')),provider:state.provider,providerName,code:'new-model',key:state.provider+':new-'+models.length,adding:true},true);break;}
    case 'closeModelDialog':case 'cancelModelDialog':$('modelDialog').close();break;
    case 'editLevels':setLevelEditing(true);break;
    case 'applyArray':applyLevels();break;
    case 'cancelArray':setLevelEditing(false);break;
    case 'restoreCatalog':{const m=readModelForm();if(m.original){Object.assign(m,{efforts:copy(m.original.efforts),reasoning:m.original.reasoning,toggle:m.original.toggle,budget:copy(m.original.budget),default:copy(m.original.default),source:'catalog'});$('reasoningEnabled').checked=m.reasoning===true;setLevelEditing(false);}break;}
    case 'refreshModelCapabilities':if(state.dialogDraft.source==='manual')toast('手工思考能力已保留。需要替换时请点“恢复目录值”。');else toast('目录能力已是本次快照；原型未请求在线更新。');break;
    case 'agentModelPicker':showModelPicker(target,'agent');break;
    case 'clearAgentModel':state.agentDraft.model=null;state.agentDraft.effort='vendor';target.hidden=true;updatePickerTrigger($('agentModelPicker'),'agent');$('agentModelPicker').focus();break;
    case 'cancelAgent':state.agentDraft=null;render();toast('已取消原型草稿。');break;
    case 'saveAgent':{const a=state.agentDraft;const m=findModel(a.model);if(m&&!isValidEffort(m,a.effort)){toast('请重新选择有效档位。');return;}a.name=$('agentName').value.trim()||state.agent.name;state.agent=copy(a);state.agentDraft=null;state.model=a.model||models[0].key;state.effort='inherit';render();toast('Agent 默认模型和档位已保存。');break;}
  }
});
$('catalogAddForm').addEventListener('submit', event => {
  event.preventDefault();
  const index = $('catalogAddChoice').value;
  if (index === '') return;
  const catalog = catalogs[Number(index)];
  const provider = state.provider;
  const providerName = models.find(model => model.provider === provider)?.providerName || provider;
  $('catalogAddDialog').close();
  openModelDialog({
    ...catalog, provider, providerName, key:`${provider}:${catalog.code}`,
    source:'catalog', original:catalog
  }, true);
});

$('modelForm').addEventListener('keydown',event=>{
  if (!state.arrayEditing || event.isComposing || !event.target.closest('#levelRow')) return;
  if (event.key === 'Escape' || (event.key === 'Enter' && event.target.id === 'levelArray')) {
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Escape') setLevelEditing(false);
    else applyLevels();
  }
});
document.addEventListener('input',event=>{
  if (event.target.id === 'levelArray' && event.target.hasAttribute('aria-invalid')) {
    event.target.removeAttribute('aria-invalid');
    event.target.removeAttribute('aria-describedby');
    $('arrayError').hidden = true;
  }
  if(event.target.id==='catalogSearch'){state.search=event.target.value;state.page=0;const start=event.target.selectionStart;render();$('catalogSearch').focus();$('catalogSearch').setSelectionRange(start,start);}});
document.addEventListener('change',event=>{
  if (event.target.id==='catalogModality') {
    state.modality=event.target.value;
    state.page=0;
    render();
    $('catalogModality').focus();
  }
});
$('modelForm').addEventListener('submit',saveModel);
$('modelDialog').addEventListener('close',()=>{document.querySelectorAll('dialog .menu').forEach(m=>m.remove());closeMenu();state.dialogDraft=null;});
window.addEventListener('hashchange',()=>{const view=location.hash.slice(1);if(['chat','catalog','models','agent','changes'].includes(view)&&view!==state.view){state.view=view;render();}});
window.addEventListener('resize',()=>closeMenu());
window.addEventListener('keydown',event=>{if(event.key==='Escape')closeMenu(true);});
state.view=['chat','catalog','models','agent','changes'].includes(location.hash.slice(1))?location.hash.slice(1):'chat';render();
