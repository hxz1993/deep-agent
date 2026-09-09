// Deterministic fictional fixtures. No business API, logging source, or credentials.
export const NOW = Date.parse('2026-09-09T15:00:00+08:00');
export const agents = ['企微助手', 'DeepCube 助手', '电子表格助手', '工单助手'];
export const users = ['陈同学', '林同学', '赵同学', '王同学', '周同学', '许同学', '吴同学', '李同学'];
export const statuses = {completed:'完成',failed:'失败',waiting:'等待操作',running:'进行中',cancelled:'已取消'};
const topics = [
  ['数据流查询节点咨询','数据流有哪些查询节点？','这些节点分别适合什么场景？'],
  ['月度费用变动分析','对比 8 月与 7 月的管理费用','请列出变动最大的三个费用科目。'],
  ['同比公式咨询','同比公式怎么写？','如果去年同期为零，应该怎样处理？'],
  ['文档图片获取','文档里的最后一张图片发给我','请确认是正文图片，不是页脚二维码。'],
  ['数据集权限设置','数据集权限如何设置？','只开放查询权限应该怎么做？'],
  ['表格数据汇总','多个表格如何快速汇总？','请先给出一个只读汇总方案。'],
  ['工单流转规则','工单如何设置自动流转？','哪些情况需要人工确认？'],
  ['预算执行查询','本月预算执行情况怎么样？','按部门查看预算差异。'],
  ['数据同步时间','数据同步需要多久？','有什么方式可以查看同步进度？'],
  ['报表公式检查','帮我检查报表里的公式','先列出有问题的单元格，不修改。'],
  ['登录异常处理','登录失败该如何处理？','在哪里能找到登录失败的原因？'],
  ['指标口径说明','收入同比和环比有什么区别？','用一个简单的例子说明。'],
];
export const sessions = Array.from({length:56}, (_, i) => {
  const topic = topics[i % topics.length];
  const status = i === 1 || i % 17 === 6 ? 'failed' : i === 3 || i === 9 ? 'waiting' : i === 4 ? 'running' : i % 19 === 12 ? 'cancelled' : 'completed';
  const questions = i % 3 === 0 ? [topic[1],topic[2]] : [topic[1]];
  return {id:`d1a69009${(i+1).toString(16).padStart(24,'0')}`,runId:`r-demo-${String(i+1).padStart(4,'0')}`,
    title:topic[0],agent:agents[i%4],user:users[(i*3)%8],channel:i%3===0?'API':'企微',status,
    updated:new Date(NOW-i*2.8*3600000).toISOString(),questions,longWait:i===4||i===1||i===23,
    duration:i===1?60000:1800+(i*1771)%54000};
});
export function filterSessions(filters={}) {
  const needle=(filters.q||'').trim().toLowerCase();
  const fullId=/^[a-f0-9]{32}$/i.test(needle);
  return sessions.filter(s => (!filters.agent||s.agent===filters.agent)
    &&(!filters.user||s.user===filters.user)&&(!filters.channel||s.channel===filters.channel)
    &&(!filters.status||s.status===filters.status)&&(!filters.wait||s.longWait)
    &&(!filters.days||Date.parse(s.updated)>=NOW-Number(filters.days)*86400000)
    &&(!needle||(fullId?s.id.toLowerCase()===needle:s.title.toLowerCase().includes(needle))));
}
export function aggregate(items) {
  return {sessions:items.length,questions:items.reduce((n,s)=>n+s.questions.length,0),
    users:new Set(items.map(s=>s.user)).size,failed:items.filter(s=>s.status==='failed').length,
    waiting:items.filter(s=>s.status==='waiting').length,longWait:items.filter(s=>s.longWait).length};
}
export function questionRows(items) {
  return items.flatMap(s=>s.questions.map((text,i)=>({session:s,text,at:new Date(Date.parse(s.updated)-(s.questions.length-1-i)*120000).toISOString()})))
    .sort((a,b)=>b.at.localeCompare(a.at));
}
const logSteps=[
  ['INFO','网关','channel.received','收到渠道消息','接收'],
  ['INFO','网关','channel.dispatched','消息已分发至 Agent','分发'],
  ['INFO','API','agent.started','Agent 开始执行','执行'],
  ['INFO','API','model.started','开始模型请求','模型'],
  ['INFO','API','model.completed','模型请求完成 · 1.8 秒','模型'],
  ['INFO','API','tool.started','开始调用 docs_search','工具'],
  ['INFO','API','tool.completed','docs_search 完成 · 842 毫秒','工具'],
  ['INFO','API','model.started','开始模型请求','模型'],
  ['INFO','API','model.completed','模型请求完成 · 2.1 秒','模型'],
  ['INFO','API','agent.completed','Agent 执行完成','执行'],
  ['INFO','网关','channel.delivered','最终答案投递完成','投递'],
];
export const logs=sessions.slice(0,20).flatMap((s,si)=>{
  let steps=logSteps.map(v=>[...v]);
  if(s.status==='failed')steps=[...steps.slice(0,8),['WARN','API','model.waiting','模型仍在等待 · 30 秒','模型'],['ERROR','API','model.failed','模型请求失败：连接超时（provider_timeout）','模型']];
  if(s.status==='waiting')steps=[...steps.slice(0,7),['INFO','API','agent.waiting','等待用户在控制台操作','人工操作']];
  if(s.status==='running')steps=[...steps.slice(0,6),['WARN','API','tool.waiting','工具仍在等待 · 30 秒','工具']];
  if(s.status==='cancelled')steps=[...steps.slice(0,7),['INFO','API','agent.cancelled','用户主动停止执行','执行']];
  if(s.channel==='API')steps=steps.filter(v=>v[1]!=='网关');
  if(/费用|预算/.test(s.questions.at(-1)))steps=steps.map(v=>v.map(x=>x.replace('docs_search','deepfos_cube_query')));
  return steps.map(([level,source,event,summary,phase],i)=>({id:`log-${si}-${i}`,level,source,event,summary,phase,
    time:new Date(Date.parse(s.updated)-(steps.length-1-i)*3300).toISOString(),sessionId:s.id,runId:s.runId,
    duration:level==='ERROR'?60000:event.endsWith('waiting')?30000:undefined,errorKind:level==='ERROR'?'provider_timeout':undefined,
    instance:'local-demo-01',service:source==='API'?'deep-agent-api':'wecom-gateway',
    reason:level==='ERROR'?'向模型服务发起请求时，未能在连接时限内建立连接。该次执行已结束，可结合前后日志检查 Provider 与网络状态。':event.endsWith('waiting')?'当前阶段尚未返回。此记录仅报告等待时长，不代表已经卡死，也不会触发重试或取消。':'该记录仅保留安全诊断字段，不包含对话正文、工具参数或工具结果。'}));
}).sort((a,b)=>b.time.localeCompare(a.time));
export function filterLogs(f={}) {
  const needle=(f.q||'').trim().toLowerCase();
  return logs.filter(l=>(!f.source||l.source===f.source)&&(!f.level||l.level===f.level)
    &&(!f.sessionId||l.sessionId===f.sessionId.trim())&&(!f.runId||l.runId===f.runId.trim())
    &&(!f.hours||Date.parse(l.time)>=NOW-Number(f.hours)*3600000)
    &&(!needle||[l.summary,l.event,l.errorKind,l.phase].join(' ').toLowerCase().includes(needle)));
}
export function time(value,day=false) {
  const date=new Date(value);
  return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:day?'2-digit':undefined,day:day?'2-digit':undefined,hour:'2-digit',minute:'2-digit',second:day?undefined:'2-digit',hour12:false}).format(date);
}
export function escapeHTML(value='') {return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
