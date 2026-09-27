import json
from pathlib import Path
import tempfile
import unittest

from tests.integration.helpers.d01_output_audit import objects
from tests.integration.helpers.d01_duplicate_reads import inspect


class D01OutputAuditTests(unittest.TestCase):
    def test_extracts_complete_objects_without_merging_prose_or_metadata(self):
        text='JSON {"a":1,"b":2} and source {"line":4}; repeat {"a":1,"b":2}'
        self.assertEqual(objects(text,{'a','b'}),[{'a':1,'b':2}]*2)
        self.assertEqual(objects('{"a":1,"b":2} {"a":3,"b":2}',{'a','b'})[-1]['a'],3)

    def test_duplicate_requires_same_run_parameters_and_complete_return(self):
        events=[]
        def pair(run,ident,offset,content):
            events.extend([{'run_id':run,'event_type':'assistant/message','payload':{'message':{'parts':[{'part_kind':'tool-call','tool_name':'evidence_read','tool_call_id':ident,'args':json.dumps({'handle':'x','offset':offset})}]}}},
                           {'run_id':run,'event_type':'tool/results','payload':{'message':{'parts':[{'part_kind':'tool-return','tool_name':'evidence_read','tool_call_id':ident,'outcome':'success','content':content}]}}}])
        pair('run1','1',0,{'rows':[1]});pair('run1','2',0,{'rows':[1]})
        pair('run1','3',1,{'rows':[1]});pair('run1','4',0,{'rows':[2]})
        pair('run2','5',0,{'rows':[1]});pair('seed-0','6',0,{'rows':[1]})
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete-d01-audit-') as temp:
            p=Path(temp);(p/'events.synthetic.json').write_text(json.dumps(events));result=inspect(p)
        self.assertEqual(result['successful_read_requests'],{'evidence_read':5})
        self.assertEqual(result['exact_repeated_read_requests'],{'evidence_read':1})
        self.assertEqual(result['unmatched_returns'],0)
