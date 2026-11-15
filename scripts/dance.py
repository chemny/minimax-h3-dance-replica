#!/usr/bin/env python3
"""Unified portable dance workflow. GPU stages require --execute; visual checks belong to the agent."""
import argparse
import json
import math
from pathlib import Path
import secrets
import subprocess
import sys
from types import SimpleNamespace

from native_client import Client,write,sha,validate_graph
from h3_run import build,node
import media
from prompt import compile_record
from profiles import GENERATION as G, DEPTH, PRESET
from routes import name as route_name, recipe as route_recipe, NAMES
from runtime_checks import require_client, configure_stdio


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def run(args):subprocess.run([args[0],'-nostdin','-n',*args[1:]] if args[0]=='ffmpeg' else args,check=True)


def depth_graph(source,prefix,settings=None):
    settings=settings or DEPTH
    return {
        '1':node('LoadVideo',file=source),
        '2':node('GetVideoComponents',video=['1',0]),
        '3':node('LoadDA3Model',model_name=settings['model'],weight_dtype=settings['dtype']),
        '4':node('DA3Inference',da3_model=['3',0],image=['2',0],resolution=settings['resolution'],resize_method=settings['resize_method'],mode='mono'),
        '5':node('DA3Render',da3_geometry=['4',0],output='depth',**{'output.normalization':settings['normalization'],'output.apply_sky_clip':settings['apply_sky_clip']}),
        '6':node('CreateVideo',images=['5',0],fps=24.,bit_depth=8,color_space='sRGB',codec='none'),
        '7':node('SaveVideo',video=['6',0],filename_prefix=prefix,format='mp4',**{'format.codec':'h264'})}



class Pipeline:
    def __init__(self,config,create=True):
        self.path=Path(config).resolve();self.c=load(self.path);self.base=self.path.parent
        self.route=route_name(self.c);self.recipe=route_recipe(self.c)
        def resolve(s):
            p=Path(s).expanduser();return p if p.is_absolute() else self.base/p
        self.resolve=resolve;self.out=resolve(self.c['out'])
        if create:
            for field in ('source','identity','wardrobe_reference'):
                if self.c.get(field) and not resolve(self.c[field]).is_file():raise ValueError('Input file missing: '+field)
        # Store immutable task specification excluding auth; changing input/settings requires a new case.
        public={k:v for k,v in self.c.items() if k!='connection'}
        manifest=self.out/'task.json'
        if manifest.exists():
            self.record=load(manifest)
            if self.record['spec']!=public:raise ValueError('Task configuration changed; create a new output directory')
            if 'route_recipe' in self.record:
                frozen=any((self.out/p).exists() for p in ('render/graph.json','render/job/job.json','render/result.json'))
                if self.record['route_recipe']!=self.recipe and not frozen:raise ValueError('Route recipe changed before rendering; use a new task directory')
                self.recipe=self.record['route_recipe']
            elif self.record.get('backend_preset',PRESET)!=PRESET and not (self.out/'render/graph.json').exists():raise ValueError('Backend preset changed before render; use a new task directory')
        else:
            self.record={'spec':public,'seed':self.c.get('seed',secrets.randbits(63)),'token':secrets.token_hex(8),'backend_preset':PRESET,'route_recipe':self.recipe}
            inputs={}
            for k in ('source','identity','wardrobe_reference','first','last','depth','performance'):
                if self.c.get(k) and resolve(self.c[k]).is_file():inputs[k]=sha(resolve(self.c[k]))
            self.record['input_sha256']=inputs
            if create:write(manifest,self.record)
        if type(self.record['seed']) is not int or not 0<=self.record['seed']<2**64:raise ValueError('seed must be an integer in 0..2^64-1')
        if create:write(self.out/'recipe.json',self.recipe)
        for k,h in self.record['input_sha256'].items():
            if not resolve(self.c[k]).is_file() or sha(resolve(self.c[k]))!=h:raise ValueError('Input changed: '+k+'; create a new task')
    def client(self):
        c=self.c.get('connection',{})
        if c.get('kind','native')!='native':raise ValueError('This runner needs native ComfyUI graph API. A workflow gateway requires an explicit adapter; no silent workflow substitution.')
        return Client(c.get('url'),self.resolve(c['key_file']) if c.get('key_file') else None,c.get('key_env','COMFY_API_KEY'))
    def meta(self):return load(self.out/'prepared/metadata.json')
    def prepare(self):
        if (self.out/'prepared/metadata.json').exists():return
        media.prepare(SimpleNamespace(source=self.resolve(self.c['source']),duration=self.c.get('duration_seconds'),width=self.c.get('width',G['width']),height=self.c.get('height',G['height']),sheet_fps=4,out=self.out/'prepared'))
    def compile(self):
        record=self.resolve(self.c['performance'])
        if not record.is_file():raise ValueError('Review contact sheets and create performance.json before continuing')
        d=load(record)
        if abs(d['duration_seconds']-self.meta()['duration_seconds'])>1e-4:raise ValueError('Performance duration mismatch')
        text=compile_record(d);path=self.out/'prompt.txt'
        if path.exists() and path.read_text(encoding='utf-8')!=text:raise ValueError('Performance changed; use new task directory')
        path.write_text(text,encoding='utf-8')
        hashes=self.out/'performance-check.json';current={'sha256':sha(record)}
        if hashes.exists() and load(hashes)!=current:raise ValueError('Performance record changed')
        write(hashes,current)
    def anchor_paths(self):
        return {k:self.resolve(self.c[k]) if self.c.get(k) else self.out/'anchors'/(k+'.png') for k in ('first','last')}
    def anchors(self):
        paths=self.anchor_paths()
        missing=[k for k,v in paths.items() if not v.is_file()]
        if missing:
            if not self.c.get('identity') or not self.resolve(self.c['identity']).is_file():
                raise ValueError('Supply the target identity photo for Codex image editing, or both existing first/last files')
            if self.c.get('wardrobe_reference') and not self.resolve(self.c['wardrobe_reference']).is_file():raise ValueError('Wardrobe reference missing')
            write(self.out/'anchors/request.json',{
                'provider':'Codex image tool (agent action; not callable from this Python CLI)',
                'identity':str(self.resolve(self.c['identity']).resolve()),
                'wardrobe_reference':str(self.resolve(self.c['wardrobe_reference']).resolve()) if self.c.get('wardrobe_reference') else None,
                'wardrobe_description':self.c.get('wardrobe_description'),
                'roles':{'source':'pose, framing, scene, occlusion','identity':'adult face and hair; clothing only if no separate wardrobe reference','wardrobe':'garment only, never this model face, hair, pose or background'},
                'frames':{k:{'source':str((self.out/'prepared'/('source-'+k+'.png')).resolve()),'destination':str(paths[k].resolve())} for k in missing},
                'instructions':'Use the original video frame as the editing canvas and identity photo for adult identity/hair; separate wardrobe reference, when provided, supplies clothing only and must not change identity. Keep source pose, head direction, hands, framing, subject scale, background and occlusion. Do not copy the reference photo standing pose. Review before approve.',
                'reference':'references/images.md'})
            print('PAUSED: Codex must edit missing anchors using its image tool; see anchors/request.json')
            return False
        from PIL import Image
        for path in paths.values():
            with Image.open(path) as image:image.verify()
        return True
    def approve(self,item):
        paths=self.anchor_paths() if item=='anchors' else {'depth':self.depth_path()}
        for path in paths.values():
            if not path.is_file():raise ValueError('Review file missing')
        write(self.out/('review-'+item+'.json'),{k:sha(v) for k,v in paths.items()})
    def reviewed(self,item):
        path=self.out/('review-'+item+'.json')
        if not path.exists():return False
        files=self.anchor_paths() if item=='anchors' else {'depth':self.depth_path()}
        return load(path)=={k:sha(v) for k,v in files.items()}
    def depth_path(self):return self.resolve(self.c['depth']) if self.c.get('depth') else self.out/'depth/depth.mp4'
    def depth(self):
        if self.c.get('depth'):
            self.validate_depth();return
        m=self.meta();directory=self.out/'depth';dest=directory/'depth.mp4'
        if dest.exists():self.validate_depth();return
        directory.mkdir(parents=True,exist_ok=True);motion=directory/'motion-delivery.mp4'
        if not motion.exists():run(['ffmpeg','-v','error','-i',str(self.out/'prepared/motion-24fps.mp4'),'-frames:v',str(m['delivery_frames']),'-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',str(motion)])
        u=self.client();gp=directory/'graph.json'
        if gp.exists():g=load(gp)
        else:
            g=depth_graph('motion.mp4','DanceReplica/'+self.record['token']+'/depth',self.recipe['depth']);validate_graph(u,g);u.idle()
            remote=u.upload(motion);g=depth_graph(remote,'DanceReplica/'+self.record['token']+'/depth',self.recipe['depth']);validate_graph(u,g);write(gp,g)
        raw=u.stage(directory/'job',g,['7'])[0]
        partial=directory/'depth.partial.mp4'
        if partial.exists():raise ValueError('Incomplete local depth output; inspect and remove .partial file before retry')
        run(['ffmpeg','-v','error','-i',str(raw),'-vf','fps=24,tpad=stop_mode=clone:stop_duration=1','-frames:v',str(m['internal_frames']),'-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',str(partial)])
        partial.replace(dest);self.validate_depth()
        write(directory/'verification.json',{'renderer':'DA3Render','normalization':DEPTH['normalization'],'apply_sky_clip':DEPTH['apply_sky_clip'],'inference_resolution':DEPTH['resolution'],'internal_frames':m['internal_frames'],'tail_padding_only':True,'transport':'native API','gpu_seed_control':'DA3Render node has no explicit seed input; pixel-exact render reproducibility not promised'})
    def validate_depth(self):
        m=self.meta();_,s,d=media.video_info(self.depth_path())
        if [s['width'],s['height']]!=m['resolution'] or s.get('avg_frame_rate')!='24/1' or d+.01<m['internal_frames']/24:raise ValueError('Depth size/fps/duration mismatch')
        run(['ffmpeg','-v','error','-i',str(self.depth_path()),'-f','null','-'])
    def render(self):
        if not self.reviewed('anchors') or not self.reviewed('depth'):raise ValueError('Agent must visually review and approve current anchors/depth before render')
        self.compile();self.validate_depth();m=self.meta();u=self.client();directory=self.out/'render';gp=directory/'graph.json'
        if gp.exists():g=load(gp)
        else:
            prompt=(self.out/'prompt.txt').read_text(encoding='utf-8');paths={**self.anchor_paths(),'depth':self.depth_path()}
            g=build('first.png','last.png','depth.mp4',prompt,m['duration_seconds'],*m['resolution'],self.recipe['steps'],self.record['seed'],self.recipe['depth_strength'],self.recipe['control_end'],recipe=self.recipe)
            validate_graph(u,g);u.idle();remote={k:u.upload(v) for k,v in paths.items()}
            g=build(remote['first'],remote['last'],remote['depth'],prompt,m['duration_seconds'],*m['resolution'],self.recipe['steps'],self.record['seed'],self.recipe['depth_strength'],self.recipe['control_end'],prefix='DanceReplica/'+self.record['token']+'/video',recipe=self.recipe)
            validate_graph(u,g);write(gp,g)
        files=u.stage(directory/'job',g,['17']);write(directory/'result.json',{'raw_video':str(files[0].resolve())})
    def finish(self):
        if media.verified_delivery(self.out/'delivery'):return
        media.finish(SimpleNamespace(raw=Path(load(self.out/'render/result.json')['raw_video']),source=self.resolve(self.c['source']),duration=self.meta()['duration_seconds'],out=self.out/'delivery'))
    def compare(self):
        from compare import compare,verified_comparison
        directory=self.out/'comparison'
        source=self.resolve(self.c['source']);result=self.out/'delivery/result.mp4'
        performance=self.resolve(self.c['performance'])
        if verified_comparison(directory,source,result,performance):return
        compare(source,result,directory,performance,source_duration=self.meta()['duration_seconds'])
    def status(self):
        return {'route':self.route,'recipe':self.recipe,'out':str(self.out),'prepared':(self.out/'prepared/metadata.json').exists(),'prompt':(self.out/'prompt.txt').exists(),'anchors_reviewed':self.reviewed('anchors'),'depth_reviewed':self.reviewed('depth'),'rendered':(self.out/'render/result.json').exists(),'delivered':(self.out/'delivery/verification.json').exists() and (self.out/'delivery/result.mp4').is_file(),'compared':(self.out/'comparison/verification.json').exists()}

    def preview(self):
        self.prepare();self.compile();m=self.meta()
        g=build('first.png','last.png','depth.mp4',(self.out/'prompt.txt').read_text(encoding='utf-8-sig'),m['duration_seconds'],*m['resolution'],self.recipe['steps'],self.record['seed'],self.recipe['depth_strength'],self.recipe['control_end'],recipe=self.recipe)
        write(self.out/'plan/graph.json',g);write(self.out/'plan/recipe.json',self.recipe)
        return g,['17']

    def validate(self):
        g,_=self.preview();u=self.client();validate_graph(u,g)
        validate_graph(u,depth_graph('motion.mp4','DanceReplica/check',self.recipe['depth']))
        write(self.out/'plan/validated-graph.json',g)
        result={'route':'depth','validation_scope':'node/schema/model selectors only','generation_submitted':False,'visual_quality_verified':False}
        write(self.out/'plan/validation.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['routes','init','check','plan','build','validate','prepare','compile','anchors','depth','approve','render','finish','compare','run','status'])
    p.add_argument('--config',type=Path);p.add_argument('--route',choices=NAMES,help='For init only; other actions use the saved config route')
    p.add_argument('--execute',action='store_true',help='Authorize GPU stages within the already agreed task');p.add_argument('--item',choices=['anchors','depth'])
    a=p.parse_args()
    if a.action=='routes':print(json.dumps({n:route_recipe({'route':n}) for n in NAMES},ensure_ascii=False,indent=2));return
    if not a.config:p.error('--config required')
    if a.action=='init':
        if a.config.exists():raise ValueError('Config exists')
        route=a.route or 'depth';r=route_recipe({'route':route})
        c={'route':route,'connection':{'kind':'native','url':'http://127.0.0.1:8188','key_env':'COMFY_API_KEY'},'source':'source.mp4','identity':'identity.png','out':'case-output','steps':r['steps']}
        if route=='depth':c.update(performance='performance.json',width=r['width'],height=r['height'])
        elif route=='ref2v':c.update(prompt='',width=r['width'],height=r['height'],motion_reference_long_edge=r['motion_reference_long_edge'])
        else:c.update(prompt='',resolution_preset=r['resolution_preset'],mask_expansion=r['mask_expansion'])
        write(a.config,c);print(a.config);return
    if a.route:p.error('Set route in task config; --route is for init only')
    if a.action in ('depth','render','run') and not a.execute:raise ValueError('Use --execute only after generation is authorized; build/validate/check never submit GPU jobs')
    if a.action=='check':
        c=load(a.config);connection=c.get('connection',{});args=[sys.executable,str(Path(__file__).with_name('check_environment.py')),'--config',str(a.config),'--out',str(a.config.resolve().parent/'environment-report.json')]
        args+=['--native-key-env',connection.get('key_env','COMFY_API_KEY')]
        if connection.get('key_file'):
            key=Path(connection['key_file']);key=key if key.is_absolute() else a.config.resolve().parent/key
            args+=['--native-key-file',str(key)]
        raise SystemExit(subprocess.call(args))
    if a.action in ('prepare','build','validate','render','run','finish','compare','depth'):
        require_client()
    c=load(a.config)
    if route_name(c)!='depth':
        from direct_routes import DirectPipeline
        if a.action in ('anchors','depth','approve'):raise ValueError('Selected direct route has no anchors/depth stages')
        flow=DirectPipeline(a.config,create=a.action!='status')
        if a.action=='plan':print(json.dumps(flow.plan(),ensure_ascii=False,indent=2));return
        if a.action=='build':flow.preview()
        elif a.action=='validate':print(json.dumps(flow.validate(),ensure_ascii=False,indent=2));return
        elif a.action=='run':flow.prepare();flow.compile();flow.render();flow.finish();flow.compare()
        elif a.action=='status':print(json.dumps(flow.status(),ensure_ascii=False,indent=2));return
        else:getattr(flow,a.action)()
        print(json.dumps(flow.status(),ensure_ascii=False,indent=2));return
    from segments import selected, SegmentedPipeline
    if a.action=='plan':
        flow=SegmentedPipeline(a.config)
        print(json.dumps(flow.p,ensure_ascii=False,indent=2));return
    segmented=selected(a.config)
    flow=SegmentedPipeline(a.config,create=a.action!='status') if segmented else Pipeline(a.config,create=a.action!='status')
    if a.action in ('build','validate'):
        if segmented:raise ValueError('Prepare segmented depth children, then build/validate each child config')
        if a.action=='build':flow.preview()
        else:print(json.dumps(flow.validate(),ensure_ascii=False,indent=2))
        return
    if a.action=='approve':
        if not a.item:raise ValueError('--item required')
        flow.approve(a.item);print('Recorded visual review for '+a.item);return
    if a.action=='run' and segmented:
        flow.prepare()
        if not flow.resolve(flow.c['performance']).is_file():print('PAUSED: review all segment sheets and supply full-source performance.json');return
        flow.compile()
        if not flow.anchors():return
        children=flow.children()
        if any(not c.reviewed('anchors') for c in children):print('PAUSED: review all shared boundaries, then approve --item anchors');return
        flow.depth()
        if any(not c.reviewed('depth') for c in children):print('PAUSED: review every segment depth, then approve --item depth');return
        flow.render();flow.finish();flow.compare()
    elif a.action=='run':
        flow.prepare()
        if not flow.resolve(flow.c['performance']).is_file():print('PAUSED: inspect contact sheets and supply performance.json');return
        flow.compile()
        if not flow.anchors():return
        if not flow.reviewed('anchors'):print('PAUSED: visually inspect anchors, then approve --item anchors');return
        flow.depth()
        if not flow.reviewed('depth'):print('PAUSED: visually inspect depth, then approve --item depth');return
        flow.render();flow.finish();flow.compare()
    elif a.action=='status':print(json.dumps(flow.status(),ensure_ascii=False,indent=2));return
    else:getattr(flow,a.action)()
    print(json.dumps(flow.status(),ensure_ascii=False,indent=2))
if __name__=='__main__':configure_stdio();main()
