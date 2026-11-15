#!/usr/bin/env python3
"""Standalone stdlib ComfyUI client; credentials stay outside task journals."""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def write(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,path)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


class APIError(RuntimeError):
    def __init__(self, code, route):
        self.code=code
        super().__init__('ComfyUI HTTP '+str(code)+' at '+route.split('?')[0])


class Client:
    def __init__(self, base=None, key_file=None, key_env='COMFY_API_KEY'):
        self.base=(base or os.getenv('COMFY_URL','http://127.0.0.1:8188')).rstrip('/')
        parts=urllib.parse.urlsplit(self.base)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('Use an HTTP(S) base URL without embedded credentials/query')
        self.key=Path(key_file).read_text(encoding='utf-8-sig').strip() if key_file else os.getenv(key_env,'')
        self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(self, route, data=None, timeout=30, content_type='application/json'):
        if isinstance(data,dict):data=json.dumps(data,ensure_ascii=False).encode()
        headers={'Content-Type':content_type}
        if self.key:headers['Authorization']='Bearer '+self.key
        try:
            with self.http.open(urllib.request.Request(self.base+route,data=data,headers=headers),timeout=timeout) as r:return json.load(r)
        except urllib.error.HTTPError as e:
            # Server error bodies can include private prompt data; don't include them in console errors.
            raise APIError(e.code,route) from None
    def idle(self):
        q=self.request('/queue')
        if q.get('queue_running') or q.get('queue_pending'):raise RuntimeError('GPU queue busy; wait without interrupting others')
    def upload(self,path):
        path=Path(path);boundary=uuid.uuid4().hex;name=uuid.uuid4().hex+path.suffix.lower()
        head=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: {mimetypes.guess_type(name)[0] or "application/octet-stream"}\r\n\r\n').encode()
        r=self.request('/upload/image',head+path.read_bytes()+f'\r\n--{boundary}--\r\n'.encode(),timeout=300,content_type='multipart/form-data; boundary='+boundary)
        return '/'.join(filter(None,[r.get('subfolder'),r['name']]))
    def download(self,ref,dest):
        query=urllib.parse.urlencode({k:ref[k] for k in ('filename','subfolder','type') if k in ref})
        headers={'Authorization':'Bearer '+self.key} if self.key else {}
        dest=Path(dest);tmp=dest.with_suffix(dest.suffix+'.part');total=0
        try:
            with self.http.open(urllib.request.Request(self.base+'/view?'+query,headers=headers),timeout=300) as r,tmp.open('wb') as f:
                while chunk:=r.read(1024*1024):
                    total+=len(chunk)
                    if total>2*1024**3:raise RuntimeError('Output exceeds 2 GiB download limit')
                    f.write(chunk)
            os.replace(tmp,dest)
        finally:tmp.unlink(missing_ok=True)
    def recover(self,token):
        q=self.request('/queue')
        for entry in q.get('queue_running',[])+q.get('queue_pending',[]):
            if len(entry)>3 and isinstance(entry[3],dict) and entry[3].get('dance_replica_job')==token:return entry[1]
        history=self.request('/history?max_items=200')
        for pid,record in history.items():
            prompt=record.get('prompt',[])
            if len(prompt)>3 and isinstance(prompt[3],dict) and prompt[3].get('dance_replica_job')==token:return pid
        return None
    def stage(self,directory,graph,output_ids,timeout=7200,poll=3):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        lock=directory/'active.lock'
        try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:raise RuntimeError('Stage lock exists. Verify previous process is stopped before removing active.lock.') from None
        os.close(fd)
        try:return self._stage(directory,graph,output_ids,timeout,poll)
        finally:lock.unlink(missing_ok=True)
    def _stage(self,d,graph,ids,timeout,poll):
        journal=d/'job.json';fingerprint=hashlib.sha256(json.dumps(graph,sort_keys=True).encode()).hexdigest()
        s=json.loads(journal.read_text(encoding='utf-8-sig')) if journal.exists() else {'token':uuid.uuid4().hex,'graph_sha256':fingerprint,'base':self.base,'status':'prepared'}
        if s['graph_sha256']!=fingerprint or s['base']!=self.base:raise ValueError('Task graph/service changed; use a new task directory')
        write(d/'graph.json',graph);write(journal,s)
        if s['status']=='succeeded' and all((d/x).is_file() and sha(d/x)==s['sha256'][x] for x in s.get('files',[])) and s.get('files'):
            return [d/x for x in s['files']]
        if s['status'] in ('submitting','submission_unknown') and not s.get('prompt_id'):
            pid=self.recover(s['token'])
            if not pid:raise RuntimeError('Submission uncertain; not resubmitted. Inspect server queue/history using token '+s['token'])
            s['prompt_id']=pid;write(journal,s)
        if s['status'] in ('failed','rejected'):raise RuntimeError('Previous stage '+s['status']+'; inspect history and use a new directory for an intentional retry')
        if not s.get('prompt_id'):
            self.idle();s['status']='submitting';write(journal,s)
            try:r=self.request('/prompt',{'prompt':graph,'client_id':s['token'],'extra_data':{'dance_replica_job':s['token']}})
            except Exception as exc:
                s['status']='rejected' if isinstance(exc,APIError) and exc.code in (400,401,403,404,422) else 'submission_unknown'
                write(journal,s);raise
            if not r.get('prompt_id') or r.get('error'):
                s['status']='rejected';write(d/'rejection.json',r);write(journal,s);raise RuntimeError('Graph rejected; inspect rejection.json')
            s['prompt_id']=r['prompt_id']
        s['status']='running';write(journal,s);deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            h=self.request('/history/'+urllib.parse.quote(s['prompt_id'],safe=''))
            r=h.get(s['prompt_id'])
            if r:
                status=r.get('status',{})
                if status.get('status_str')=='error':
                    s['status']='failed';write(d/'history.json',r);write(journal,s);raise RuntimeError('ComfyUI execution failed; inspect history.json')
                if status.get('completed'):
                    write(d/'history.json',r);refs=[]
                    for node_id in ids:
                        output=r.get('outputs',{}).get(str(node_id),{})
                        for kind in ('images','videos','gifs'):
                            refs.extend(x for x in output.get(kind,[]) if isinstance(x,dict) and x.get('filename') and x.get('type')=='output')
                    if not refs:raise RuntimeError('Expected save node returned no output files')
                    paths=[]
                    for i,ref in enumerate(refs):
                        suffix=Path(ref['filename']).suffix.lower()
                        if suffix not in {'.png','.jpg','.jpeg','.webp','.mp4','.webm','.mov'}:raise ValueError('Unexpected output format')
                        dest=d/f'output-{i}{suffix}';self.download(ref,dest);paths.append(dest)
                    s.update(status='succeeded',files=[x.name for x in paths],sha256={x.name:sha(x) for x in paths});write(journal,s);return paths
            time.sleep(poll)
        raise TimeoutError('Polling timed out; resume this same stage without resubmitting')


def validate_graph(client,graph):
    """Resolve selectors including model subdirectories and detect schema mismatches without submitting."""
    obj=client.request('/object_info')
    for n in graph.values():
        kind=n['class_type']
        if kind not in obj:raise ValueError('Missing node: '+kind)
        schema=obj[kind].get('input',{})
        fields={**schema.get('required',{}),**schema.get('optional',{})}
        for field,value in list(n['inputs'].items()):
            if (kind,field) in {('LoadImage','image'),('LoadVideo','file')}:continue # Uploaded media are validated by the server, not model inventory.
            if field not in fields:continue # dynamic combo children are flattened by ComfyUI
            desc=fields[field];choices=desc[0] if isinstance(desc[0],list) else (desc[1].get('options') if desc[0]=='COMBO' else None)
            if isinstance(value,str) and isinstance(choices,list) and value not in choices:
                matches=[x for x in choices if isinstance(x,str) and x.replace('\\','/').split('/')[-1]==value]
                if len(matches)==1:n['inputs'][field]=matches[0]
                else:raise ValueError('Missing/ambiguous selector '+kind+'.'+field+': '+value)
    return graph
