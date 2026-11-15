#!/usr/bin/env python3
"""Read-only onboarding audit. Standard library only; never submits jobs or installs."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from routes import recipe as route_recipe, direct_graph, NAMES
from runtime_checks import client_checks, server_modules, configure_stdio
HERE = Path(__file__).resolve().parent

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--comfy-url')
    p.add_argument('--config', type=Path, help='Unified task config; connection credentials remain external')
    p.add_argument('--route', choices=NAMES)
    p.add_argument('--native-key-env', default='COMFY_API_KEY')
    p.add_argument('--native-key-file', type=Path, help='Optional auth proxy key for native ComfyUI')
    p.add_argument('--api-url', help='Optional workflow gateway; not a native ComfyUI URL')
    p.add_argument('--key-file', type=Path, help='Existing gateway key file; contents never reported')
    p.add_argument('--comfy-root', type=Path)
    p.add_argument('--python', dest='python_exe', help='ComfyUI Python, not necessarily the checker Python')
    p.add_argument('--gateway-dir', type=Path, help='Deprecated compatibility option; no longer required')
    p.add_argument('--steps', type=int, choices=[4, 8])
    p.add_argument('--out', type=Path, default=Path.cwd() / 'h3-environment-report.json')
    a = p.parse_args()
    config=json.loads(a.config.read_text(encoding='utf-8-sig')) if a.config else {}
    if a.route:config['route']=a.route
    if a.steps is not None:config['steps']=a.steps
    recipe=route_recipe(config);a.steps=recipe['steps'];route=recipe['route']
    connection=config.get('connection',{})
    if connection.get('kind','native') not in ('native','gateway'):raise ValueError('Unsupported connection.kind')
    if a.config:
        key=connection.get('key_file')
        if key:
            key=Path(key).expanduser();key=key if key.is_absolute() else a.config.resolve().parent/key
        if connection.get('kind','native')=='gateway':
            a.api_url=a.api_url or connection['url'];a.key_file=a.key_file or key
        else:
            a.comfy_url=a.comfy_url or connection.get('url');a.native_key_file=a.native_key_file or key
            a.native_key_env=connection.get('key_env','COMFY_API_KEY')
    gateway_only=bool(a.api_url) and not a.comfy_url
    if not a.comfy_url and not gateway_only:a.comfy_url=os.getenv('COMFY_URL','http://127.0.0.1:8188')
    checks = []
    def add(name, state, detail, action=''):
        checks.append(dict(name=name, status=state, detail=detail, next_action=action))
    # Avoid URL-embedded secrets and do not persist supplied endpoint URLs.
    def get(base, route, key=None):
        u = urllib.parse.urlsplit(base)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Invalid URL')
        if key is None and base == a.comfy_url:
            key = a.native_key_file.read_text(encoding='utf-8-sig').strip() if a.native_key_file else os.getenv(a.native_key_env,'')
        req = urllib.request.Request(base.rstrip('/') + route, headers={'Authorization': 'Bearer ' + key} if key else {})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=12) as r:
            return json.load(r)
    def read_api(base, route, key=None):
        try:
            return get(base, route, key)
        except urllib.error.HTTPError as e:
            add(route, 'fail', 'HTTP ' + str(e.code), '401/403: check credentials and permissions; 404: check endpoint type and base path.')
        except Exception as e:
            add(route, 'unknown', type(e).__name__, 'Check service, address, network and TLS; no generation was submitted.')
        return None
    checks.extend(client_checks())
    if a.api_url:
        key = os.getenv(connection.get('key_env','COMFY_API_KEY'), '')
        if a.key_file:
            try:
                key = a.key_file.read_text(encoding='utf-8-sig').strip()
            except OSError:
                add('gateway_key', 'fail', 'Key file unreadable', 'Provide an existing readable key file; do not paste the key into reports.')
        if not key:
            add('gateway_key', 'unknown', 'No key supplied', 'For authenticated gateways supply --key-file or COMFY_API_KEY. Native ComfyUI may have no key.')
        cat = read_api(a.api_url, '/v1/workflows', key)
        if cat is not None:
            workflow='video.character_swap' if route=='character_swap' else 'video.reference'
            supported=route!='depth' and workflow in cat.get('workflows',{})
            add('gateway_route_mapping','pass' if supported else 'fail',{'route':route,'workflow':workflow if route!='depth' else None,'advertised':supported},'Use a compatible workflow API or native ComfyUI for depth; video.replace_person is not the FL2VA depth route.')
            add('gateway_recipe_preflight','unknown','Actual model/LoRAs are checked against /v1/prepare before /v1/jobs; catalog alone is insufficient')
    native_local=bool(a.comfy_url and urllib.parse.urlsplit(a.comfy_url).hostname in ('localhost','127.0.0.1','::1'))
    inspect_local=bool(a.comfy_root or a.python_exe or (native_local and not gateway_only))
    root = a.comfy_root
    if not root and inspect_local:
        candidates = [Path.cwd(), Path.cwd()/'ComfyUI', Path.home()/'ComfyUI']
        if os.name == 'nt':
            candidates += [Path.home()/'ComfyUI-Installs'/'ComfyUI'/'ComfyUI']
        root = next((x for x in candidates if (x/'main.py').is_file() and (x/'folder_paths.py').is_file()), None)
    if inspect_local:add('comfy_install', 'pass' if root and (root/'main.py').is_file() else 'unknown', str(root) if root else 'Not found in common locations', 'For portable/Desktop/custom installs supply --comfy-root; remote API users need no local ComfyUI.')
    py = a.python_exe
    if not py and root:
        py = next((str(x) for x in [root/'.venv/Scripts/python.exe', root/'.venv/bin/python', root.parent/'python_embeded/python.exe'] if x.is_file()), None)
    if py:
        probe = """import json,importlib.util,sys
r={'modules':{x:importlib.util.find_spec(x) is not None for x in json.loads(sys.argv[1])}}
r['python']=sys.version.split()[0]
try:
 import torch
 r['torch']=torch.__version__;r['cuda']=torch.cuda.is_available()
 if r['cuda']:r['gpu']=torch.cuda.get_device_name(0);r['vram_gib']=round(torch.cuda.get_device_properties(0).total_memory/1024**3,1)
except Exception as e:r['torch_error']=type(e).__name__
print(json.dumps(r))
"""
        try:
            result = subprocess.run([py, '-X', 'utf8', '-c', probe, json.dumps(server_modules(recipe))], capture_output=True, encoding='utf-8', errors='replace', timeout=45)
            info = json.loads(result.stdout.strip().splitlines()[-1])
            add('comfy_python', 'pass', info)
            missing = [k for k,v in info['modules'].items() if not v]
            add('python_dependencies', 'fail' if missing else 'pass', {'missing':missing}, 'Use this ComfyUI interpreter and the installed node requirements to resolve missing packages after approval; never blindly upgrade torch.')
            add('cuda', 'pass' if info.get('cuda') else 'fail', 'CUDA available' if info.get('cuda') else 'CUDA unavailable', 'Check NVIDIA driver and matching PyTorch. Current preset validated on RTX3090 24GB; other GPUs need separate validation.')
        except Exception as e:
            add('comfy_python', 'unknown', type(e).__name__, 'Supply the actual ComfyUI Python with --python; inspect startup errors.')
    elif inspect_local:
        add('comfy_python', 'unknown', 'Runtime not located; checker Python is not assumed to be ComfyUI Python', 'Supply --python. For remote API ask the operator for an environment report; local Python says nothing about remote dependencies.')
    elif a.comfy_url:
        add('server_runtime', 'unknown', 'Remote server runtime is not inspected from this client', 'Run the checker on the GPU host for Python/CUDA details; no local ComfyUI installation is required.')
    obj = read_api(a.comfy_url, '/object_info') if a.comfy_url else None
    if isinstance(obj, dict):
        spec = importlib.util.spec_from_file_location('h3_graph', HERE/'h3_run.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        graph = mod.build('first.png','last.png','depth.mp4','audit only',3,recipe['width'],recipe['height'],a.steps,1,recipe=recipe) if route=='depth' else direct_graph(recipe,'identity.png','motion.mp4','audit only',3,1)[0]
        required = set(n['class_type'] for n in graph.values()) | ({'DA3Render','LoadDA3Model','DA3Inference'} if route=='depth' else set())
        missing = sorted(required - obj.keys())
        add('native_nodes', 'fail' if missing else 'pass', {'missing':missing}, 'Confirm ComfyUI version and existing custom-node installation; use node startup logs to resolve missing imports, then recheck. Do not auto-install.')
        selectors={'UNETLoader':'unet_name','LoraLoaderModelOnly':'lora_name','ModelPatchLoader':'name','CLIPLoader':'clip_name','VAELoader':'vae_name','CheckpointLoaderSimple':'ckpt_name'}
        needs=[(n['class_type'],selectors[n['class_type']],n['inputs'][selectors[n['class_type']]]) for n in graph.values() if n['class_type'] in selectors]
        for node, field, weight in needs:
            desc = obj.get(node,{}).get('input',{}).get('required',{}).get(field,[None])
            choices = desc[0] if isinstance(desc[0],list) else (desc[1].get('options') if desc[0]=='COMBO' else None)
            found = isinstance(choices,list) and any(str(x).replace('\\','/').split('/')[-1]==weight for x in choices)
            add('model:'+weight, 'pass' if found else ('fail' if isinstance(choices,list) else 'unknown'), 'Listed by running ComfyUI' if found else 'Not advertised in expected model selector', 'Check model folders and extra_model_paths.yaml; equivalent/renamed weights require explicit graph mapping, not silent substitution.')
        if route=='depth':
            da3 = json.dumps(obj.get('LoadDA3Model',{}));found=recipe['depth']['model'] in da3
            add('model:DA3_Mono_Large','pass' if found else 'unknown','Listed by node' if found else 'Not confirmed from node schema','Inspect DA3 selector and configured model paths before requesting download.')
        queue = read_api(a.comfy_url, '/queue')
        if isinstance(queue,dict):
            busy = bool(queue.get('queue_running') or queue.get('queue_pending'))
            add('queue', 'warn' if busy else 'pass', 'Busy; do not interrupt' if busy else 'Idle at inspection time')
    add('generation_client', 'pass', 'Bundled native_client.py; no external gateway source required')
    if route=='depth':
        add('endpoint_image_editing','unknown','Codex image tool availability must be checked by the agent; no ComfyUI image-edit model required','Use Codex image editing or supply reviewed first/last files.')
        add('depth_execution_access','pass' if isinstance(obj,dict) and all(k in obj for k in ['DA3Inference','DA3Render','LoadDA3Model']) else 'unknown','Depth uses native API; no SSH required')
    report = {'mode':'gateway' if gateway_only else 'gateway-and-native' if a.api_url else 'native', 'route':route,'recipe':recipe,'steps':a.steps, 'checks':checks, 'ready':False, 'note':'Read-only inventory, not a generation proof. Resolve fail/unknown items and validate actual graphs before submitting.'}
    # Never claim readiness from selectors alone: runtime schema, file integrity and end-to-end execution remain unverified.
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for c in checks:
        print('[%s] %s: %s' % (c['status'].upper(),c['name'],json.dumps(c['detail'],ensure_ascii=True)))
    print('Report: '+str(a.out.resolve()))
    return 2 if any(c['status']=='fail' for c in checks) else 0

if __name__=='__main__':
    configure_stdio()
    raise SystemExit(main())
