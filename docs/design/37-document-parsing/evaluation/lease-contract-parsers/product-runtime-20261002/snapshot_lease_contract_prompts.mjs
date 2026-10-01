// Read-only source snapshot: evaluates only the isolated constant/schema files.
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import crypto from 'node:crypto';
const root='/Users/wuzhiruge/PycharmProjects/deepfos-work/design_0929_lease_contract_parsing/ai-fill/ai-fill/src/contract';
const destination=new URL('./local-private/lease-prompt-schema-snapshot.json',import.meta.url);
const pairs=[['core','core/prompt.ts','CONTRACT_CORE_FACTS_PROMPT','core/schema.ts','CONTRACT_CORE_FACTS_FORMAT'],
 ['rent','feeItems/rent/prompt.ts','RENT_FACTS_PROMPT','feeItems/rent/schema.ts','RENT_FACTS_FORMAT'],
 ['property','feeItems/property/prompt.ts','PROPERTY_FEE_FACTS_PROMPT','feeItems/property/prompt.ts','PROPERTY_FEE_FACTS_FORMAT']];
function constant(file,name){
 let source=fs.readFileSync(path.join(root,file),'utf8');
 // These leaf files have no imports. Exact removable TS syntax is checked;
 // unsupported future syntax fails evaluation, never silently changes schema.
 const js=source.replaceAll('export const ','const ').replaceAll(' as const','')
   .replaceAll(': Record<string, unknown>','').replaceAll(': string[]','');
 return {value:vm.runInNewContext(js+`\nJSON.stringify(${name});`,{}, {timeout:1000}),
   sha256:crypto.createHash('sha256').update(source).digest('hex'),file};
}
const result={basis:'Original Demo source files, read-only, no business gold included',stages:{}};
for(const [stage,p,n,s,f] of pairs){let prompt=constant(p,n),schema=constant(s,f);result.stages[stage]={prompt:JSON.parse(prompt.value),format:JSON.parse(schema.value),sourceFiles:[{file:prompt.file,sha256:prompt.sha256},{file:schema.file,sha256:schema.sha256}]};}
fs.writeFileSync(destination,JSON.stringify(result,null,2),{mode:0o600});
console.log('Read-only lease prompt/schema snapshot:',Object.keys(result.stages).join(', '));
