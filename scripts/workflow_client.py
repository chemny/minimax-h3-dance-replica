"""Adapter for the documented /v1/workflows, /v1/prepare, /v1/jobs protocol."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from native_client import Client, APIError, sha, write
from routes import assert_gateway_recipe


class WorkflowClient(Client):
    def request(self, route, data=None, timeout=30, content_type='application/json', headers=None):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode()
        h = {'Content-Type': content_type}
        if self.key:
            h['Authorization'] = 'Bearer '+self.key
        h.update(headers or {})
        try:
            with self.http.open(urllib.request.Request(self.base+route, data=data, headers=h), timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise APIError(error.code, route) from None

    def upload(self, path):
        path = Path(path)
        return self.request('/v1/assets?filename='+urllib.parse.quote(path.name, safe=''), path.read_bytes(),
                            timeout=300, content_type='application/octet-stream')['id']

    def capabilities(self, recipe):
        data = self.request('/v1/workflows')
        workflow = 'video.character_swap' if recipe['route'] == 'character_swap' else 'video.reference'
        if workflow not in data.get('workflows', {}):
            raise ValueError('Gateway does not advertise '+workflow)
        return {'workflow': workflow, 'catalog': data['workflows'][workflow],
                'validation_scope': 'catalog only; actual recipe checked with /v1/prepare before any submission'}

    def stage(self, directory, body, recipe, timeout=7200, poll=3):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        lock = directory/'active.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError('Stage lock exists; verify previous process has stopped') from None
        os.close(fd)
        try:
            return self._gateway_stage(directory, body, recipe, timeout, poll)
        finally:
            lock.unlink(missing_ok=True)

    def _gateway_stage(self, directory, body, recipe, timeout, poll):
        journal = directory/'job.json'
        fingerprint = hashlib.sha256(json.dumps({'body': body, 'recipe': recipe}, sort_keys=True).encode()).hexdigest()
        s = json.loads(journal.read_text(encoding='utf-8-sig')) if journal.exists() else {
            'token': uuid.uuid4().hex, 'base': self.base, 'fingerprint': fingerprint, 'status': 'prepared'}
        if s['base'] != self.base or s['fingerprint'] != fingerprint:
            raise ValueError('Gateway task/service/recipe changed; use a new task directory')
        write(journal, s)
        write(directory/'request.json', body)
        if s['status'] == 'succeeded' and s.get('files') and all(
                (directory/f).is_file() and sha(directory/f) == s['sha256'][f] for f in s['files']):
            return [directory/f for f in s['files']]
        if s['status'] in ('failed', 'cancelled', 'interrupted'):
            raise RuntimeError('Previous gateway stage failed or was cancelled; inspect before an intentional new task')
        if not s.get('job_id'):
            plan = self.request('/v1/prepare', body, timeout=300)
            assert_gateway_recipe(plan, recipe)
            prompts=[n['inputs'].get('prompt') for n in plan['graph'].values() if n['class_type']=='MiniMaxH3ReferenceToVideo']
            if 'prompt' in body and prompts!=[body['prompt']]:
                raise ValueError('Gateway changed the supplied prompt; no video job was submitted')
            write(directory/'prepare.json', plan)
            s['status'] = 'submitting'
            write(journal, s)
            # The same saved key/body is reused after a lost response. Requires this gateway's idempotency contract.
            try:
                job = self.request('/v1/jobs', body, timeout=300, headers={'Idempotency-Key': s['token']})
            except Exception:
                s['status'] = 'submission_unknown'
                write(journal, s)
                raise
            s.update(job_id=job['id'], status='running')
            write(journal, s)
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            job = self.request('/v1/jobs/'+urllib.parse.quote(s['job_id'], safe=''))
            write(directory/'result.json', job)
            if job['status'] in ('failed', 'cancelled', 'interrupted', 'submission_unknown'):
                s['status'] = job['status']
                write(journal, s)
                raise RuntimeError('Gateway job '+job['status']+'; inspect saved result, no automatic new job')
            if job['status'] == 'succeeded':
                videos = [x for x in job['outputs'] if Path(x['filename']).suffix.lower() in ('.mp4', '.webm', '.mov')]
                if len(videos) != 1:
                    raise ValueError('Expected exactly one final gateway video')
                item = videos[0]
                route = item['url']
                if not route.startswith('/v1/jobs/'+s['job_id']+'/files/'):
                    raise ValueError('Unexpected output URL; credentials are never sent to a different host')
                dest = directory/('output'+Path(item['filename']).suffix.lower())
                tmp = dest.with_suffix(dest.suffix+'.part')
                headers = {'Authorization': 'Bearer '+self.key} if self.key else {}
                with self.http.open(urllib.request.Request(self.base+route, headers=headers), timeout=300) as response, tmp.open('wb') as out:
                    size = 0
                    while block := response.read(1024**2):
                        size += len(block)
                        if size > 2*1024**3:
                            raise ValueError('Output exceeds 2GiB limit')
                        out.write(block)
                tmp.replace(dest)
                s.update(status='succeeded', files=[dest.name], sha256={dest.name: sha(dest)})
                write(journal, s)
                return [dest]
            time.sleep(poll)
        raise TimeoutError('Gateway job still exists; resume this stage, do not create a different key')
