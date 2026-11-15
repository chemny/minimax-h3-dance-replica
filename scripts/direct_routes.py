"""Shared lifecycle for native Ref2V and Character Swap; no endpoints/depth stages."""
from fractions import Fraction
import json
import math
from pathlib import Path
from types import SimpleNamespace

import media
from dance import Pipeline, load, run
from native_client import Client, sha, validate_graph, write
from routes import direct_graph
from workflow_client import WorkflowClient
from stages import Stage,input_file


class DirectPipeline(Pipeline):
    def __init__(self, config, create=True):
        super().__init__(config, create)
        allowed={'route','connection','source','identity','out','prompt','performance','duration_seconds','seed','steps',
                 'width','height','lora_strength','sage','motion_reference_long_edge','resolution_preset','mask_expansion',
                 'model_overrides','audio','timeout_seconds','segmentation'}
        if set(self.c)-allowed:raise ValueError('Unsupported direct-route fields: '+', '.join(sorted(set(self.c)-allowed)))
        if not self.c.get('identity'):raise ValueError('Direct routes require identity')
        if self.c.get('segmentation') and self.c['segmentation'].get('enabled',True):raise ValueError('External direct-route segmentation is not implemented; Relay internal windows are separate')
        if any(self.c.get(k) for k in ('first', 'last', 'depth', 'wardrobe_reference')):
            raise ValueError('Direct routes use one approved identity/clothing image, without first/last/depth inputs')
        if self.c.get('audio', 'source') not in ('source', 'none'):
            raise ValueError('audio must be source or none')

    def client(self):
        c = self.c.get('connection', {})
        cls = WorkflowClient if c.get('kind', 'native') == 'gateway' else Client
        if c.get('kind', 'native') not in ('native', 'gateway'):
            raise ValueError('connection.kind must be native or gateway')
        return cls(c.get('url'), self.resolve(c['key_file']) if c.get('key_file') else None, c.get('key_env', 'COMFY_API_KEY'))

    def plan(self):
        _, stream, available = media.video_info(self.resolve(self.c['source']))
        exact = float(int(stream['duration_ts'])*Fraction(stream['time_base'])) if stream.get('duration_ts') is not None else available
        duration = self.c.get('duration_seconds', exact)
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 2 <= duration <= 15:
            raise ValueError('Direct routes currently support full clips of 2..15s; no automatic truncation or depth-route segmentation')
        if duration > exact+.001:
            raise ValueError('Requested duration exceeds source')
        return {'route': self.route, 'duration_seconds': duration, 'delivery_frames': math.ceil(duration*24-1e-6),
                'fps': 24, 'external_segments': 1, 'required_anchors': 0,
                'internal_frames': 5+17*math.ceil((duration*24-5)/17) if self.route == 'ref2v' else None,
                'window_frames': self.recipe.get('window_frames'), 'history_frames': self.recipe.get('history_frames')}

    def prepare(self):
        plan = self.plan()
        if (self.out/'prepared/metadata.json').exists():
            meta=self.meta()
            if meta.get('route')==self.route and meta.get('motion_input') and Path(meta['motion_input']).is_file():
                if meta.get('motion_input_sha256')!=sha(meta['motion_input']):raise ValueError('Prepared motion changed; use a new task')
                return
        else:
            media.prepare(SimpleNamespace(source=self.resolve(self.c['source']), duration=plan['duration_seconds'],
                          width=self.recipe.get('width', 768), height=self.recipe.get('height', 1344), sheet_fps=4, out=self.out/'prepared'))
        meta = self.meta()
        meta.update(route=self.route, required_anchors=0, delivery_frames=plan['delivery_frames'])
        if self.route == 'ref2v':
            dest = self.out/'prepared/motion-reference.mp4'
            edge = self.recipe['motion_reference_long_edge']
            def encode(path,resumable=False):
                command=['ffmpeg', *(['-y'] if resumable else []), '-v', 'error', '-i', str(self.resolve(self.c['source'])), '-vf',
                    f'fps=24,scale={edge}:{edge}:force_original_aspect_ratio=decrease:force_divisible_by=32,tpad=stop_mode=clone:stop_duration=1',
                    '-frames:v', str(plan['internal_frames']), '-an', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', str(path)]
                (media.run if resumable else run)(command)
            if (self.out/'prepared/.stage.json').exists():
                stage=Stage(self.out/'prepared',{'operation':'prepare','source':input_file(self.resolve(self.c['source'])),'duration':plan['duration_seconds'],
                    'width':self.recipe['width'],'height':self.recipe['height'],'sheet_fps':4})
                with stage.locked():stage.file('reference-motion',dest,lambda p:encode(p,True))
            else:encode(dest)
            _, stream, _ = media.video_info(dest)
            meta.update(motion_input=str(dest.resolve()), motion_reference_resolution=[stream['width'], stream['height']])
        else:
            meta.update(motion_input=str(self.resolve(self.c['source']).resolve()), generation_resolution='backend adaptive '+self.recipe['resolution_preset'])
        meta['motion_input_sha256']=sha(meta['motion_input'])
        write(self.out/'prepared/metadata.json', meta)

    def compile(self):
        if not self.c.get('prompt') and self.c.get('performance'):
            return super().compile()
        text = self.c.get('prompt')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Supply an explicit role prompt, or performance.json; no case prompt is inserted automatically')
        path = self.out/'prompt.txt'
        if path.exists() and path.read_text(encoding='utf-8') != text:
            raise ValueError('Prompt changed; use a new task directory')
        path.write_text(text, encoding='utf-8')

    def preview(self):
        self.prepare()
        self.compile()
        g, ids = direct_graph(self.recipe, 'identity.png', 'motion.mp4', (self.out/'prompt.txt').read_text(encoding='utf-8-sig'),
                              self.meta()['duration_seconds'], self.record['seed'], 'DanceReplica/'+self.record['token']+'/video')
        write(self.out/'plan/graph.json', g)
        write(self.out/'plan/recipe.json', self.recipe)
        return g, ids

    def validate(self):
        client = self.client()
        if isinstance(client, WorkflowClient):
            result = client.capabilities(self.recipe)
        else:
            graph, _ = self.preview()
            validate_graph(client, graph)
            write(self.out/'plan/validated-graph.json', graph)
            result = {'route': self.route, 'validation_scope': 'node/schema/model selectors only', 'graph_checked': True}
        result.update(generation_submitted=False, visual_quality_verified=False)
        write(self.out/'plan/validation.json', result)
        return result

    def render(self):
        self.prepare()
        self.compile()
        u = self.client()
        directory = self.out/'render'
        prompt = (self.out/'prompt.txt').read_text(encoding='utf-8')
        uploads = directory/'uploads.json'
        if uploads.exists():
            remote = load(uploads)
            if remote['base'] != u.base:
                raise ValueError('Upload backend changed; use a new task directory')
        else:
            if not isinstance(u, WorkflowClient):
                g, _ = self.preview()
                validate_graph(u, g)
                u.idle()
            source = self.resolve(self.c['source']) if isinstance(u, WorkflowClient) else Path(self.meta()['motion_input'])
            remote = {'base': u.base, 'identity': u.upload(self.resolve(self.c['identity'])), 'motion': u.upload(source)}
            write(uploads, remote)
        if isinstance(u, WorkflowClient):
            r = self.recipe
            body = dict(prompt=prompt, duration_seconds=self.meta()['duration_seconds'], seed=self.record['seed'],
                        motion_video=remote['motion'], steps=r['steps'], audio=self.c.get('audio', 'source'), timeout_seconds=self.c.get('timeout_seconds', 7200))
            if self.route == 'ref2v':
                body.update(workflow='video.reference', images=[remote['identity']], width=r['width'], height=r['height'],
                            profile='fast', lora_strength=r['lora_strength'], sage=r['sage'], motion_reference_long_edge=r['motion_reference_long_edge'])
            else:
                body.update(workflow='video.character_swap', identity_image=remote['identity'],
                            resolution_preset=r['resolution_preset'], mask_expansion=r['mask_expansion'])
            files = u.stage(directory/'job', body, r, timeout=self.c.get('timeout_seconds', 7200))
        else:
            gp = directory/'graph.json'
            if gp.exists():
                g = load(gp)
                ids = ['save'] if self.route == 'character_swap' else ['17']
            else:
                g, ids = direct_graph(self.recipe, remote['identity'], remote['motion'], prompt,
                                     self.meta()['duration_seconds'], self.record['seed'], 'DanceReplica/'+self.record['token']+'/video')
                validate_graph(u, g)
                write(gp, g)
            files = u.stage(directory/'job', g, ids, timeout=self.c.get('timeout_seconds', 7200))
        write(directory/'result.json', {'route': self.route, 'raw_video': str(files[0].resolve()), 'recipe': self.recipe})

    def finish(self):
        if media.verified_delivery(self.out/'delivery'):
            return
        raw = Path(load(self.out/'render/result.json')['raw_video'])
        tail_hold=0
        if self.route == 'character_swap':
            _, stream, duration = media.video_info(raw)
            missing = self.meta()['delivery_frames']-int(stream.get('nb_frames') or round(duration*24))
            if missing > 1:
                raise ValueError('Character Swap output too short; refusing to hide missing content')
            if missing == 1:
                tail_hold=1
                padded = self.out/'render/tail-held.mp4'
                if not padded.exists():
                    run(['ffmpeg', '-v', 'error', '-i', str(raw), '-vf', 'tpad=stop_mode=clone:stop_duration=0.05',
                         '-frames:v', str(self.meta()['delivery_frames']), '-an', '-c:v', 'libx264', '-crf', '18', str(padded)])
                raw = padded
        media.finish(SimpleNamespace(raw=raw, source=self.resolve(self.c['source']), duration=self.meta()['duration_seconds'],
                                     out=self.out/'delivery', audio=self.c.get('audio', 'source')))
        path=self.out/'delivery/verification.json';report=load(path)
        report.update(route=self.route,recipe=self.recipe,tail_hold_frames=tail_hold)
        write(path,report)

    def compare(self):
        from compare import compare,verified_comparison
        source=self.resolve(self.c['source']);result=self.out/'delivery/result.mp4';directory=self.out/'comparison'
        performance=self.resolve(self.c['performance']) if self.c.get('performance') else None
        if verified_comparison(directory,source,result,performance):return
        compare(source,result,directory,performance,source_duration=self.meta()['duration_seconds'])

    def status(self):
        return {'route': self.route, 'out': str(self.out), 'recipe': self.recipe,
                'prepared': (self.out/'prepared/metadata.json').exists(), 'prompt': (self.out/'prompt.txt').exists(),
                'rendered': (self.out/'render/result.json').exists(), 'delivered': (self.out/'delivery/result.mp4').exists(),
                'compared': (self.out/'comparison/verification.json').exists()}
