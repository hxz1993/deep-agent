"""Isolated real-model A/B; reads local credentials, never writes configuration.
Run: PYTHONPATH=src .venv/bin/python 'docs/reports/Report Skill选择提示验证/evaluate.py'
Artifacts contain synthetic tasks only. No production files/tools are used.
"""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI
from pydantic_ai import FunctionToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness.skills import Skills
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.factory import build_agent
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.skills.instructions import SKILL_SELECTION_INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/local-artifacts/TMP to delete Skill选择AB'
SKILLS = {
 'lease-check': ('租赁合同金额、日期及付款条款核对。', '先读取合同。编号或金额缺失返回未知，不猜测。付款条款按原文解释。references/history.md仅讨论历史口径，本次普通合同无需读取。'),
 'bom-check': ('Excel BOM成本核对，包括数量、单价、公式缓存及成本口径。', '先读取BOM。含税与未税换算需先读references/tax.md；其他问题无需此资料。不要把空白单价当成0。references/images.md仅在有嵌入图片时读取。'),
 'travel-guide': ('旅游路线和景点推荐。', '先确认目的地与天数。'),
}
CASES = [
 {'id':'lease','prompt':'核对附件lease中的合同编号、月租金和付款条件，简短回答。','skills':['lease-check'],'files':['lease'],'refs':[],'checks':['未知','12000']},
 {'id':'bom','prompt':'核对附件bom的成本：含税单价为113元，按该成本流程的税率换算未税单价；空白单价的物料怎样处理？','skills':['bom-check'],'files':['bom'],'refs':['references/tax.md'],'checks':['100','未知']},
 {'id':'both','prompt':'分别核对lease的合同编号与月租金，以及bom的空白单价，不需要税额换算。简短回答。','skills':['lease-check','bom-check'],'files':['lease','bom'],'refs':[],'checks':['未知','12000']},
 {'id':'unrelated','prompt':'把“早上好”翻译成英文，只回答译文。','skills':[],'files':[],'refs':[],'checks':['good morning']},
]

def score(messages, case):
 active=set();calls=[];order_ok=True;refs=[];files=[];loaded=[];pending={}
 for message in messages:
  for part in message.get('parts',[]):
   kind=part.get('part_kind',''); name=part.get('tool_name')
   if kind=='tool-call':
    args=part.get('args') or {};args=json.loads(args) if isinstance(args,str) else args
    if name=='load_capability':
     code=args.get('id');pending[part.get('tool_call_id')]=code;calls.append(['load',code]);loaded.append(code)
    else:
     calls.append([name,args])
     if name=='read_file':
      resource=args.get('resource','');required={'lease':'lease-check','bom':'bom-check','references/tax.md':'bom-check'}.get(resource)
      if required and required not in active:order_ok=False
      (refs if resource.startswith('references/') else files).append(resource)
   elif kind=='tool-return' and name=='load_capability' and part.get('outcome')=='success':
    active.add(pending.get(part.get('tool_call_id')))
 output='\n'.join(p.get('content','') for p in messages[-1].get('parts',[]) if p.get('part_kind')=='text')
 selected_ok=set(loaded)==set(case['skills']) and len(loaded)==len(set(loaded))
 reference_ok=set(refs)==set(case['refs']);read_ok=set(files)==set(case['files'])
 answer_ok=all(x.lower() in output.lower().replace(',', '').replace('，', '') for x in case['checks'])
 return dict(calls=calls,loaded=loaded,selection_ok=selected_ok,order_ok=order_ok,reference_ok=reference_ok,read_ok=read_ok,answer_ok=answer_ok,passed=all((selected_ok,order_ok,reference_ok,read_ok,answer_ok)),output=output)

async def main():
 OUT.mkdir(parents=True, exist_ok=True)
 with sqlite3.connect(f'file:{ROOT / "data/deep-agent-local.db"}?mode=ro', uri=True) as db:
  db.row_factory=sqlite3.Row
  row=dict(db.execute("SELECT m.model,p.api_key,p.base_url,p.use_default_base_url FROM llm_models m JOIN llm_providers p ON p.id=m.provider_id WHERE p.provider_type='deepseek' AND m.model='deepseek-flash' AND m.enabled=1 LIMIT 1").fetchone())
 async with httpx.AsyncClient(timeout=120) as http:
  provider=DeepSeekProvider(openai_client=AsyncOpenAI(api_key=row['api_key'],base_url='https://api.deepseek.com' if row['use_default_base_url'] else row['base_url'],http_client=http))
  model=OpenAIChatModel(row['model'],provider=provider)
  with tempfile.TemporaryDirectory(prefix='TMP_to_delete-skill-ab-') as temp:
   for code,(desc,body) in SKILLS.items():
    folder=Path(temp)/code;folder.mkdir()
    (folder/'SKILL.md').write_text(f'---\nname: {code}\ndescription: {desc}\n---\n{body}\n')
   gate=asyncio.Semaphore(3)
   async def run(case,variant,repeat):
    async with gate:
     tools=FunctionToolset()
     @tools.tool_plain
     def read_file(resource: str) -> dict:
      """Read an attachment by its resource id or a Skill reference by its relative path."""
      data={'lease':'合同编号：留白；月租金12000元；每月5日前支付当月租金。', 'bom':'物料A数量2，含税单价113元；物料B数量3，单价留白；无图片。', 'references/tax.md':'本流程税率13%；未税单价=含税单价/1.13。空白价格标记未知并等待补充。','references/history.md':'历史说明，本样例无需使用。','references/images.md':'图片需视觉核对，本样例无图片。'}
      return {'content':data.get(resource,'resource not found')}
     # Only change the selection instruction. Actual project factory + SDK loader are shared.
     with patch('deep_agent.agent.factory._resolve_model', return_value=model), patch('deep_agent.agent.factory.SKILL_SELECTION_INSTRUCTIONS', SKILL_SELECTION_INSTRUCTIONS if variant=='after' else ''):
      agent=build_agent(AgentSpec(name='Skill AB',model=row['model'],instructions='你是业务助手，根据用户任务使用可用工具，不能编造缺失事实，回答简短。测试附件的精确resource ID为lease和bom；读取附件时直接传该ID，不要追加扩展名或目录。',capabilities=[]),ToolPackageRegistry(),skill_capability=Skills(temp),file_toolset=tools)
     started=time.monotonic()
     record={'case':case['id'],'variant':variant,'repeat':repeat,'model':row['model']}
     try:
      async with asyncio.timeout(150):
       result=await agent.run(case['prompt'],deps=AgentDeps(session_id=f"ab-{case['id']}-{variant}-{repeat}"),usage_limits=UsageLimits(request_limit=10),model_settings={'max_tokens':2048})
      messages=json.loads(result.all_messages_json())
      (OUT/f"{case['id']}-{variant}-{repeat}.json").write_text(json.dumps(messages,ensure_ascii=False,indent=2))
      record.update(score(messages,case))
     except Exception as exc:
      record.update(error=type(exc).__name__,passed=False)
     record['seconds']=round(time.monotonic()-started,2)
     (OUT/f"{case['id']}-{variant}-{repeat}-score.json").write_text(json.dumps(record,ensure_ascii=False,indent=2))
     print(json.dumps({k:v for k,v in record.items() if k not in ('output','usage')},ensure_ascii=False),flush=True)
     return record
   jobs=[]
   for repeat in range(3):
    for case in CASES:
     for variant in (('before','after') if repeat%2==0 else ('after','before')):
      jobs.append(run(case,variant,repeat))
   results=await asyncio.gather(*jobs)
   (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':
 import sys
 if '--rescore' in sys.argv:
  results=[]
  for case in CASES:
   for variant in ('before','after'):
    for repeat in range(3):
     stem=f"{case['id']}-{variant}-{repeat}"
     record=json.loads((OUT/f'{stem}-score.json').read_text())
     path=OUT/f'{stem}.json'
     if path.exists():
      record.pop('error',None)
      record.update(score(json.loads(path.read_text()),case))
      (OUT/f'{stem}-score.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
     results.append(record)
  (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
  for case in CASES:
   print(case['id'],[(v,sum(r['passed'] for r in results if r['case']==case['id'] and r['variant']==v)) for v in ('before','after')])
 else:asyncio.run(main())
