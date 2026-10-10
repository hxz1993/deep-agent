"""Compare first source table to pre-existing manually checked cells.

Whitespace/NFKC normalization is only for this test assertion, never product
output conversion. Known PDF/image page facts are not sent to suppliers.
"""
from html.parser import HTMLParser
import json
import re
import unicodedata
from run_adapters import ROOT, save


class FirstTable(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.completed=False;self.rows=[];self.row=[];self.cell=None
    def handle_starttag(self,tag,attrs):
        if self.completed:return
        if tag=='table':self.depth+=1
        if self.depth!=1:return
        if tag=='tr':self.row=[]
        if tag in ('td','th'):self.cell=[]
        if tag=='br' and self.cell is not None:self.cell.append(' ')
    def handle_endtag(self,tag):
        if self.completed:return
        if self.depth==1:
            if tag in ('td','th') and self.cell is not None:self.row.append(''.join(self.cell));self.cell=None
            if tag=='tr' and self.row:self.rows.append(self.row)
        if tag=='table':
            self.depth-=1
            if self.depth==0:self.completed=True
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)


def normalize(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text))


def main():
    gold=json.loads((ROOT.parent/'online-api-20260930/local-private/table-audit/gold.json').read_text())['rows']
    trial=ROOT/'local-private/production-initial';results=[]
    for provider in ('mineru','paddle','glm'):
        base=trial/'37e58b3e1cdbbea4'/provider;p=base/'normalized-result.json'
        if not p.exists():continue
        parsed=json.loads(p.read_text());parser=FirstTable();parser.feed(parsed['pages'][0]['content'])
        matches=[]
        for i,row in enumerate(gold):
            for j,expected in enumerate(row):
                actual=parser.rows[i][j] if i<len(parser.rows) and j<len(parser.rows[i]) else None
                matches.append({'row':i+1,'column':j+1,'expected':expected,'actual':actual,'passed':actual is not None and normalize(actual)==normalize(expected)})
        result={'provider':provider,'checked':len(matches),'passed':sum(m['passed'] for m in matches),'observed_rows':len(parser.rows),'cells':matches}
        save(base/'image-table-audit.json',result);results.append(result)
        print(provider,result['passed'],'/',result['checked'],'rows',result['observed_rows'])
    save(trial/'image-table-summary.json',results)


if __name__=='__main__':main()
