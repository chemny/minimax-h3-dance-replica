#!/usr/bin/env python3
"""List selected model sources; optionally check existing files. Never download."""
import argparse
import json
from pathlib import Path

from native_client import sha
from routes import recipe, NAMES
from runtime_checks import configure_stdio

ROOT=Path(__file__).resolve().parent.parent

def inventory(config, directories=(), verify_hashes=False):
    r=recipe(config)
    names={r[k] for k in ('base','encoder','vae','audio_vae','adapter','sam','controlnet','turbo_lora') if k in r}
    if r['route']=='depth':names.add(r['depth']['model'])
    manifest=json.loads((ROOT/'presets/model-manifest.json').read_text(encoding='utf-8'))
    table={m['filename']:m for m in manifest['models']}
    result=[]
    for name in sorted(names):
        if name not in table:
            result.append({'filename':name,'status':'unmapped','note':'Custom model needs an explicitly verified source and checksum.'});continue
        item=dict(table[name]);item['status']='not_checked'
        if directories:
            matches=[Path(d)/Path(item['folder']).relative_to('models')/name for d in directories]
            matches=[p for p in matches if p.is_file()]
            if not matches:item['status']='missing'
            elif len(matches)>1:item['status']='ambiguous';item['paths']=[str(p) for p in matches]
            else:
                path=matches[0];item['path']=str(path)
                if path.stat().st_size!=item['size_bytes']:item['status']='size_mismatch'
                elif verify_hashes:item['status']='verified' if sha(path)==item['sha256'] else 'hash_mismatch'
                else:item['status']='present_not_hashed'
        result.append(item)
    return {'route':r['route'],'steps':r['steps'],'downloaded':False,'required_bytes':sum(m.get('size_bytes',0) for m in result),'models':result}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--route',choices=NAMES,default='ref2v');p.add_argument('--steps',type=int,choices=[4,8],default=8)
    p.add_argument('--config',type=Path);p.add_argument('--models-dir',type=Path,action='append',default=[])
    p.add_argument('--verify-sha256',action='store_true');p.add_argument('--json',action='store_true')
    a=p.parse_args()
    if a.verify_sha256 and not a.models_dir:p.error('--verify-sha256 requires --models-dir')
    c=json.loads(a.config.read_text(encoding='utf-8-sig')) if a.config else {'route':a.route,'steps':a.steps}
    d=inventory(c,a.models_dir,a.verify_sha256)
    if a.json:print(json.dumps(d,ensure_ascii=False,indent=2))
    else:
        print(f"{d['route']}: {d['steps']} steps; {d['required_bytes']/1024**3:.2f} GiB total; shared files counted once")
        for m in d['models']:
            print(m['filename']+' ['+m['status']+']')
            if 'url' in m:print('  '+m['folder']+'\n  '+m['url']+'\n  SHA256 '+m['sha256'])
    return 2 if any(m['status'] in ('missing','ambiguous','size_mismatch','hash_mismatch','unmapped') for m in d['models']) else 0

if __name__=='__main__':configure_stdio();raise SystemExit(main())
