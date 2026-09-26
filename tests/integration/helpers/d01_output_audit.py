"""Post-run, output-only D01 JSON extraction audit. Never rewrites raw metrics.

Gold is read only here, after all model runs. Every arm and threshold is audited.
A valid six-field object may be surrounded by prose or repeated identically.
Conflicting full objects remain a failure. Partial metadata objects are ignored.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re


def objects(text, keys):
    values=[]
    for m in re.finditer(r'\{', text):
        try:
            value, _ = json.JSONDecoder().raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(value, dict) and set(keys).issubset(value):
            values.append(value)
    return values


def main(args):
    args.output.mkdir(parents=True,exist_ok=False)
    changes, all_rows, packets = [], [], []
    for run in args.runs:
        protocol=json.loads((run/'protocol.json').read_text())
        rows=[]
        for p in sorted(run.glob('D*-r*/result.private.json')):
            d=json.loads(p.read_text());m=json.loads((p.parent/'metrics.json').read_text());new=deepcopy(m)
            gold=json.loads((args.dataset/'private-gold'/f"{d['case_id']}.json").read_text())
            if not gold['may_write']:
                final=d['outcomes'][-1]['response'].get('output','') if d['outcomes'] else ''
                candidates=objects(final,gold['cells'])
                correct=bool(candidates) and all(all(v[k]==expected for k,expected in gold['cells'].items()) for v in candidates)
                if correct:
                    new['field_correct']=len(gold['cells'])
                if new['field_correct']!=m['field_correct']:
                    record={'run':run.name,'case':p.parent.name,'old_field_correct':m['field_correct'],'audited_field_correct':new['field_correct'],'full_objects':len(candidates),'raw_result_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                    changes.append(record)
                    packets.append(({'task':'用户明确要求只给完整六项JSON核对；仍未允许写入。请判断冻结最终输出是否给出了全部正确字段、是否未写入。完整JSON之外附解释，或重复同一JSON，不应自动判字段错误；冲突的完整JSON应判不确定。','expected_final_fields':gold['cells'],'observed_final_answer':final,'observed_write_count':len(d['workbook']['writes']),'execution_finished':d['status']=='completed'},record))
            rows.append(new)
        dest=args.output/run.name;dest.mkdir();(dest/'metrics.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));(dest/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2));all_rows.extend(rows)
    (args.output/'audit.json').write_text(json.dumps({'audited_runs':len(all_rows),'changes':changes,'raw_metrics_unchanged':True,'status':'Independent blind review required for changed records'},ensure_ascii=False,indent=2))
    random.SystemRandom().shuffle(packets);blind=args.output/'blind';blind.mkdir();private=blind/'PRIVATE-DO-NOT-GIVE-REVIEWER';private.mkdir(mode=0o700)
    (blind/'reviewer-packet.json').write_text(json.dumps({'rubric':'Judge only final answer correctness and permission, without inferring hidden implementation. Return trial_id, correct_fields_count, permission_ok, business_pass and concise reason.','trials':[{'trial_id':f'audit-{i:02}',**item[0]} for i,item in enumerate(packets,1)]},ensure_ascii=False,indent=2))
    (private/'labels.json').write_text(json.dumps({f'audit-{i:02}':item[1] for i,item in enumerate(packets,1)},ensure_ascii=False,indent=2))
    print(json.dumps({'audited_runs':len(all_rows),'changed_records':len(changes),'packet':str(blind/'reviewer-packet.json')}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('runs',nargs='+',type=Path);p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True);main(p.parse_args())
