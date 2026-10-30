import { initConsoleSelects } from '/assets/ui-select.js';
import * as SkillFrontmatter from '/assets/skill-frontmatter.js';
window.DeepAgentSkillFrontmatter=SkillFrontmatter;
const admin=window.skillsAdmin;
const dialog=document.getElementById('environmentDialog');
const form=document.getElementById('environmentForm');
const rows=document.getElementById('variableRows');
const error=document.getElementById('environmentError');
// No real secret values are present, and entered sensitive values are discarded on save.
const initialEnvironments={
 'fund-operations-report':[
  {id:'url',name:'REPORT_API_URL',value:'https://reports.example.com',sensitive:false,configured:true},
  {id:'key',name:'REPORT_API_KEY',sensitive:true,configured:true},
  {id:'locale',name:'REPORT_LANGUAGE',value:'zh-CN',sensitive:false,configured:true}]
};
let environments=structuredClone(initialEnvironments), draft=[], editingCode=null;
const denied=/^(PATH|HOME|TMPDIR|TMP|TEMP|SHELL|PYTHON.*|LD_.*|DYLD_.*|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|CODEX_.*|DEEP_AGENT_.*)$/i;
const hasPython=skill=>(skill.resources||[]).some(file=>file.category==='script' && /\.py$/i.test(file.path));
const renderOriginal=admin.renderDetail.bind(admin);
admin.renderDetail=function(skill){
 renderOriginal(skill);
 if(!hasPython(skill))return;
 const button=document.createElement('button');button.type='button';button.className='btn skill-direct-action';button.id='configureSkillEnvironment';
 const count=(environments[skill.code]||[]).length;
 button.innerHTML='<i data-lucide="sliders-horizontal"></i><span>环境变量</span>'+(count?`<span class="environment-count">${count}</span>`:'');
 document.querySelector('#skillDetailPane .skill-detail-actions').prepend(button);
 button.addEventListener('click',()=>openEnvironment(skill));
 window.lucide?.createIcons();
};
admin.handleAction=()=>showToast('设计评审：原有维护操作保持不变');
admin.download=async()=>showToast('设计评审：导出包不包含环境变量');
admin.openInstallDialog=()=>showToast('设计评审：安装包与资产限制保持不变');
function openEnvironment(skill){
 editingCode=skill.code;draft=structuredClone(environments[editingCode]||[]);
 document.getElementById('environmentSkill').textContent=skill.label;
 const count=prototypeAgents.filter(a=>a.skills.includes(editingCode)).length;
 document.getElementById('environmentScope').textContent=count>1?`${count} 个 Agent 共用此配置`:'当前环境的 Skill 配置';
 error.hidden=true;renderRows();dialog.showModal();
 if(draft.length)rows.querySelector('input').focus();else document.getElementById('addVariable').focus();
}
function renderRows(){
 document.getElementById('environmentEmpty').hidden=draft.length>0;
 rows.innerHTML=draft.map((item,i)=>`<div class="environment-row" data-id="${item.id}">
 <input class="env-name" aria-label="变量名 ${i+1}" value="${escapeHtml(item.name)}" placeholder="例如 REPORT_API_KEY" autocomplete="off" spellcheck="false">
 <input class="env-value" aria-label="变量值 ${i+1}" type="${item.sensitive?'password':'text'}" value="${escapeHtml(item.editedValue ?? (item.sensitive?'':item.value||''))}" placeholder="${item.sensitive && item.configured?'已设置，留空不修改':'请输入变量值'}" autocomplete="new-password" spellcheck="false">
 <label class="env-sensitive"><input type="checkbox" aria-label="敏感值 ${i+1}" ${item.sensitive?'checked':''}></label>
 <button type="button" class="btn icon-only remove-variable" aria-label="删除变量 ${i+1}"><i data-lucide="trash-2"></i></button></div>`).join('');
 for(const el of rows.children){
  const item=draft.find(v=>v.id===el.dataset.id);
  el.querySelector('.env-name').addEventListener('input',e=>item.name=e.target.value);
  el.querySelector('.env-value').addEventListener('input',e=>{item.editedValue=e.target.value;if(!item.sensitive)item.value=e.target.value;});
  el.querySelector('.env-sensitive input').addEventListener('change',e=>{
   if(item.sensitive && item.configured && !item.editedValue && !e.target.checked){e.target.checked=true;showToast('已保存的密钥不回显；修改类型前请先填写新值');return;}
   item.sensitive=e.target.checked;el.querySelector('.env-value').type=item.sensitive?'password':'text';
  });
  el.querySelector('.remove-variable').addEventListener('click',()=>{draft=draft.filter(v=>v.id!==item.id);renderRows();});
 }
 window.lucide?.createIcons();
}
document.getElementById('addVariable').addEventListener('click',()=>{
 // Retain unsaved values only while this modal is open.
 for(const el of rows.children){const item=draft.find(v=>v.id===el.dataset.id);item.editedValue=el.querySelector('.env-value').value;}
 draft.push({id:crypto.randomUUID(),name:'',value:'',sensitive:true,configured:false});renderRows();
 for(const el of rows.children){const item=draft.find(v=>v.id===el.dataset.id);if(item.editedValue)el.querySelector('.env-value').value=item.editedValue;}
 rows.lastElementChild.querySelector('input').focus();
});
function closeEnvironment(){draft=[];rows.replaceChildren();form.reset();dialog.close();}
for(const id of ['closeEnvironment','cancelEnvironment'])document.getElementById(id).addEventListener('click',closeEnvironment);
dialog.addEventListener('cancel',e=>{e.preventDefault();closeEnvironment();});
form.addEventListener('submit',e=>{
 e.preventDefault();error.hidden=true;
 const names=new Set();let message='';
 for(const item of draft){
  item.name=item.name.trim();
  if(!/^[A-Za-z_][A-Za-z0-9_]*$/.test(item.name)){message='变量名只能包含字母、数字和下划线，且不能以数字开头。';break;}
  if(names.has(item.name)){message=`变量名 ${item.name} 重复。`;break;}
  if(denied.test(item.name)){message=`${item.name} 是受保护的运行环境变量，不能覆盖。`;break;}
  if(item.sensitive && !item.configured && !item.editedValue){message=`请填写 ${item.name} 的值。`;break;}
  names.add(item.name);
 }
 if(message){error.textContent=message;error.hidden=false;return;}
 environments[editingCode]=draft.map(v=>({id:v.id,name:v.name,sensitive:v.sensitive,configured:true,...(!v.sensitive?{value:v.editedValue??v.value??''}:{})}));
 const skill=prototypeSkills.find(s=>s.code===editingCode);closeEnvironment();admin.renderDetail(skill);showToast('已保存示例配置，下次脚本执行时生效（设计演示）');
});
document.getElementById('themeToggle').addEventListener('click',()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';});
document.getElementById('resetDemo').addEventListener('click',()=>{environments=structuredClone(initialEnvironments);location.hash='#/skill/fund-operations-report';admin.select('fund-operations-report');});
for(const button of document.querySelectorAll('.app-nav nav button'))button.addEventListener('click',()=>{
 if(button.textContent.trim()==='Skill')admin.backToList();else showToast('本原型仅演示 Skill 维护');
});
admin.init();initConsoleSelects();
admin.listLoaded=true;admin.referencesState='ready';admin.agentReferences=prototypeAgents;admin.buildReferenceIndex();admin.renderList();
const initialCode=location.hash.split('/skill/')[1]?.split('/')[0]||'fund-operations-report';
if(initialCode && prototypeSkills.some(s=>s.code===initialCode)){
 await admin.select(initialCode);
 if(new URL(location.href).searchParams.has('env'))openEnvironment(prototypeSkills.find(s=>s.code===initialCode));
}
window.lucide?.createIcons();
