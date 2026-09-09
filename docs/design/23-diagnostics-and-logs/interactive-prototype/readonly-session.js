// Design-only adapter. Uses production projections, trajectory, Markdown and CSS;
// never imports chat.js/workspace.js, which initialize live requests and timers.
import { projectConversationItems } from '/console/conversation-items.js';
import { applyAssistantPresentationActions, processDisclosureLabel, toolResultPresentation } from '/console/assistant-presentation.js';
import { createTrajectoryController } from '/console/trajectory.js';
import { copyTextToClipboard } from '/console/message-actions.js';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
const json = value => `<pre class="inspector-json">${esc(JSON.stringify(value, null, 2))}</pre>`;
const md = value => window.DeepAgentMarkdown?.renderBody(String(value || '')) || `<div class="markdown-body">${esc(value)}</div>`;
const STATUS = {completed:'已完成',failed:'执行失败',waiting:'等待操作',running:'执行中',cancelled:'已取消'};

function answerFor(question, index) {
  if (/费用|预算/.test(question)) return `已按当前口径完成只读对比。\n\n| 项目 | 本期 | 上期 | 变动 |\n| --- | ---: | ---: | ---: |\n| 管理费用 | 128.6 万 | 116.2 万 | +10.7% |\n| 销售费用 | 86.4 万 | 82.1 万 | +5.2% |\n| 财务费用 | 12.8 万 | 13.6 万 | −5.9% |\n\n**主要差异集中在管理费用。** 建议继续查看人员费用、咨询服务与差旅三个科目的明细，核对入账期间和部门归属。\n\n本次仅查询，没有修改模型或业务数据。`;
  if (/同比|环比|指标|公式/.test(question)) return `可以按下面的口径计算：\n\n\`同比 =（本期值 − 去年同期值）/ 去年同期值\`\n\n- **同比**：与去年相同期间比较，用于观察长期变化。\n- **环比**：与紧邻的上一期间比较，用于观察近期变化。\n- 分母为零或缺失时，建议显示“不可比”，不要直接当成 0%。\n\n例如本期收入 120 万、去年同期 100 万，同比增长 **20%**。正式使用前还需确认时间范围和统计口径一致。`;
  if (/数据流|节点/.test(question)) return index ? `可以按数据来源和后续处理方式选择：\n\n| 场景 | 建议节点 |\n| --- | --- |\n| 读取外部数据库 | 数据集查询 |\n| 读取平台数据表 | 数据表查询 |\n| 按维度切片取数 | 财务模型查询 |\n| 查询对象属性 | DeepModel 查询 |\n\n查询节点负责读取；过滤、关联、聚合等处理建议放在后续节点，便于单独检查输入和结果。` : `数据流中的查询节点主要包括以下几类：\n\n- **数据集查询**：读取已配置连接中的业务数据。\n- **数据表查询**：读取平台数据表的记录。\n- **财务模型查询**：按维度和期间查询模型数据。\n- **DeepModel 查询**：读取对象及关联属性。\n- **维度查询**：获取成员、层级和属性。\n\n这些节点的输出可以连接到过滤、关联、聚合等处理节点。选型时先明确数据来源，再确定需要的字段与查询范围。\n\n以上是文档查询结果，本次没有创建或修改数据流。`;
  if (/图片/.test(question)) return `已定位到正文中的图片引用。最后一张正文图片是概览示意图，页脚二维码不属于正文内容。\n\n可以继续在原客户端确认发送操作。诊断中心仅展示会话记录，不代替用户发送消息或文件。`;
  return `已查到与“${question}”相关的说明。\n\n建议按以下顺序检查：\n\n1. **确认范围**：先确定当前应用、对象与用户权限。\n2. **核对配置**：检查相关字段及上下游依赖是否完整。\n3. **验证结果**：使用只读查询验证结果，再决定是否调整。\n\n本次仅阅读和查询，未修改业务数据。若需要继续定位，可补充发生时间、对象编码和具体错误信息。`;
}

// Both projections are derived from one fictional turn specification. No live IDs,
// tokens, customer data or artificial run receipt events are introduced here.
export function createReadonlyFixture(session) {
  const questions = session.questions?.length ? session.questions : [session.title];
  const conversation = [], events = [];
  let sequence = 0;
  // Anchor the last recorded fact to the list/log timestamp. Turns remain 90 s
  // apart, while the latest turn ends at its actual fixture phase.
  const lastOffset = session.status === 'running' ? 1000
    : ['failed','waiting'].includes(session.status) ? 6200 : 9200;
  const base = Date.parse(session.updated) - (questions.length - 1) * 90000 - lastOffset;
  const stamp = offset => new Date(base + offset).toISOString();
  questions.forEach((question, index) => {
    const last = index === questions.length - 1;
    const runId = last ? session.runId || `demo-${session.id.slice(-8)}-${index + 1}` : `demo-${session.id.slice(-8)}-${index + 1}`;
    const time = index * 90000;
    const addEvent = (eventType, parts, offset, status = 'completed', extra = {}) => {
      sequence += 1;
      const event = {event_id:`demo-${session.id}-${sequence}`,event_type:eventType,sequence,run_id:runId,turn_id:`demo-turn-${index + 1}`,status,created_at:stamp(time + offset),updated_at:stamp(time + offset),payload:{message:{kind:eventType==='assistant/message'?'response':'request',parts,model_name:'deepseek-v4.1',timestamp:stamp(time + offset),usage:{input_tokens:2340,output_tokens:426}}},...extra};
      events.push(event);
      return event;
    };
    const addItem = (kind,event,fields) => conversation.push({item_id:`item-${event.event_id}`,kind,order:{sequence:event.sequence,part_offset:0},created_at:event.created_at,updated_at:event.updated_at,status:event.status,...fields});
    const reference = {key:'query_scope',type:'deepfos.query',label:'当前查询范围',schema_version:1,payload:{mode:'readonly',source:'design_fixture'}};
    const user = addEvent('user/message',[{part_kind:'user-prompt',content:index===0?[{kind:'text-content',content:question},{kind:'context-ref',...reference}]:question}],0);
    addItem('user_message',user,{text:question,attachments:[],context_refs:index===0?[reference]:[]});
    const thought = '先确认问题涉及的数据范围，再读取匹配文档。仅获取需要的内容，不执行写入操作。';
    const process = index ? '我再核对一下具体场景与使用限制。' : '我先查询相关资料，确认可用节点和适用范围。';
    const callId = `demo-call-${index + 1}`;
    const toolName = /费用|预算/.test(question) ? 'deepfos_cube_query' : 'deepfos-doc_docs_search';
    const args = {query:question,limit:5};
    const loop = addEvent('assistant/message',[{part_kind:'thinking',content:thought},{part_kind:'text',content:process},{part_kind:'tool-call',tool_name:toolName,tool_call_id:callId,args}],1000);
    addItem('assistant_message',loop,{thinking:thought,text:process,phase:'process'});
    const pending = last && session.status==='running';
    const toolStatus = pending ? 'pending' : 'completed';
    const result = {matches:3,source:'文档中心',items:[{title:question,summary:'找到相关说明及适用范围。'},{title:'配置和权限说明',summary:'查询只读，不修改业务对象。'}]};
    const toolItem = {item_id:`tool-${callId}`,kind:'tool',order:{sequence:loop.sequence,part_offset:3},created_at:loop.created_at,updated_at:pending?loop.updated_at:stamp(time+4600),status:toolStatus,tool_name:toolName,call_id:callId,input:args,output:pending?null:result};
    conversation.push(toolItem);
    if (!pending) addEvent('tool/result',[{part_kind:'tool-return',tool_name:toolName,tool_call_id:callId,content:result}],4600);
    if (last && session.status === 'waiting') {
      const confirmId=`demo-confirm-${index + 1}`;
      const args={title:'请确认操作范围',message:'需要在原客户端确认后才会继续。'};
      const waiting=addEvent('assistant/message',[{part_kind:'text',content:'已整理操作范围，等待原客户端确认。'},{part_kind:'tool-call',tool_name:'builtin__confirm',tool_call_id:confirmId,args,metadata:{widget:'confirm'}}],6200);
      addItem('assistant_message',waiting,{text:'已整理操作范围，等待原客户端确认。',phase:'process'});
      conversation.push({item_id:`tool-${confirmId}`,kind:'tool',order:{sequence:waiting.sequence,part_offset:1},created_at:waiting.created_at,updated_at:waiting.updated_at,status:'awaiting_input',tool_name:'builtin__confirm',call_id:confirmId,input:args,widget:'confirm'});
    } else if (last && session.status === 'failed') {
      addEvent('execution/error',[],6200,'failed',{payload:{error_kind:'provider_timeout',message:'模型请求连接超时，本次执行已结束。',phase:'model_request',run_id:runId}});
    } else if (pending) {
      // Pending state is read-only: no HITL widget, approve, resume or cancel action.
    } else {
      const final = addEvent('assistant/message',[{part_kind:'text',content:last && session.status==='cancelled'?'已完成相关资料的初步查询。':answerFor(question,index)}],9200,last && session.status==='cancelled'?'interrupted':'completed');
      addItem('assistant_message',final,{text:final.payload.message.parts[0].content,phase:'final'});
    }
  });
  return {conversation,events,reference:{key:'query_scope',type:'deepfos.query',label:'当前查询范围',schema_version:1,payload:{mode:'readonly',source:'design_fixture'}}};
}

function fact(label, value, {copy=false,mono=false}={}) {
  return `<div class="wide${mono?' mono':''}"><span>${esc(label)}</span><strong${copy?' class="inspector-copy-value"':''}>${copy?`<span class="inspector-id-text">${esc(value)}</span><button type="button" class="inspector-copy-btn" data-demo-copy="${esc(value)}" title="复制 ${esc(label)}" aria-label="复制 ${esc(label)}">${icon('copy')}</button>`:esc(value)}</strong></div>`;
}

function assistantPresenter(node, host) {
  const row = document.createElement('div');
  row.className = 'msg-row assistant assistant-answer';
  row.innerHTML = `<div class="bubble-wrap assistant-message"><div class="assistant-process"><div class="assistant-process-header"><button type="button" class="assistant-process-summary" aria-expanded="false"><span class="chevron">${icon('chevron-right')}</span><span class="assistant-process-title">${processDisclosureLabel({elapsedSeconds:9})}</span></button></div><div class="assistant-process-body"></div></div><div class="assistant-final markdown"></div><div class="msg-actions"><button type="button" class="msg-copy-btn" data-copy-answer title="复制 Agent 回复" aria-label="复制 Agent 回复">${icon('copy')}</button></div></div>`;
  host.append(row);
  const process = row.querySelector('.assistant-process');
  const body = row.querySelector('.assistant-process-body');
  const output = row.querySelector('.assistant-final');
  const tools = new Map();
  const toggle = (block,button) => button.addEventListener('click',()=>{const open=block.classList.toggle('open');button.setAttribute('aria-expanded',String(open));});
  toggle(process,row.querySelector('.assistant-process-summary'));
  let text = '';
  const presenter = {
    addThinkingBlock(content) {
      const block=document.createElement('div'); block.className='assistant-thinking';
      block.innerHTML=`<button type="button" class="assistant-thinking-head" aria-expanded="false"><span class="thinking-icon">${icon('brain')}</span><span class="thinking-heading"><span class="thinking-title">思考过程</span></span><span class="thinking-chevron">${icon('chevron-right')}</span></button><div class="assistant-thinking-body"><pre class="thinking-content">${esc(content)}</pre></div>`;
      body.append(block);toggle(block,block.querySelector('button'));
    },
    addProcessText(content) {const block=document.createElement('div');block.className='assistant-process-text markdown';block.innerHTML=md(content);body.append(block);},
    setFinalText(content) {text=String(content||'');output.innerHTML=md(text);},
    addToolCall(call) {
      const block=document.createElement('div');block.className='tool-block';
      block.innerHTML=`<button type="button" class="tool-head" aria-expanded="false"><span class="tool-icon">${icon('wrench')}</span><span class="tool-name">${esc(call.display_name||call.tool)}</span><span class="tool-status">等待返回</span><span class="chevron">${icon('chevron-right')}</span></button><div class="tool-body"><div class="label">参数 args</div>${json(call.args)}</div>`;
      body.append(block);tools.set(call.tool_call_id,block);toggle(block,block.querySelector('button'));
    },
    addToolResult(result) {const block=tools.get(result.tool_call_id);if(!block)return;const status=toolResultPresentation(result);block.querySelector('.tool-status').innerHTML=`<span class="${status.className}">${status.label}</span>`;block.querySelector('.tool-body').insertAdjacentHTML('beforeend',`<div class="label">结果 result</div>${json(result.result)}`);},
    finalizeThinking(){},finalizeText(){},
    attachHitlWidget(call){const block=tools.get(call.tool_call_id);if(block)block.querySelector('.tool-status').textContent='等待原客户端操作';row.querySelector('.assistant-process-title').textContent=processDisclosureLabel({pending:true});},
    markInterrupted(){row.querySelector('.bubble-wrap').insertAdjacentHTML('beforeend','<div class="message-run-status interrupted">已停止</div>');},
  };
  applyAssistantPresentationActions(presenter,node.actions);
  if(!text){output.remove();row.querySelector('.msg-actions').remove();}
  else row.querySelector('[data-copy-answer]').addEventListener('click',async(event)=>{const button=event.currentTarget;try{await copyTextToClipboard(text);button.title='已复制';}catch{button.title='请手动复制正文';}});
  return row;
}

export function mountReadonlySession(container, session, {onBack=()=>{},onLogs=()=>{}}={}) {
  const fixture=createReadonlyFixture(session);
  const lastRunEvents=fixture.events.filter(event=>event.run_id===fixture.events.at(-1)?.run_id);
  const requestCount=lastRunEvents.filter(event=>['assistant/message','execution/error'].includes(event.event_type)).length;
  const toolCount=lastRunEvents.flatMap(event=>event.payload?.message?.parts||[]).filter(part=>part.part_kind==='tool-call').length;
  const labels=[['session','概览'],['channel','渠道'],['run','当前'],['context','上下文']];
  let tab='session', referenceSelected=false;
  container.innerHTML=`<style>
    .readonly-session{height:100%;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 300px;grid-template-rows:58px minmax(0,1fr);background:var(--bg);overflow:hidden}
    .readonly-session>.readonly-header{grid-column:1/-1;min-width:0;display:flex;align-items:center;gap:12px;padding:0 18px;border-bottom:1px solid var(--border-soft)}
    .readonly-session .readonly-header .chat-title{flex:1;min-width:0}.readonly-session .readonly-header .chat-title>span:first-child{font-size:14px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .readonly-session .readonly-subtitle{font-size:10px;color:var(--text-faint);margin-top:3px;display:block}
    .readonly-session .readonly-back{display:flex;align-items:center;justify-content:center;width:28px;height:28px;border:0;border-radius:6px;color:var(--text-dim);background:transparent;cursor:pointer}.readonly-session .readonly-back:hover{background:var(--bg-hover)}
    .readonly-session>.chat{min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;border:0;border-radius:0}
    .readonly-session .messages{padding:18px 0 36px;min-height:0}.readonly-session .msg-row{max-width:840px;padding:12px 36px}.readonly-session .assistant-final{font-size:13px}
    .readonly-session>.inspector-pane{display:flex;min-height:0;min-width:0;border-left:1px solid var(--border-soft)!important;background:var(--bg);padding:0;overflow:hidden}
    .readonly-session .inspector-content{width:100%;height:100%;padding:0;overflow:auto}.readonly-session .inspector-tabs{position:sticky;top:0;background:var(--bg);z-index:2}.readonly-session .inspector-tab-panel{padding:0 14px}
    .readonly-session .readonly-lock{display:inline-flex;gap:4px;align-items:center;font-size:11px;color:var(--text-faint);white-space:nowrap}.readonly-session svg{width:14px;height:14px}.readonly-session .trajectory-row{padding-top:7px;padding-bottom:7px}
    .readonly-session .readonly-context{border:1px solid var(--border-soft);background:var(--bg);border-radius:20px;padding:3px 8px;display:flex;gap:5px;align-items:center;font:inherit;font-size:10px;color:var(--text-dim);cursor:pointer}
    .readonly-session .readonly-header .btn{height:28px;min-height:28px;padding:0 9px;font-size:11px}.readonly-session .readonly-notice{max-width:768px;margin:8px auto;padding:0 36px;font-size:12px;color:var(--text-dim)}
    .readonly-session .readonly-note{font-size:11px;color:var(--text-faint);line-height:1.65}.readonly-session .inspector-json{font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere}.readonly-session .inspector-id-text{overflow-wrap:anywhere;word-break:break-all}
    @media(max-width:1000px){.readonly-session{grid-template-columns:minmax(0,1fr) 260px}.readonly-session .msg-row{padding-left:20px;padding-right:20px}.readonly-session .readonly-subtitle{max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  </style><section class="readonly-session">
    <header class="readonly-header"><button type="button" class="readonly-back" data-demo-back title="返回会话" aria-label="返回会话">${icon('arrow-left')}</button><div class="chat-title"><span>${esc(session.title)}</span><span class="readonly-subtitle">${esc(session.user)} · ${esc(session.agent)} · ${esc(session.id)}</span></div><nav class="chat-view-tabs" id="chatViewTabs" role="tablist" aria-label="会话视图"><button type="button" class="chat-view-tab active" id="conversationViewTab" role="tab" aria-selected="true" data-chat-view="conversation">对话</button><button type="button" class="chat-view-tab" id="trajectoryViewTab" role="tab" aria-selected="false" data-chat-view="trajectory">轨迹</button></nav><span class="readonly-lock">${icon('lock-keyhole')}只读</span><button type="button" class="btn" data-demo-logs>${icon('logs')}关联日志</button></header>
    <main class="chat"><div class="messages" id="messages"><div class="date-separator">${esc(new Date(session.updated).toLocaleDateString('zh-CN',{month:'long',day:'numeric'}))}</div></div><section class="trajectory-view hidden" id="trajectoryView"><header class="trajectory-toolbar"><div class="trajectory-modes" role="group" aria-label="轨迹筛选"><button type="button" class="active" data-trajectory-mode="sequence">${icon('clock-3')}<span>顺序</span></button><button type="button" data-trajectory-mode="calls">${icon('wrench')}<span>调用</span><small id="trajectoryCallCount">0</small></button></div><div class="trajectory-actions"><label class="trajectory-search">${icon('search')}<input type="search" id="trajectorySearch" placeholder="搜索轨迹" aria-label="搜索轨迹"></label></div></header><div class="trajectory-layout"><div class="trajectory-list" id="trajectoryList"></div></div></section></main>
    <aside class="inspector-pane" id="inspectorPane"><div class="inspector-content" id="inspectorContent"></div></aside>
  </section>`;
  const host=container.querySelector('#messages');
  for (const node of projectConversationItems(fixture.conversation)) {
    if(node.type==='assistant')assistantPresenter(node,host);
    else {const row=document.createElement('div');row.className='msg-row user';row.innerHTML=`<div class="bubble-wrap"><div class="bubble">${esc(node.text)}</div>${node.contextRefs.length?`<button type="button" class="readonly-context" data-demo-context>${icon('braces')}当前查询范围${icon('info')}</button>`:''}<div class="msg-actions"><button type="button" class="msg-copy-btn" data-demo-copy="${esc(node.text)}" title="复制用户问题" aria-label="复制用户问题">${icon('copy')}</button></div></div>`;host.append(row);}
  }
  if(session.status!=='completed')host.insertAdjacentHTML('beforeend',`<p class="readonly-notice">${esc({failed:'模型请求连接超时，本次执行已结束。可通过关联日志继续排查。',running:'该会话仍在原客户端执行。此原型展示一个固定的只读时刻，不建立实时连接。',waiting:'等待用户在原客户端操作，诊断中心不提供审批或续跑操作。',cancelled:'用户已主动停止此次执行。'}[session.status]||'')}</p>`);
  function renderInspector(){
    if(container.querySelector('main.chat')?.classList.contains('trajectory-active'))return;
    const facts=(rows)=>`<div class="inspector-facts compact">${rows.join('')}</div>`;
    const budget=`<section class="inspector-section inspector-budget-section"><header><strong>上下文预算</strong></header><div class="run-usage-summary"><span><small>历史占用</small><strong>18,420</strong></span><span><small>可用输入</small><strong>981,580</strong></span></div><div class="run-usage-track" role="img" aria-label="示意上下文占用约百分之二"><span class="input" style="width:2%"></span><span class="remaining" style="width:98%"></span></div><div class="run-usage-legend"><span class="input">历史</span><span class="remaining">剩余</span></div></section><section class="inspector-section"><header><strong>上下文压缩</strong><span>未触发</span></header><div class="compaction-comparison"><span><small>压缩前</small><strong>—</strong></span>${icon('arrow-right')}<span><small>压缩后</small><strong>—</strong></span></div></section>`;
    const panes={
      session:`<section class="inspector-section inspector-session-group">${facts([fact('会话 ID',session.id,{copy:true,mono:true}),fact('Agent',session.agent),fact('用户',session.user),fact('模型','DeepSeek v4.1'),fact('会话统计',`${session.questions?.length||1} 回合 · ${fixture.events.length} 事件`)])}<header class="inspector-subheading"><strong>请求 Header</strong></header>${facts([fact('App / Space','demo-app / demo'),fact('Language','zh-cn'),fact('User ID',`demo-user-${session.user}`,{mono:true})])}</section>${budget}`,
      channel:`<section class="inspector-section">${facts([fact('渠道名称',session.channel),fact('会话对象',session.user),fact('Agent',session.agent),fact('会话 ID',session.id,{copy:true,mono:true})])}</section>`,
      run:`<section class="inspector-section"><div class="run-usage-summary"><span><small>本次使用</small><strong>8,432</strong></span><span><small>预计剩余</small><strong>991,568</strong></span></div>${facts([fact('当前状态',STATUS[session.status]||session.status),fact('请求',requestCount),fact('工具调用',toolCount),fact('耗时',`${((session.duration||9200)/1000).toFixed(1)} 秒`),fact('Run ID',session.runId||`demo-${session.id.slice(-8)}-1`,{copy:true,mono:true}),fact('输入 Token','7,812'),fact('输出 Token','620'),fact('缓存读取','4,096'),fact('更新时间',new Date(session.updated).toLocaleString('zh-CN'))])}</section>`,
      context:referenceSelected?`<section class="inspector-section context-reference-inspector"><header><strong>上下文引用</strong></header>${facts([fact('名称','当前查询范围'),fact('类型','deepfos.query'),fact('版本','1')])}${json(fixture.reference)}</section>`:`<div class="inspector-tab-empty">${icon('braces')}<strong>尚未选择上下文引用</strong><span>点击用户消息旁的引用标签，可查看只读 JSON。</span></div>`,
    };
    container.querySelector('#inspectorContent').innerHTML=`<nav class="inspector-tabs" role="tablist" aria-label="会话详情"><span class="inspector-tab-buttons">${labels.map(([key,label])=>`<button type="button" role="tab" data-demo-tab="${key}" aria-selected="${tab===key}">${label}</button>`).join('')}</span></nav><div class="inspector-tab-panel">${panes[tab]}<p class="readonly-note">页面数据为设计示例。</p></div>`;
    window.lucide?.createIcons();
  }
  const previousWorkspace=window.DeepAgentConsoleWorkspace;
  const adapter={renderInspector,openInspectorPane(){}};
  window.DeepAgentConsoleWorkspace=adapter;
  renderInspector();
  const trajectory=createTrajectoryController({state:{sessionId:session.id}});
  trajectory.setHistory({session_id:session.id,items:fixture.events});
  const onClick=async(event)=>{
    if(event.target.closest('[data-demo-back]'))onBack();
    if(event.target.closest('[data-demo-logs]'))onLogs(session.id);
    const tabButton=event.target.closest('[data-demo-tab]');if(tabButton){tab=tabButton.dataset.demoTab;renderInspector();}
    if(event.target.closest('[data-demo-context]')){referenceSelected=true;tab='context';renderInspector();}
    const copy=event.target.closest('[data-demo-copy]');if(copy){try{await copyTextToClipboard(copy.dataset.demoCopy);copy.title='已复制';}catch{copy.title='请手动复制';}}
  };
  container.addEventListener('click',onClick);
  window.lucide?.createIcons();
  return ()=>{container.removeEventListener('click',onClick);if(window.DeepAgentConsoleWorkspace===adapter)window.DeepAgentConsoleWorkspace=previousWorkspace;};
}
