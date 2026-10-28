'use strict';
const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
const STORE='deepagent-skill-management-prototype-v1';
const categories=['财务取数','电子表格','分析与报告','通用能力'];
const agents=[{id:'sany',name:'三一Demo助手',desc:'同行财务对标与行业数据查询'},{id:'sheet',name:'电子表格智能助手',desc:'问数、分析与候选填数'},{id:'cube',name:'DeepCube脚本助手',desc:'脚本编写与财务建模'},{id:'fund',name:'基金经营报告助手',desc:'基金经营分析与报告'},{id:'lease',name:'租赁合同助手',desc:'合同核验与财务分析'},{id:'channel',name:'渠道助手',desc:'日常问答与文件交付'},{id:'pipeline',name:'数据流建模助手',desc:'数据流建模与验证'}];
function item(code,label,description,cats,refs,version='1.0.0',reference_count=3,script_count=0,source='upload') {
 return {code,label,description,categories:cats,agents:refs,version,reference_count,script_count,source,updated:'10-28 16:20',editor:'ruge',body:`## 适用范围\n\n${description}\n\n## 工作步骤\n\n1. 确认当前任务、对象与数据范围。\n2. 阅读相关参考资料，使用当前 Agent 已授权的工具。\n3. 核对返回结果的期间、单位、完整性与业务状态。\n4. 简洁说明结果和数据来源，缺失项如实说明。\n\n## 使用边界\n\n本 Skill 提供方法与素材，不自动授予工具权限。不得把示例数据作为真实查询结果。`};
}
const seed=[
 item('deepfos-readonly-analysis','DeepFOS 只读分析','统一只读取数流程，核对业务口径、来源及异常。',['通用能力','财务取数'],['sany','sheet','cube','fund','lease','pipeline'],'1.0.0',2,0,'builtin'),
 item('consolidation-model-analysis','合并财务模型分析','合并范围、抵销口径与财务指标分析方法。',['财务取数'],['sany','sheet'],'1.1.3',4),
 item('deeptable-assistant-workflows','电子表格常规问数与填数','识别选区、核验维度与范围，生成候选填数。',['电子表格'],['sany','sheet'],'1.4.8',5),
 item('deeptable-intelligent-analysis','电子表格智能分析','解释指标变化、比较预算与实际并形成分析依据。',['电子表格','分析与报告'],['sany','sheet'],'2.18.4',5,1),
 item('poc-allocation-trace','分摊过程追溯','按既有分摊规则解释来源、动因与分摊结果。',['财务取数'],['sany','sheet'],'1.0.1',3),
 item('ifind-finance-data-http','同花顺 iFinD 金融数据','通过综合 MCP 查询公司财务、宏观与行业经济数据。',['财务取数'],['sany'],'0.1.0',5),
 item('wind-finance-data-http','万得 Wind 金融数据','通过股票和行业经济 MCP 进行同行对标与指标查询。',['财务取数'],['sany'],'0.1.0',5),
 item('file-reading-verification','文件读取与核验','根据文件格式读取内容，保留来源、页码与完整性。',['通用能力'],['lease','channel','fund'],'1.0.0',1,0,'builtin'),
 item('deepcube-script-authoring','DeepCube 脚本编写','财务模型脚本的语法、成员表达式与安全边界。',['财务取数'],['cube'],'2.2.0',8),
 item('fund-operations-report','基金与经营运营报告','按模板生成经营指标、图表、口径和来源说明。',['分析与报告'],['fund'],'1.3.0',9,2),
 item('fund-cube-query','DeepFOS Cube 取数','为经营报告获取维度明确的 Cube 切片。',['财务取数'],['fund'],'1.2.0',1),
 item('lease-contract-review','租赁合同核验','提取租期、付款计划、利率与合同异常。',['分析与报告'],['lease'],'1.1.0',4,1),
 item('channel-delivery','渠道文件交付','识别渠道能力，交付已有文件并核对发送结果。',['通用能力'],['channel'],'1.0.0',2),
 item('pipeline-modeling','数据流建模流程','按实际节点和依赖关系配置并校验数据流。',['分析与报告'],['pipeline'],'0.8.0',6),
 item('financial-ratio-draft','财务比率分析草稿','对比流动性、盈利能力和偿债能力指标。',['财务取数'],[],'0.2.0',2),
 item('report-html-template','HTML 报告模板','自包含报告的图表样式和布局参考。',['分析与报告'],[],'0.1.0',2,1),
 item('imported-research-notes','研究资料整理','最近导入的研究方法和参考资料，尚未分类。',[],[],'0.1.0',3),
 item('deepagent-diagnostics','DeepAgent 诊断','核对真实工具调用、会话事件与异常。',['通用能力'],[],'1.0.0',2,0,'builtin')
];
let skills;
try { const saved=JSON.parse(localStorage.getItem(STORE)); skills=Array.isArray(saved)&&saved.every(x=>x.code&&Array.isArray(x.agents)&&Array.isArray(x.categories))?saved:structuredClone(seed); } catch {skills=structuredClone(seed);}
const defaults={agents:'sany',categories:'财务取数',shared:'all',all:'all'};
let view='agents', group='sany', query='', selected=null, detailTab='overview', toastTimer, lastListKey='';
const persist=()=>localStorage.setItem(STORE,JSON.stringify(skills));
const createIcons=()=>window.lucide?.createIcons();
const lookup=code=>skills.find(s=>s.code===code);
const agentName=id=>agents.find(a=>a.id===id)?.name||id;
const tabName={agents:'按 Agent',categories:'按用途',shared:'共享 Skill',all:'全部 Skill'};
function toast(s){$('#toast').textContent=s;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,2800);}
function matches(s){return `${s.label} ${s.code} ${s.description} ${s.categories.join(' ')} ${s.agents.map(agentName).join(' ')}`.toLowerCase().includes(query.toLowerCase());}
function inGroup(s,key=group){
 if(view==='agents')return key==='unlinked'?s.agents.length===0:s.agents.includes(key);
 if(view==='categories')return key==='unclassified'?s.categories.length===0:s.categories.includes(key);
 if(view==='shared')return s.agents.length>1&&(key==='all'||key==='two'&&s.agents.length===2||key==='many'&&s.agents.length>=3);
 return key==='all'||key==='upload'&&s.source!=='builtin'||key==='builtin'&&s.source==='builtin'||key==='unlinked'&&s.agents.length===0;
}
function groupData(){
 if(view==='agents')return [...agents.map(a=>({key:a.id,label:a.name,icon:'bot'})),{key:'unlinked',label:'未关联 Agent',icon:'unlink',divider:true}];
 if(view==='categories')return [...categories.map(c=>({key:c,label:c,icon:'folder'})),{key:'unclassified',label:'未分组',icon:'folder-open',divider:true}];
 if(view==='shared')return [{key:'all',label:'全部共享',icon:'share-2'},{key:'two',label:'被 2 个 Agent 引用',icon:'users-round'},{key:'many',label:'被 3 个及以上引用',icon:'users-round'}];
 return [{key:'all',label:'全部 Skill',icon:'library'},{key:'upload',label:'自定义',icon:'package'},{key:'builtin',label:'内置',icon:'boxes'},{key:'unlinked',label:'未关联 Agent',icon:'unlink',divider:true}];
}
function syncRoute(){history.replaceState(null,'',`#${view}/${encodeURIComponent(group)}`);}
function readRoute(){const [v,g]=location.hash.slice(1).split('/');if(v in defaults){view=v;group=g?decodeURIComponent(g):defaults[v];if(!groupData().some(x=>x.key===group))group=defaults[v];}}
function render(){
 const data=groupData();if(!data.some(x=>x.key===group))group=defaults[view];
 $$('.console-segmented [data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));
 $('#groupHeading').textContent=({agents:'Agent',categories:'用途分组',shared:'引用范围',all:'资源目录'})[view];
 $('#groupCount').textContent=view==='agents'?agents.length:view==='categories'?categories.length:'';
 $('#groups').innerHTML=data.map(g=>`${g.divider?'<hr class="nav-divider">':''}<button type="button" class="group-row${group===g.key?' active':''}" data-group="${esc(g.key)}" aria-pressed="${group===g.key}">${icon(g.icon)}<span class="group-name">${esc(g.label)}</span><span class="group-number">${skills.filter(s=>inGroup(s,g.key)).length}</span></button>`).join('');
 const all=skills.filter(s=>inGroup(s));const rows=all.filter(matches);
 const name=data.find(g=>g.key===group)?.label;
 $('#collectionTitle').textContent=view==='agents'&&group!=='unlinked'?name:view==='categories'?name:view==='shared'?'共享 Skill':name;
 $('#collectionCount').textContent=`${rows.length} 个`;
 $('#collectionIcon').setAttribute('data-lucide',view==='agents'&&group!=='unlinked'?'bot':view==='categories'?'folder':view==='shared'?'share-2':'library');
 $('#collectionDescription').textContent=view==='agents'?(group==='unlinked'?'尚未被任何 Agent 引用，可整理后重新关联。':agents.find(a=>a.id===group)?.desc):view==='categories'?'按业务用途组织；同一 Skill 可以出现在多个分组。':view==='shared'?'被多个 Agent 引用的同一份 Skill；内容与版本统一更新。':'全部资源按 Skill 编码去重，不因关联多个 Agent 重复计数。';
 $('#associateBtn').hidden=view!=='agents'||group==='unlinked';
 const shared=all.filter(s=>s.agents.length>1).length;
 $('#listSummary').innerHTML=query?`找到 <strong>${rows.length}</strong> 个匹配结果`:`${all.length} 个 Skill${view==='agents'&&group!=='unlinked'?` · <strong>${shared} 个与其他 Agent 共用</strong>`:view==='all'?` · ${skills.filter(s=>s.agents.length>1).length} 个共享 · ${skills.filter(s=>!s.agents.length).length} 个未关联`:''}`;
 $('#clearSearch').hidden=!query;
 $('#skillRows').innerHTML=rows.map(s=>{
  const ids=[...s.agents].sort((a,b)=>Number(b===group)-Number(a===group));
  return `<tr><td><div class="skill-title-row"><button class="name-button" data-open="${s.code}" title="${esc(s.code)}">${esc(s.label)}</button><span class="version-tag">${esc(s.version)}</span>${s.source==='builtin'?'<span class="source-tag">内置</span>':''}${ids.length>1?`<span class="source-tag shared">共享 ${ids.length}</span>`:''}</div><div class="skill-description">${esc(s.description)}</div></td><td><div class="ref-chips">${ids.length?ids.slice(0,2).map(id=>`<button class="ref-chip${view==='agents'&&id===group?' current':''}" data-go-agent="${id}" title="查看${esc(agentName(id))}关联的Skill">${esc(agentName(id))}</button>`).join(''):'<span class="muted">未关联</span>'}${ids.length>2?`<button class="ref-more" data-refs="${s.code}" aria-label="查看${esc(s.label)}全部${ids.length}个引用Agent">+${ids.length-2}</button>`:''}</div></td><td><div class="resource-count"><span title="参考资料">${icon('book-open-text')}${s.reference_count}</span>${s.script_count?`<span title="脚本">${icon('file-code-2')}${s.script_count}</span>`:''}</div></td><td class="updated">${esc(s.updated)}<small>${esc(s.editor)}</small></td><td><button class="row-more" data-open="${s.code}" aria-label="查看${esc(s.label)}详情">${icon('chevron-right')}</button></td></tr>`;
 }).join('');
 $('#emptyState').hidden=rows.length>0;
 $('#totalNote').textContent=`显示 ${rows.length} / ${all.length} 个 · 全库 ${skills.length} 个独立 Skill`;
 const listKey=`${view}/${group}/${query}`;if(listKey!==lastListKey){$('.table-wrap').scrollTop=0;lastListKey=listKey;}createIcons();syncRoute();
}
function changeView(v,g=defaults[v]){view=v;group=g;closeDrawer();render();}
function openSkill(code,tab='overview'){selected=code;detailTab=tab;renderDrawer();$('#drawer').hidden=false;$('#drawerShade').hidden=false;$('.app-shell').inert=true;$('.drawer-head button').focus();}
function closeDrawer(){const code=selected;$('#drawer').hidden=true;$('#drawerShade').hidden=true;$('.app-shell').inert=false;selected=null;if(code)$(`[data-open="${code}"]`)?.focus();}
function renderDrawer(){
 const s=lookup(selected);if(!s){closeDrawer();return;}
 const readonly=s.source==='builtin';
 $('#drawer').innerHTML=`<header class="drawer-head"><div><h2>${esc(s.label)}</h2><code>${esc(s.code)}</code></div><button class="btn ghost compact icon-only" data-close-drawer aria-label="关闭详情">${icon('x')}</button></header><div class="detail-meta"><span class="version-tag">${esc(s.version)}</span><span>${readonly?'内置':'自定义'}</span><span>${s.reference_count} 份参考 · ${s.script_count} 个脚本</span><span>更新人 ${esc(s.editor)}</span></div><nav class="detail-tabs" aria-label="Skill 详情页签">${[['overview','说明'],['files','资源'],['refs',`引用 Agent（${s.agents.length}）`]].map(([key,text])=>`<button data-detail-tab="${key}" class="${detailTab===key?'active':''}" aria-pressed="${detailTab===key}">${text}</button>`).join('')}</nav><div class="drawer-body" id="drawerBody"></div><footer class="drawer-foot"><div>${readonly?'<span class="muted">内置内容由产品维护</span>':`<button class="btn primary" data-edit>${icon('pencil-line')}编辑${s.agents.length>1?'共享 Skill':''}</button><button class="btn" data-categories>${icon('folder-pen')}分组</button>`}</div><div>${view==='agents'&&s.agents.includes(group)?`<button class="btn ghost" data-unlink="${group}">解除当前关联</button>`:''}</div></footer>`;
 const notice=s.agents.length>1?`<div class="shared-notice">${icon('share-2')}<div>当前由 <strong>${s.agents.length} 个 Agent</strong> 共用。${readonly?'内置内容由产品维护，可在引用页签调整关联。':view==='agents'?'编辑将同步影响这些 Agent；只为当前 Agent 调整时，可复制为独立 Skill。':'编辑将同步影响这些 Agent；单独调整时，可先进入对应 Agent 分组再创建独立副本。'}</div></div>`:'';
 if(detailTab==='overview')$('#drawerBody').innerHTML=`${notice}<div class="tag-list">${s.categories.map(c=>`<span>${esc(c)}</span>`).join('')||'<span>未分组</span>'}</div><p>${esc(s.description)}</p><article>${DOMPurify.sanitize(marked.parse(s.body))}</article><p class="article-note">此处展示可阅读的说明。SKILL.md 的完整定义可在“资源”中查看。</p>`;
 if(detailTab==='files')$('#drawerBody').innerHTML=`<h3>资源文件</h3><button class="btn" data-show-definition>${icon('file-text')}查看 SKILL.md</button>${['references/query-rules.md',...(s.reference_count>1?['references/metrics-and-units.md']:[]),...(s.script_count?['scripts/build_report.py']:[])].map(p=>`<div class="resource-row">${icon(p.endsWith('.py')?'file-code-2':'file-text')}<span>${p}</span><small>示例资源</small><button class="btn compact ghost" data-preview-resource="${esc(p)}">预览</button></div>`).join('')}<p class="muted" style="margin-top:18px;font-size:11px">原型仅展示部分示例文件，数量按示例元数据回显。</p>`;
 if(detailTab==='refs')$('#drawerBody').innerHTML=`${notice}<div style="display:flex;justify-content:space-between;align-items:center"><h3>引用 Agent</h3><button class="btn compact" data-manage-refs>${icon('link')}管理关联</button></div>${s.agents.length?s.agents.map(id=>`<div class="ref-agent-row">${icon('bot')}<div><strong>${esc(agentName(id))}</strong><small>引用同一 Skill · ${esc(s.version)}</small></div><button class="btn ghost compact" data-go-agent="${id}">查看</button><button class="btn ghost compact" data-unlink="${id}">解除关联</button></div>`).join(''):'<p class="muted">尚未被 Agent 引用，Skill 仍保留在资源库中。</p>'}`;
 createIcons();
}
function dialog(title,body,saveLabel,submit,note='仅修改原型示例数据'){
 $('#dialogTitle').textContent=title;$('#dialogBody').innerHTML=body+'<div class="field-error" id="dialogError" role="alert" hidden></div>';$('#dialogSave').textContent=saveLabel;$('#dialogNote').textContent=note;
 $('#actionForm').onsubmit=e=>{e.preventDefault();if($('#actionForm').reportValidity())submit(new FormData($('#actionForm')));};createIcons();$('#actionDialog').showModal();
}
const finish=(text)=>{persist();$('#actionDialog').close();render();if(selected)renderDrawer();toast(text);};
function editSkill(){
 const s=lookup(selected);if(s.source==='builtin')return;
 dialog(`编辑${s.agents.length>1?'共享 ':''}Skill`,`${s.agents.length>1?`<div class="shared-notice">${icon('share-2')}<div>保存后，以下 ${s.agents.length} 个 Agent 将使用更新后的同一份内容：<br><strong>${s.agents.map(id=>esc(agentName(id))).join('、')}</strong></div></div>`:''}<label class="field">名称<input name="label" value="${esc(s.label)}" required maxlength="100"></label><label class="field">用途说明<input name="description" value="${esc(s.description)}" required></label><label class="field">Skill 正文<textarea name="body" required>${esc(s.body)}</textarea></label>${view==='agents'&&s.agents.includes(group)?'<button type="button" class="btn ghost" id="cloneInstead">为当前 Agent 复制一份后编辑</button>':''}`,s.agents.length>1?'保存共享 Skill':'保存',f=>{s.label=f.get('label').trim();s.description=f.get('description').trim();s.body=f.get('body');s.updated='刚刚';s.editor='本地用户';finish(`已更新${s.agents.length>1?'共享 Skill，'+s.agents.length+' 个 Agent 保持引用':' Skill'}`);});
 $('#cloneInstead')?.addEventListener('click',()=>{$('#actionDialog').close();cloneSkill();});
}
function cloneSkill(){
 const s=lookup(selected);const agentId=group;
 dialog('为当前 Agent 创建独立 Skill',`<p>仅替换「${esc(agentName(agentId))}」的关联，其他 Agent 继续使用原 Skill。</p><label class="field">名称<input name="label" value="${esc(s.label)} · 独立版" required></label><label class="field">新编码<input name="code" value="${esc(s.code)}-${agentId}" required pattern="[a-z0-9][a-z0-9-]*"></label><label class="field">Skill 正文<textarea name="body" required>${esc(s.body)}</textarea></label>`,'创建并替换当前关联',f=>{
  const code=f.get('code').trim();if(skills.some(x=>x.code===code)){$('#dialogError').hidden=false;$('#dialogError').textContent='编码已存在，请更换。';return;}
  const clone={...structuredClone(s),code,label:f.get('label').trim(),body:f.get('body'),agents:[agentId],source:'upload',updated:'刚刚',editor:'本地用户'};s.agents=s.agents.filter(id=>id!==agentId);skills.push(clone);selected=code;finish('已创建独立 Skill，仅替换当前 Agent 的关联');
 });
}
function unlink(id){
 const s=lookup(selected);
 dialog('解除 Skill 关联',`<p>将「${esc(s.label)}」从「${esc(agentName(id))}」解除关联。</p><p>${s.agents.length>1?'其他 Agent 的引用和 Skill 内容保持不变。':'Skill 保留在资源库，可在“未关联 Agent”中找到。'}</p>`,'解除关联',()=>{s.agents=s.agents.filter(a=>a!==id);finish('已解除关联，Skill 未删除');});
}
function manageRefs(){
 const s=lookup(selected);
 dialog('管理引用 Agent',`<p>勾选 Agent 关联此 Skill；取消勾选只解除关联，不删除 Skill。</p>${agents.map(a=>`<label class="check-row"><input type="checkbox" name="agents" value="${a.id}" ${s.agents.includes(a.id)?'checked':''}><span>${esc(a.name)}<small>${esc(a.desc)}</small></span></label>`).join('')}`,'保存关联',f=>{s.agents=f.getAll('agents');finish('已更新引用关系，Skill 内容未变');});
}
function associate(){
 const a=group;const available=skills.filter(s=>!s.agents.includes(a));
 dialog(`为${agentName(a)}关联 Skill`,`<p>选择资源库中已有 Skill，共享原内容与版本，不会生成副本。</p><label class="console-search" style="width:100%;margin-bottom:10px">${icon('search')}<input id="associationSearch" type="search" placeholder="搜索名称或编码" aria-label="搜索可关联Skill"></label><div id="associationChoices">${available.map(s=>`<label class="check-row" data-choice="${esc(s.label+' '+s.code)}"><input type="checkbox" name="codes" value="${s.code}"><span>${esc(s.label)}<small>${esc(s.code)}</small></span><small>${s.agents.length?'已被 '+s.agents.length+' 个 Agent 引用':'未关联'}</small></label>`).join('')||'<p>全部 Skill 已关联。</p>'}</div>`,'关联选中 Skill',f=>{const codes=f.getAll('codes');if(!codes.length){$('#dialogError').hidden=false;$('#dialogError').textContent='请至少选择一个 Skill。';return;}codes.forEach(c=>lookup(c).agents.push(a));finish(`已关联 ${codes.length} 个 Skill`);});
 $('#associationSearch').oninput=e=>{$$('[data-choice]').forEach(el=>el.hidden=!el.dataset.choice.toLowerCase().includes(e.target.value.toLowerCase()));};
}
function editCategories(){const s=lookup(selected);dialog('设置用途分组',`<p>分组用于浏览，可以多选；不改变 Agent 引用或 Skill 内容。</p>${categories.map(c=>`<label class="check-row"><input type="checkbox" name="categories" value="${c}" ${s.categories.includes(c)?'checked':''}><span>${c}</span></label>`).join('')}`,'保存分组',f=>{s.categories=f.getAll('categories');finish('已调整用途分组');});}
$('#mainNav').innerHTML=[['测试','message-square'],['Agent','bot'],['集成','puzzle'],['渠道','radio'],['设置',''],['模型','library-big'],['工具','wrench'],['MCP','plug'],['Skill','book-open-check'],['诊断',''],['会话','messages-square'],['用量','chart-no-axes-combined'],['日志','list']].map(([label,i])=>i?`<button type="button" class="nav-button${label==='Skill'?' active':''}" ${label==='Skill'?'':'data-placeholder-nav'}>${icon(i)}<span>${label}</span></button>`:`<div class="nav-section">${label}</div>`).join('');
document.addEventListener('click',e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.view)changeView(b.dataset.view);
 else if(b.dataset.group){group=b.dataset.group;closeDrawer();render();}
 else if(b.dataset.open)openSkill(b.dataset.open);
 else if(b.dataset.refs)openSkill(b.dataset.refs,'refs');
 else if(b.dataset.goAgent)changeView('agents',b.dataset.goAgent);
 else if(b.dataset.detailTab){detailTab=b.dataset.detailTab;renderDrawer();}
 else if(b.hasAttribute('data-close-drawer'))closeDrawer();
 else if(b.hasAttribute('data-close-dialog'))$('#actionDialog').close();
 else if(b.hasAttribute('data-edit'))editSkill();
 else if(b.hasAttribute('data-manage-refs'))manageRefs();
 else if(b.hasAttribute('data-categories'))editCategories();
 else if(b.dataset.unlink)unlink(b.dataset.unlink);
 else if(b.hasAttribute('data-placeholder-nav'))toast('本原型聚焦 Skill 维护，其他页面沿用现有 Console');
 else if(b.hasAttribute('data-show-definition')){
  const s=lookup(selected);$('#drawerBody').innerHTML=`<h3>SKILL.md</h3><pre class="detail-code">${esc(`---\nname: ${s.code}\nversion: ${s.version}\ndescription: ${s.description}\n---\n\n${s.body}`)}</pre>`;
 }else if(b.dataset.previewResource){$('#drawerBody').innerHTML=`<h3>${esc(b.dataset.previewResource)}</h3><p>这是原型中的示例资源预览，真实页面沿用当前文件读取能力。</p><p>查询前确认范围；取值后检查期间、单位、缺失项及来源。</p>`;}
});
$('#drawerShade').onclick=closeDrawer;
document.addEventListener('keydown',e=>{if(e.key==='Tab'&&!$('#actionDialog').open&&!$('#drawer').hidden){const list=$$('button,input,textarea,a[href]', $('#drawer')).filter(x=>!x.disabled&&x.getClientRects().length);if(list.length&&e.shiftKey&&document.activeElement===list[0]){e.preventDefault();list.at(-1).focus();}else if(list.length&&!e.shiftKey&&document.activeElement===list.at(-1)){e.preventDefault();list[0].focus();}}if(e.key==='Escape'&&!$('#actionDialog').open&&!$('#drawer').hidden){e.preventDefault();closeDrawer();}});
$('#actionDialog').addEventListener('click',e=>{if(e.target===$('#actionDialog')){const r=$('#actionDialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#actionDialog').close();}});
$('#search').oninput=e=>{query=e.target.value.trim();render();};$('#clearSearch').onclick=()=>{$('#search').value='';query='';render();};$('#associateBtn').onclick=associate;
$('#resetDemo').onclick=()=>dialog('重置原型示例','<p>恢复初始示例内容、分组和关联关系，不影响真实 Console 数据。</p>','重置示例',()=>{skills=structuredClone(seed);view='agents';group='sany';query='';$('#search').value='';closeDrawer();finish('已恢复初始示例');$('.table-wrap').scrollTop=0;});
$('#themeToggle').onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='light'?'dark':'light';};
$('#installBtn').onclick=()=>{dialog('上传安装 Skill','<p>此处仅演示安装入口，不会上传文件或安装到真实环境。</p><div class="file-drop">'+icon('package-plus')+'<p>选择 Skill ZIP 包</p><input type="file" id="demoFile" accept=".zip" aria-label="选择示例Skill包"></div>','模拟安装',()=>{if(!$('#demoFile').files.length){$('#dialogError').hidden=false;$('#dialogError').textContent='请选择一个 ZIP 文件。';return;}$('#actionDialog').close();toast('已演示安装入口；未上传、未安装真实文件');});};
$('#exportBtn').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({prototype:true,agents,skills},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='DeepAgent-Skill维护-示例数据.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出原型示例数据，不是 Skill 安装包');};
window.addEventListener('hashchange',()=>{readRoute();closeDrawer();render();});
readRoute();render();
