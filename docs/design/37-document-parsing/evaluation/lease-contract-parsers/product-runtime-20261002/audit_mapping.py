"""Selected exact source-gold checks; never feeds gold to any model.

These are partial business-field checks, not whole-document OCR accuracy or
proof of safe payment generation. Source conflict and schema representation are
reported separately rather than 'fixed' by the tool adapter.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from run_adapters import ROOT, save


def equivalent(value, expected):
    if isinstance(value,list):return any(equivalent(item,expected) for item in value)
    if expected is None:return value in ('',None)
    if isinstance(expected,str) and expected.endswith('%'):
        try:return Decimal(str(value))==Decimal(expected[:-1])/100
        except InvalidOperation:return False
    try:return Decimal(str(value).replace(',',''))==Decimal(str(expected).replace(',',''))
    except InvalidOperation:return str(value)==str(expected)


def get(value,path):
    parts=path.split('.')
    for index,part in enumerate(parts):
        if part=='paid' and isinstance(value,list):
            value=[item for item in value if item.get('rentalFree')!='yes'];continue
        if part=='*' and isinstance(value,list):return [get(item,'.'.join(parts[index+1:])) for item in value]
        if isinstance(value,list):
            try:value=value[int(part)]
            except (IndexError,ValueError):return None
        elif isinstance(value,dict):value=value.get(part)
        else:return None
    return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--trial',default='production-initial');args=parser.parse_args()
    trial=ROOT/'local-private'/args.trial
    source=ROOT/'local-private/additional-source-gold.json'
    additional=json.loads(source.read_text())['samples']
    manifest=json.loads((ROOT/'local-private/manifest.json').read_text())
    casehash={row['name']:row['sha256'][:16] for row in manifest}
    checks={}
    def add(name,stage,path,expected):checks.setdefault(casehash[name],[]).append((stage,path,expected))
    for sample in additional:
        f={x['id']:x['expected'] for x in sample['facts']};name=sample['sample']
        for field,key in [('leaseStartDate','lease_start'),('leaseEndDate','lease_end'),('leaseArea','area'),('contractNo','contract_no')]:
            if key in f:add(name,'core',field,f[key])
        if 'fixed_1_month' in f:
            add(name,'rent','fixed.presence','present');add(name,'rent','rate.presence','absent');add(name,'rent','step.presence','absent')
            add(name,'rent','fixed.header.vatRate',f['rent_vat']);add(name,'rent','fixed.header.paymentPeriodInterval','6')
            for i in range(4):
                for path,suffix in [('startDate','start'),('endDate','end'),('amount','month')]:add(name,'rent',f'fixed.periods.paid.{i}.{path}',f[f'fixed_{i+1}_{suffix}'])
        elif 'rate_1_start' in f:
            add(name,'rent','rate.presence','present');add(name,'rent','rate.header.vatRate',f['rent_vat'])
            for i in range(3):
                for path,suffix in [('startDate','start'),('endDate','end'),('driverRate',None)]:add(name,'rent',f'rate.periods.paid.{i}.{path}',f[f'rate_{i+1}'+('_'+suffix if suffix else '')])
        elif 'body_lease_start' in f:
            add(name,'core','leaseStartDate',f['body_lease_start']);add(name,'core','leaseArea',f['building_area'])
            add(name,'rent','fixed.header.vatRate',f['resource_vat']);add(name,'rent','fixed.header.paymentPeriodInterval','3')
    old=ROOT.parent/'local-private/gold.json';g=json.loads(old.read_text());name='明日巴适租赁合同+oa.pdf'
    for path,key in [('leaseStartDate','lease_start'),('leaseEndDate','lease_end'),('leaseArea','area')]:add(name,'core',path,g['fields'][key]['value'])
    add(name,'rent','fixed.presence','absent');add(name,'rent','rate.header.vatRate','9%');add(name,'rent','rate.header.paymentPeriodInterval','1')
    for i,t in enumerate(g['rent_tiers']):
        for path,key in [('startDate','start'),('endDate','end'),('driverRate','rate')]:add(name,'rent',f'rate.periods.paid.{i}.{path}',t[key])
    add(name,'property','periods.0.amountPerBasis','5200')
    # Source-gold says fee depends on revenue; fixed fee alone does not pass.
    for path,v in [('stepPeriods.0.bands.0.upperStep','200000'),('stepPeriods.0.bands.0.stepValue','0'),('stepPeriods.0.bands.1.lowerStep','200000'),('stepPeriods.0.bands.1.stepValue','4550')]:add(name,'property',path,v)
    for name,start,area in [('友阿奥莱租赁合同+oa.pdf','2025-08-01','200'),('友阿奥特莱斯补充协议（二）+oa.pdf','2025-08-01',None)]:
        for field,v in [('leaseStartDate',start),('leaseEndDate','2035-09-30'),('leaseArea',area)]:add(name,'core',field,v)
        add(name,'rent','fixed.presence','absent');add(name,'rent','rate.header.vatRate','5%');add(name,'rent','rate.periods.paid.0.driverRate','7%')
        add(name,'rent','rate.periods.paid.0.startDate','2025-10-01' if area else '2025-09-27')
        add(name,'rent','rate.periods.paid.0.endDate','2035-09-30')
        add(name,'rent','rate.periods.paid.0.driver','含税营业额' if area else '不含税营业额')
        add(name,'property','periods.*.amountPerBasis','2000')
    results=[]
    for case,selected in checks.items():
        for provider in ('mineru','paddle','glm'):
            base=trial/case/provider/'mapping'
            if not (base/'core/result.json').exists():continue
            outputs={stage:json.loads((base/stage/'result.json').read_text()) for stage in ('core','rent','property') if (base/stage/'result.json').exists()}
            rows=[]
            for stage,path,expected in selected:
                observed=get(outputs.get(stage,{}),path);rows.append({'stage':stage,'path':path,'expected':expected,'observed':observed,'passed':equivalent(observed,expected)})
            result={'case_hash':case,'provider':provider,'passed':sum(x['passed'] for x in rows),'checked':len(rows),'checks':rows}
            save(base/'source-gold-audit.json',result);results.append(result)
            print(case,provider,result['passed'],'/',result['checked'])
    save(trial/'mapping-gold-summary.json',results)


if __name__=='__main__':main()
