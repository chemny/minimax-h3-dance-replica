#!/usr/bin/env python3
"""Plan shared anchors before images; orchestrate bounded H3 segments on one source clock."""
import json, math, subprocess
from pathlib import Path
from types import SimpleNamespace
import media
from native_client import write, sha
from stages import Stage,input_file

FPS=24

def plan(duration, target=10.0, maximum=15.0):
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in (duration,target,maximum)):
        raise ValueError('Durations must be finite numbers')
    if duration<2 or not 4<=target<=maximum<=15:
        raise ValueError('Use duration >=2s and 4<=target<=maximum<=15s')
    count=math.ceil(duration*FPS-1e-6)
    # One shared source frame per join. Target is approximate; hard maximum includes this frame.
    hard=math.floor(maximum*FPS+1e-6)
    n=max(1,math.ceil(count/(target*FPS)),math.ceil((count-1)/(hard-1)))
    boundaries=[round((count-1)*i/n) for i in range(n+1)]
    anchors=[{'id':f'boundary-{i:03}', 'source_frame':f,'source_seconds':f/FPS,
              'source_image':f'boundaries/source-{i:03}.png','image':f'boundaries/anchor-{i:03}.png'}
             for i,f in enumerate(boundaries)]
    segments=[]
    for i,(a,b) in enumerate(zip(boundaries,boundaries[1:])):
        frames=b-a+1
        if not 2<=frames/FPS<=maximum:raise ValueError('Segment duration outside backend range')
        segments.append({'id':i,'start_frame':a,'end_frame_inclusive':b,
                         'start_seconds':a/FPS,'end_boundary_seconds':b/FPS,
                         'delivery_frames':frames,'duration_seconds':frames/FPS,
                         'first_anchor':anchors[i]['id'],'last_anchor':anchors[i+1]['id'],
                         'drop_first_frame_on_join':i>0})
    return {'fps':FPS,'source_duration_seconds':duration,'delivery_frames':count,
            'target_seconds':target,'hard_max_seconds':maximum,'segment_count':n,
            'unique_anchor_count':n+1,'boundaries':anchors,'segments':segments,
            'overlap_frames_per_join':1,'note':'Target is approximate; shared endpoint adds up to one frame. All actual segment durations include overlap and obey hard maximum. No speed change.'}


def ffmpeg(args):subprocess.run(['ffmpeg','-nostdin','-n','-v','error',*args],check=True)


class SegmentedPipeline:
    def __init__(self,config,create=True):
        self.path=Path(config).resolve();self.c=json.loads(self.path.read_text(encoding='utf-8-sig'));self.base=self.path.parent
        self.resolve=lambda s:Path(s).expanduser() if Path(s).expanduser().is_absolute() else self.base/Path(s).expanduser()
        self.out=self.resolve(self.c['out']);self.source=self.resolve(self.c['source'])
        _,_,available=media.video_info(self.source);duration=self.c.get('duration_seconds',available)
        if duration>available+.05:raise ValueError('Requested source duration unavailable')
        settings=self.c.get('segmentation',{});self.p=plan(duration,settings.get('target_seconds',10.),settings.get('max_seconds',15.))
        self.manifest=self.out/'segmentation.json'
        spec={k:v for k,v in self.c.items() if k!='connection'}
        record={'spec':spec,'source_sha256':sha(self.source),'plan':self.p}
        if self.manifest.exists():
            old=json.loads(self.manifest.read_text(encoding='utf-8-sig'))
            if old!=record:raise ValueError('Segmented task inputs/settings changed; use a new task directory')
        elif (self.out/'task.json').exists():raise ValueError('Existing single-clip task: use a new output directory for segmentation')
        elif create:write(self.manifest,record)
    def configs(self):return [self.out/'segments'/f'{s["id"]:03}'/'config.json' for s in self.p['segments']]
    def children(self):
        from dance import Pipeline
        return [Pipeline(p) for p in self.configs()]
    def prepare(self):
        width=self.c.get('width',768);height=self.c.get('height',1344)
        if any(type(x) is not int or x<256 or x%32 for x in (width,height)) or width*height>768*1344:raise ValueError('Invalid generation dimensions')
        if any(self.c.get(k) for k in ('first','last','depth')):
            raise ValueError('For segmented tasks supply identity and shared boundary images; single first/last/depth paths cannot be silently reused across segments')
        normalized=self.out/'motion-24fps.mp4'
        if not normalized.exists():
            normalized.parent.mkdir(parents=True,exist_ok=True)
            ffmpeg(['-i',str(self.source),'-vf',f'fps=24,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,tpad=stop_mode=clone:stop_duration=1','-frames:v',str(self.p['delivery_frames']),'-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(normalized)])
        for anchor in self.p['boundaries']:
            dest=self.out/anchor['source_image'];dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():ffmpeg(['-i',str(normalized),'-vf',f'select=eq(n\\,{anchor["source_frame"]})','-frames:v','1',str(dest)])
        for s,config in zip(self.p['segments'],self.configs()):
            config.parent.mkdir(parents=True,exist_ok=True);src=config.parent/'source.mp4'
            if not src.exists():ffmpeg(['-i',str(normalized),'-vf',f'trim=start_frame={s["start_frame"]}:end_frame={s["end_frame_inclusive"]+1},setpts=PTS-STARTPTS','-an','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',str(src)])
            c={k:v for k,v in self.c.items() if k in ('route','connection','steps','width','height','depth_strength','control_end','wardrobe_description','model_overrides','sage','lora_strength')}
            c['connection']=dict(c.get('connection',{}))
            if c['connection'].get('key_file'):c['connection']['key_file']=str(self.resolve(c['connection']['key_file']).resolve())
            for k in ('identity','wardrobe_reference'):
                if self.c.get(k):c[k]=str(self.resolve(self.c[k]).resolve())
            i=s['id'];c.update(source=str(src.resolve()),out='case',performance='performance.json',duration_seconds=s['duration_seconds'],first=str((self.out/self.p['boundaries'][i]['image']).resolve()),last=str((self.out/self.p['boundaries'][i+1]['image']).resolve()),segmentation={'enabled':False})
            if self.c.get('seed') is not None:c['seed']=(self.c['seed']+i)%(2**64)
            if config.exists() and json.loads(config.read_text(encoding='utf-8-sig'))!=c:raise ValueError('Child task changed; use new directory')
            if not config.exists():write(config,c)
        for child in self.children():child.prepare()
    def anchors(self):
        self.prepare();frames=[];complete=True
        for a in self.p['boundaries']:
            src=self.out/a['source_image'];dest=self.out/a['image']
            if not dest.exists():complete=False
            else:
                from PIL import Image
                with Image.open(dest) as im:im.verify()
            frames.append({**a,'source_image':str(src.resolve()),'image':str(dest.resolve())})
        write(self.out/'boundaries/request.json',{'provider':'Codex imagegen','identity':str(self.resolve(self.c['identity']).resolve()) if self.c.get('identity') else None,'wardrobe_reference':str(self.resolve(self.c['wardrobe_reference']).resolve()) if self.c.get('wardrobe_reference') else None,'wardrobe_description':self.c.get('wardrobe_description'),'frames':frames,'unique_image_count':len(frames),'instructions':'Generate each unique boundary once using its source pose/framing and the same approved identity/clothes. Each interior image is shared byte-for-byte by preceding last and following first. Do not generate these twice. Review all boundaries before any segment rendering; no extra intermediate guides.'})
        if not complete:print('PAUSED: create/review all shared images in boundaries/request.json')
        return complete
    def compile(self):
        from prompt import compile_record
        source=self.resolve(self.c['performance'])
        if not source.exists():raise ValueError('Supply full-source performance.json after reviewing all segment contact sheets')
        d=json.loads(source.read_text(encoding='utf-8-sig'));compile_record(d)
        if abs(d['duration_seconds']-self.p['source_duration_seconds'])>1e-4:raise ValueError('Full-source performance duration mismatch')
        for s,config in zip(self.p['segments'],self.configs()):
            start=s['start_seconds'];stop=min(start+s['duration_seconds'],d['duration_seconds'])
            parts=[]
            for event in d['segments']:
                a=max(start,event['start']);b=min(stop,event['end'])
                if b>a+1e-8:parts.append({**event,'start':a-start,'end':b-start})
            if not parts:raise ValueError('Segment performance missing')
            # 24fps rounding may extend <=1 frame beyond source; hold the last observed state only.
            parts[0]['start']=0.;parts[-1]['end']=s['duration_seconds']
            local={**d,'duration_seconds':s['duration_seconds'],'segments':parts,'source_offset_seconds':start,'boundary_note':'Clipped from observed source timeline; review continuity and crossing actions, do not invent a new action at segment start.'}
            compile_record(local);dest=config.parent/'performance.json'
            if dest.exists() and json.loads(dest.read_text(encoding='utf-8-sig'))!=local:raise ValueError('Performance changed; use new task directory')
            if not dest.exists():write(dest,local)
        for child in self.children():child.compile()
    def approve(self,item):
        if item=='anchors' and not self.anchors():raise ValueError('Missing shared anchors')
        for child in self.children():child.approve(item)
    def depth(self):
        self.prepare()
        for child in self.children():child.depth()
    def render(self):
        self.compile();children=self.children()
        if not self.anchors() or any(not c.reviewed('anchors') or not c.reviewed('depth') for c in children):raise ValueError('Review all shared anchors and each segment depth before rendering')
        for child in children:child.render();child.finish()
    def finish(self):
        if media.verified_delivery(self.out/'delivery'):return
        paths=[]
        for child in self.children():
            child.finish();paths.append(child.out/'delivery/result.mp4')
        parts=self.out/'join'
        stage=Stage(parts,{'operation':'join','segments':[input_file(p) for p in paths],'plan':self.p})
        with stage.locked():return self._join(paths,parts,stage)
    def _join(self,paths,parts,stage):
        trimmed=[]
        for i,(src,s) in enumerate(zip(paths,self.p['segments'])):
            dest=parts/f'{i:03}.mp4';start=1 if i else 0
            stage.file(f'trim-{i}',dest,lambda p,src=src,start=start,s=s:media.run(['ffmpeg','-y','-v','error','-i',str(src),'-vf',f'trim=start_frame={start}:end_frame={s["delivery_frames"]},setpts=PTS-STARTPTS','-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(p)]))
            trimmed.append(dest)
        # Fixed simple filenames avoid concat quoting issues on arbitrary user paths.
        listing=parts/'concat.txt';listing.write_text(''.join(f"file '{p.name}'\n" for p in trimmed),encoding='utf-8')
        raw=parts/'joined.mp4'
        stage.file('concat',raw,lambda p:media.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(listing),'-an','-c','copy',str(p)]))
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=nb_read_frames','-of','json',str(raw)]))['streams'][0]
        if int(info['nb_read_frames'])!=self.p['delivery_frames']:raise ValueError('Joined frame count mismatch; do not hide gaps/overlaps')
        media.finish(SimpleNamespace(raw=raw,source=self.source,duration=self.p['source_duration_seconds'],out=self.out/'delivery'))
        write(parts/'verification.json',{'frames':self.p['delivery_frames'],'dropped_duplicate_frames':self.p['segment_count']-1,'source_audio_restored_once':True,'speed_change':False,'crossfade':False,'joins_require_visual_review':True})
        stage.complete()
    def compare(self):
        from compare import compare,verified_comparison
        out=self.out/'comparison'
        source=self.source;result=self.out/'delivery/result.mp4'
        performance=self.resolve(self.c['performance'])
        if verified_comparison(out,source,result,performance):return
        compare(source,result,out,performance,source_duration=self.p['source_duration_seconds'],interval=max(1.,self.p['source_duration_seconds']/180))
    def status(self):
        return {'out':str(self.out),'segment_count':self.p['segment_count'],'unique_anchor_count':self.p['unique_anchor_count'],'plan':self.p,'prepared':all(p.exists() for p in self.configs()),'delivered':(self.out/'delivery/verification.json').exists()}


def selected(config):
    c=json.loads(Path(config).read_text(encoding='utf-8-sig'));settings=c.get('segmentation',{})
    if c.get('route','depth')!='depth':return False
    if not settings.get('enabled',True):return False
    base=Path(config).resolve().parent;out=Path(c['out']);out=out if out.is_absolute() else base/out
    # Existing single-clip jobs must remain resumable under their original graph/settings.
    if (out/'task.json').exists() and not (out/'segmentation.json').exists():return False
    source=Path(c['source']);source=source if source.is_absolute() else base/source
    _,_,duration=media.video_info(source)
    return c.get('duration_seconds',duration)>settings.get('target_seconds',10.)
