// Review fixtures only. Never reads credentials or calls a business API.
var escapeHtml = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
var formatDate = value => value || '—';
var showDbBanner = () => {};
var showToast = (message) => { const node=document.getElementById('prototypeToast');node.textContent=message;node.hidden=false;clearTimeout(window.prototypeToastTimer);window.prototypeToastTimer=setTimeout(()=>node.hidden=true,2600); };
var prototypeSkills = [
 {code:'fund-operations-report',label:'基金与经营运营报告',description:'查询经营数据，校验口径并生成 HTML 报告。',version:'4.0.1',schema_version:'1.0',source:'upload',runtime_status:'ready',reference_count:2,asset_count:1,script_count:1,updated_at:'2026/10/30 11:20:00',resources:[
  {path:'references/report-guide.md',category:'reference',mime:'text/markdown',size:1800,read_format:'text',readable:true},
  {path:'references/metrics.md',category:'reference',mime:'text/markdown',size:1100,read_format:'text',readable:true},
  {path:'assets/report-template.html',category:'asset',mime:'text/html',size:6200,read_format:'text',readable:true},
  {path:'scripts/render_report.py',category:'script',mime:'text/x-python',size:4200,readable:false}]},
 {code:'ifind-finance-data-http',label:'同花顺 iFinD 金融数据',description:'通过已关联的 MCP 查询金融数据。',version:'0.1.0',schema_version:'1.0',source:'upload',runtime_status:'ready',reference_count:1,asset_count:0,script_count:0,updated_at:'2026/10/28 16:20:00',resources:[]},
 {code:'financial-ratio-draft',label:'财务比率分析草稿',description:'校验财务指标并生成分析文件。',version:'0.2.0',schema_version:'1.0',source:'upload',runtime_status:'ready',reference_count:0,asset_count:0,script_count:1,updated_at:'2026/10/28 16:20:00',resources:[{path:'scripts/analyze.py',category:'script',mime:'text/x-python',size:2300,readable:false}]}
];
var prototypeAgents=[{id:'fund',name:'基金经营报告助手',skills:['fund-operations-report']},{id:'sany',name:'三一Demo助手',skills:['fund-operations-report','ifind-finance-data-http']}];
var state={skills:structuredClone(prototypeSkills)};
var prototypeMarkdown={
 'SKILL.md':'---\nname: fund-operations-report\ndescription: 查询经营数据，校验口径并生成 HTML 报告。\n---\n\n# 基金与经营运营报告\n\n## 工作流程\n\n1. 确认范围，使用当前 Agent 已授权的 MCP 查询数据。\n2. 将查询结果交给 Python 脚本校验、汇总。\n3. 使用模板生成 HTML 报告，返回文件引用。\n\n## 脚本环境\n\n脚本通过 `os.getenv("REPORT_API_URL")` 读取服务地址，通过 `os.getenv("REPORT_API_KEY")` 读取密钥。变量值由管理员在 Skill 详情中配置，不写入包内。\n\n## 输出\n\n正式结构化结果写入 `outputs/result.json`。不在日志或报告中输出凭据。',
 'references/report-guide.md':'# 报告规范\n\n报告包含摘要、指标、图表和口径说明。保留真实来源，不推算缺失数据。',
 'references/metrics.md':'# 指标口径\n\n核对期间、币种及单位，重复坐标必须先处理。',
 'assets/report-template.html':'<!doctype html>\n<html lang="zh-CN">\n  <head><meta charset="utf-8"><title>经营分析报告</title></head>\n  <body><h1>经营分析报告</h1><main>{{ report_body }}</main></body>\n</html>'
};
var apiFetch=async (path,options={})=>{
 if(options.method && options.method!=='GET')throw new Error('设计原型不连接写接口');
 if(path==='/api/v1/skills')return structuredClone(prototypeSkills);
 if(path.endsWith('/references/agents'))return structuredClone(prototypeAgents);
 const match=path.match(/^\/api\/v1\/skills\/([^/]+)(?:\/resources\/(.+))?$/);
 if(!match)throw new Error('原型未实现该接口');
 const skill=prototypeSkills.find(s=>s.code===decodeURIComponent(match[1]));if(!skill)throw new Error('示例 Skill 不存在');
 if(!match[2])return structuredClone(skill);
 const name=decodeURIComponent(match[2]);const content=prototypeMarkdown[name] || '# 参考资料\n\n示例内容。';
 return {content,format:'text',mime:'text/plain',returned_count:content.split('\n').length,has_more:false};
};
window.DeepAgentMarkdown={renderBody:text=>window.DOMPurify.sanitize(window.marked.parse(text)),renderDiagrams:()=>{}};
