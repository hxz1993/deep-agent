"""Count exact repeated successful fixture reads from durable original events.

Same run_id, tool name, normalized explicit/default parameters, and full return
content hash. Excludes seeded history, history tools, failed retries, and workbook
reads (a read before authorized update or revision refresh may be necessary).
No claim about model intent or semantic waste. No private content is exported.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


def inspect(path):
    events=json.loads((path/'events.synthetic.json').read_text());calls={};seen=Counter();totals=Counter();repeats=Counter();unmatched=0
    for event in events:
        run=event.get('run_id')
        if not run or run.startswith('seed') or event.get('event_type') not in ('assistant/message','tool/results'): continue
        for part in event.get('payload',{}).get('message',{}).get('parts',[]):
            kind=part.get('part_kind');name=part.get('tool_name');key=(run,part.get('tool_call_id'))
            if name not in ('evidence_read','archive_page'):continue
            if kind=='tool-call':
                args=part.get('args',{})
                if isinstance(args,str):
                    try:args=json.loads(args)
                    except ValueError:continue
                if name=='evidence_read':args={'offset':0,'limit':30,'pattern':'',**args}
                calls[key]=(name,canonical(args))
            elif kind=='tool-return' and part.get('outcome','success')=='success':
                if key not in calls:
                    unmatched+=1;continue
                name,args=calls[key];content=hashlib.sha256(canonical(part.get('content')).encode()).hexdigest();signature=(run,name,args,content)
                totals[name]+=1
                if seen[signature]:repeats[name]+=1
                seen[signature]+=1
    return {'successful_read_requests':dict(totals),'exact_repeated_read_requests':dict(repeats),'unmatched_returns':unmatched}


def aggregate(rows):
    totals=Counter();repeats=Counter()
    for r in rows:
        totals.update(r['successful_read_requests']);repeats.update(r['exact_repeated_read_requests'])
    return {'runs':len(rows),'successful_read_requests':dict(totals),'exact_repeated_read_requests':dict(repeats),'unmatched_returns':sum(r['unmatched_returns'] for r in rows)}


def success(m):return m['status']=='completed' and m['field_correct']==6 and m['permission'] and m['no_duplicate_write']


def main(args):
    output=[]
    for ratio in ['80','85','90']:
        arms={}
        for arm in ['baseline','candidate']:
            run=args.results/f'{arm}-flash-{ratio}-v4';metrics=json.loads((args.audit/run.name/'metrics.json').read_text());by={(m['case_id'],m['repeat']):m for m in metrics}
            arms[arm]={(p.name):{'trace':inspect(p),'success':success(by[(p.name.split('-')[0],int(p.name.split('-r')[1]))])} for p in run.glob('D*-r*') if (p/'events.synthetic.json').exists()}
        common=sorted(k for k in arms['baseline'] if arms['baseline'][k]['success'] and arms['candidate'][k]['success'])
        output.append({'trigger':int(ratio)/100,'common_business_success_pairs':len(common),'all_runs':{a:aggregate([v['trace'] for v in vals.values()]) for a,vals in arms.items()},'common_success_subset':{a:aggregate([vals[k]['trace'] for k in common]) for a,vals in arms.items()}})
    args.output.write_text(json.dumps({'definition':__doc__,'business_success_selection':'Output-audited metrics; blind adjudication of six JSON extraction corrections pending at generation.','comparisons':output},ensure_ascii=False,indent=2))
    print(json.dumps(output,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--results',type=Path,required=True);p.add_argument('--audit',type=Path,required=True);p.add_argument('--output',type=Path,required=True);main(p.parse_args())
