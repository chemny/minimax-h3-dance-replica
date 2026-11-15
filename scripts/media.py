#!/usr/bin/env python3
"""Prepare timed references or finish a generated clip with original audio; no GPU use."""
import argparse,json,math,subprocess
from pathlib import Path
from profiles import GENERATION as G
from native_client import write, sha
from stages import Stage, input_file
from runtime_checks import require_client, configure_stdio

def run(args):subprocess.run([args[0],'-nostdin',*(['-n'] if '-y' not in args else []),*args[1:]] if args[0]=='ffmpeg' else args,check=True)
def probe(path,packets=False):
    args=['ffprobe','-v','error']
    if packets:args+=['-select_streams','a:0','-show_packets','-show_entries','packet=data_hash','-show_data_hash','sha256']
    else:args+=['-show_format','-show_streams']
    data=json.loads(subprocess.check_output(args+['-of','json',str(path)]))
    if packets:
        # Some FFprobe versions also emit container-specific skip/discard side data.
        # Compare encoded packet bytes, with their count and order, across remuxes.
        hashes=[]
        for packet in data.get('packets',[]):
            value=packet.get('data_hash','')
            if not isinstance(value,str) or not value.startswith('SHA256:') or len(value)!=71 or any(c not in '0123456789abcdef' for c in value[7:].lower()):
                raise ValueError('FFprobe did not return an audio packet SHA-256')
            hashes.append({'data_hash':value})
        if not hashes:raise ValueError('FFprobe returned no audio packets')
        data['packets']=hashes
    return data
def frames(seconds):return 5+17*math.ceil((seconds*24-5)/17)
def output_dir(path):
    if path.exists():raise ValueError('Use a new output directory to preserve existing results')
    path.mkdir(parents=True)
def video_info(path):
    d=probe(path);s=next(x for x in d['streams'] if x['codec_type']=='video')
    duration=float(s.get('duration') or d['format']['duration']);return d,s,duration

def verified_delivery(directory):
    """Validate a cached delivery before a pipeline skips local finishing."""
    directory=Path(directory);record=directory/'verification.json';video=directory/'result.mp4'
    if not record.is_file():return False
    report=json.loads(record.read_text(encoding='utf-8-sig'))
    if not video.is_file() or report.get('full_decode_passed') is not True:
        raise ValueError('Saved delivery is missing or unverified; inspect local outputs, do not resubmit generation')
    expected=report.get('video_sha256')
    checkpoint=directory/'.stage.json'
    if not expected and checkpoint.is_file():
        stage=json.loads(checkpoint.read_text(encoding='utf-8-sig'))
        expected=stage.get('steps',{}).get('mux',{}).get('sha256')
    if expected:
        if sha(video)!=expected:raise ValueError('Completed delivery changed; preserve it and finish the existing raw video into a new local directory')
    else:
        # Legacy reports have no digest. Recheck the media without rewriting it.
        run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'])
        stream=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=width,height,r_frame_rate,nb_read_frames','-of','json',str(video)]))['streams'][0]
        if int(stream['nb_read_frames'])!=report['frames'] or stream['r_frame_rate']!='24/1' or [stream['width'],stream['height']]!=report['resolution']:
            raise ValueError('Legacy delivery no longer matches its verification report')
    return True

def prepare(a):
    require_client()
    d,s,available=video_info(a.source);duration=a.duration if a.duration is not None else available
    if not 2<=duration<=15:raise ValueError('Single-clip path supports 2–15s; plan segments for longer clips')
    if duration>available+.05:raise ValueError('Source video is shorter than requested duration')
    if a.width%32 or a.height%32 or a.width*a.height>768*1344:raise ValueError('Use 32px aligned dimensions, at most 768×1344 pixels')
    n=math.ceil(duration*24);padded=frames(duration);dest=a.out/'motion-24fps.mp4'
    stage=Stage(a.out,{'operation':'prepare','source':input_file(a.source),'duration':duration,'width':a.width,'height':a.height,'sheet_fps':a.sheet_fps})
    with stage.locked():
        return _prepare(a,d,s,duration,n,padded,dest,stage)

def _prepare(a,d,s,duration,n,padded,dest,stage):
    vf=f'fps=24,scale={a.width}:{a.height}:force_original_aspect_ratio=decrease,pad={a.width}:{a.height}:(ow-iw)/2:(oh-ih)/2,tpad=stop_mode=clone:stop_duration=1'
    stage.file('motion',dest,lambda p:run(['ffmpeg','-y','-v','error','-i',str(a.source),'-vf',vf,'-frames:v',str(padded),'-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(p)]))
    for name,idx in [('source-first.png',0),('source-last.png',n-1)]:
        stage.file(name,a.out/name,lambda p,idx=idx:run(['ffmpeg','-y','-v','error','-i',str(dest),'-vf',f'select=eq(n\\,{idx})','-frames:v','1',str(p)]))
    # Contact-sheet creation uses Pillow already present on the current client.
    from PIL import Image,ImageDraw,ImageFont
    rate=a.sheet_fps;sheet_dir=a.out/'review-frames';sheet_dir.mkdir(exist_ok=True)
    paths=[]
    for i in range(math.ceil(duration*rate)):
        path=sheet_dir/f'{i+1:05}.png';index=min(n-1,round(i*24/rate))
        stage.file(f'review-{i}',path,lambda p,index=index:run(['ffmpeg','-y','-v','error','-i',str(dest),'-vf',f'select=eq(n\\,{index}),scale=192:-1','-frames:v','1',str(p)]))
        paths.append(path)
    font=ImageFont.load_default()
    for page,start in enumerate(range(0,len(paths),32)):
        group=paths[start:start+32]
        with Image.open(group[0]) as im:height=im.height
        sheet=Image.new('RGB',(192*8,(height+28)*math.ceil(len(group)/8)),'white');draw=ImageDraw.Draw(sheet)
        for i,path in enumerate(group):
            x=i%8*192;y=i//8*(height+28)
            with Image.open(path) as im:sheet.paste(im,(x,y+28))
            draw.text((x+4,y+6),f'{(start+i)/rate:.3f}s',fill='black',font=font)
        stage.file(f'contact-{page}',a.out/f'contact-{page+1:02}.jpg',lambda p:sheet.save(p,quality=95))
    meta={'source':str(a.source.resolve()),'source_fps':s.get('avg_frame_rate'),'source_resolution':[s['width'],s['height']],'duration_seconds':duration,'delivery_frames':n,'internal_frames':padded,'fps':24,'resolution':[a.width,a.height],'motion':str(dest.resolve()),'source_has_audio':any(x['codec_type']=='audio' for x in d['streams']),'sampling_note':'contact sheets are observations, not automatic action recognition; densify near changes'}
    metadata=a.out/'metadata.json'
    if metadata.exists():meta={**json.loads(metadata.read_text(encoding='utf-8-sig')),**meta}
    write(metadata,meta);stage.complete();print(json.dumps(meta,ensure_ascii=True));return meta

def finish(a):
    require_client()
    if not math.isfinite(a.duration) or a.duration<2:raise ValueError('delivery duration must be finite and >=2s')
    raw_info,vs,raw_duration=video_info(a.raw)
    if raw_duration+.001<math.ceil(a.duration*24)/24:raise ValueError('Generated clip is too short')
    stage=Stage(a.out,{'operation':'finish','raw':input_file(a.raw),'source':input_file(a.source),'duration':a.duration,'audio':getattr(a,'audio','source')})
    with stage.locked():return _finish(a,vs,stage)

def _finish(a,vs,stage):
    n=math.ceil(a.duration*24);pic=a.out/'picture.mp4';music=a.out/'source-audio.mka';final=a.out/'result.mp4'
    stage.file('picture',pic,lambda p:run(['ffmpeg','-y','-v','error','-i',str(a.raw),'-map','0:v:0','-frames:v',str(n),'-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(p)]))
    audio=getattr(a,'audio','source')!='none' and any(x['codec_type']=='audio' for x in probe(a.source)['streams'])
    cmd=['ffmpeg','-v','error','-i',str(pic)]
    if audio:
        stage.file('music',music,lambda p:run(['ffmpeg','-y','-v','error','-i',str(a.source),'-t',str(a.duration),'-vn','-map','0:a:0','-c:a','copy',str(p)]))
        cmd+=['-i',str(music),'-map','0:v:0','-map','1:a:0']
    # No -t or -frames on this mux step: keep the copied audio encoder tail.
    stage.file('mux',final,lambda p:run(cmd[:1]+['-y']+cmd[1:]+['-c','copy','-movflags','+faststart',str(p)]))
    run(['ffmpeg','-v','error','-i',str(final),'-f','null','-'])
    q=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=width,height,r_frame_rate,nb_read_frames','-of','json',str(final)]))['streams'][0]
    if int(q['nb_read_frames'])!=n or q['r_frame_rate']!='24/1':raise ValueError('Unexpected frame count/fps')
    exact=None
    if audio:
        exact=probe(music,True)['packets']==probe(final,True)['packets']
        if not exact:raise ValueError('Source audio packets changed')
    report={'video':str(final.resolve()),'video_sha256':sha(final),'frames':n,'fps':24,'resolution':[q['width'],q['height']],'source_audio_present':audio,'source_audio_packets_equal':exact,'full_decode_passed':True}
    write(a.out/'verification.json',report);stage.complete();print(json.dumps(report));return report

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    x=sub.add_parser('prepare');x.add_argument('source',type=Path);x.add_argument('--duration',type=float);x.add_argument('--width',type=int,default=G['width']);x.add_argument('--height',type=int,default=G['height']);x.add_argument('--sheet-fps',type=int,choices=[2,4,8,12],default=4);x.add_argument('--out',type=Path,required=True)
    x=sub.add_parser('finish');x.add_argument('--raw',type=Path,required=True);x.add_argument('--source',type=Path,required=True);x.add_argument('--duration',type=float,required=True);x.add_argument('--out',type=Path,required=True)
    a=p.parse_args();prepare(a) if a.mode=='prepare' else finish(a)
if __name__=='__main__':configure_stdio();main()
