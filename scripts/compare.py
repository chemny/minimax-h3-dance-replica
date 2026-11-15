#!/usr/bin/env python3
"""Compare existing videos on their original clocks; technical checks never imply visual acceptance."""
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess

from media import probe, video_info
from native_client import sha, write
from stages import Stage, input_file
from runtime_checks import require_client, configure_stdio


def ffmpeg(args):
    subprocess.run(['ffmpeg','-nostdin',*(['-n'] if '-y' not in args else []),'-v','error',*args],check=True)


def sample_times(duration, interval, performance=None):
    points={0.0,max(0.0,duration-.05)}
    for i in range(math.ceil(duration/interval)):points.add(i*interval)
    if performance:
        record=json.loads(Path(performance).read_text(encoding='utf-8'))
        for s in record.get('segments',[]):
            for key in ('start','end'):
                v=s.get(key)
                if type(v) in (int,float) and math.isfinite(v) and 0<=v<duration:
                    points.add(float(v))
                    points.add(max(0.0,float(v)-.1))
                    points.add(min(duration-.001,float(v)+.1))
    times=sorted(round(x,6) for x in points if 0<=x<duration)
    if len(times)>500:raise ValueError('Too many samples; increase --sample-interval or reduce event boundaries')
    return times


def verified_comparison(directory,source,result,performance=None):
    """Do not reuse stale visual acceptance for changed or missing media."""
    directory=Path(directory);record=directory/'verification.json';video=directory/'comparison.mp4'
    if not record.is_file() or not (directory/'acceptance.json').is_file():return False
    report=json.loads(record.read_text(encoding='utf-8-sig'))
    if report['source']['sha256']!=sha(source) or report['result']['sha256']!=sha(result):
        raise ValueError('Comparison inputs changed; use a new comparison output directory')
    if not video.is_file() or sha(video)!=report['comparison']['sha256']:
        raise ValueError('Completed comparison changed; preserve it and use a new local comparison directory')
    checkpoint=directory/'.stage.json'
    if checkpoint.is_file():
        stage=json.loads(checkpoint.read_text(encoding='utf-8-sig'))
        if stage['spec'].get('performance')!=(input_file(performance) if performance else None):
            raise ValueError('Comparison performance changed; use a new comparison output directory')
    return True


def compare(source,result,out,performance=None,cell_width=384,interval=1.0,source_duration=None):
    require_client()
    source=Path(source).resolve();result=Path(result).resolve();out=Path(out).resolve()
    if cell_width<64 or cell_width%2 or not math.isfinite(interval) or interval<=0:raise ValueError('Use even cell width >=64 and positive finite sample interval')
    ds,ss,sd=video_info(source);dr,rs,rd=video_info(result)
    if any(not math.isfinite(x) or x<=0 for x in (sd,rd)):raise ValueError('Video durations must be finite and positive')
    rate=Fraction(rs.get('avg_frame_rate') or rs['r_frame_rate'])
    if rate<=0 or rate>240:raise ValueError('Unsupported comparison frame rate')
    if source_duration is not None and (not math.isfinite(source_duration) or source_duration<=0 or source_duration>sd+.05):raise ValueError('Invalid authorized source duration')
    target_source=sd if source_duration is None else source_duration
    effective_source=min(sd,target_source)
    duration=max(target_source,rd);frames=math.ceil(duration*float(rate)-1e-5)
    duration=frames/float(rate);times=sample_times(duration,interval,performance)
    stage=Stage(out,{'operation':'compare','source':input_file(source),'result':input_file(result),
        'performance':input_file(performance) if performance else None,'cell_width':cell_width,'interval':interval,'source_duration':source_duration})
    with stage.locked():
        return _compare(source,result,out,ds,ss,sd,dr,rs,rd,rate,target_source,effective_source,duration,frames,times,cell_width,source_duration,stage)

def _compare(source,result,out,ds,ss,sd,dr,rs,rd,rate,target_source,effective_source,duration,frames,times,cell_width,source_duration,stage):
    width=cell_width;height=math.ceil(width*ss['height']/ss['width']/2)*2
    def panel(index):
        trim=f'trim=duration={target_source},' if index==0 and source_duration is not None else ''
        return f'[{index}:v]{trim}setpts=PTS-STARTPTS,fps={rate},scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,tpad=stop_mode=clone:stop_duration={duration}[p{index}]'
    picture=out/'picture.mp4';final=out/'comparison.mp4'
    stage.file('picture',picture,lambda p:ffmpeg(['-y','-i',str(source),'-i',str(result),'-filter_complex',panel(0)+';'+panel(1)+';[p0][p1]hstack=inputs=2[v]','-map','[v]','-frames:v',str(frames),'-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(p)]))
    audio=any(s['codec_type']=='audio' for s in dr['streams'])
    # Mux separately without a second -t/-frames cut, preserving source result audio packets.
    args=['-i',str(picture)]
    if audio:args+=['-i',str(result),'-map','0:v:0','-map','1:a:0']
    args+=['-c','copy','-movflags','+faststart']
    stage.file('mux',final,lambda p:ffmpeg(['-y',*args,str(p)]));ffmpeg(['-i',str(final),'-f','null','-'])
    counter=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=nb_read_frames,width,height,avg_frame_rate','-of','json',str(final)]))['streams'][0]
    if int(counter['nb_read_frames'])!=frames or Fraction(counter['avg_frame_rate'])!=rate:raise ValueError('Comparison frame count/rate mismatch')
    audio_equal=probe(result,True)['packets']==probe(final,True)['packets'] if audio else None
    if audio and not audio_equal:raise ValueError('Comparison audio packets changed')
    from PIL import Image,ImageDraw,ImageFont
    font=ImageFont.load_default();images=out/'frames';images.mkdir(exist_ok=True)
    thumbs=[]
    for i,t in enumerate(times):
        # Decode by frame index, avoiding keyframe-only seek and sub-frame ambiguity.
        index=min(frames-1,int(round(t*float(rate))))
        dest=images/f'{i:04}.jpg'
        stage.file(f'frame-{i}',dest,lambda p,index=index:ffmpeg(['-y','-i',str(final),'-vf',f'select=eq(n\\,{index}),scale={width}:{height//2}', '-frames:v','1',str(p)]))
        thumbs.append((t,index/float(rate),dest))
    for start in range(0,len(thumbs),8):
        group=thumbs[start:start+8];th=height//2
        sheet=Image.new('RGB',(width*2,(th+28)*math.ceil(len(group)/2)),'white');draw=ImageDraw.Draw(sheet)
        for i,(_,actual,path) in enumerate(group):
            x=i%2*width;y=i//2*(th+28)
            with Image.open(path) as im:sheet.paste(im,(x,y+28))
            draw.text((x+4,y+7),f'{actual:.3f}s  SOURCE | RESULT',font=font,fill='black')
        stage.file(f'contact-{start//8}',out/f'contact-{start//8+1:02}.jpg',lambda p:sheet.save(p,quality=95))
    metadata={'source':{'path':str(source),'sha256':sha(source),'duration':sd,'authorized_playback_seconds':target_source,'source_duration_limit':source_duration,'start_time':ss.get('start_time',ds.get('format',{}).get('start_time')),'resolution':[ss['width'],ss['height']]},'result':{'path':str(result),'sha256':sha(result),'duration':rd,'start_time':rs.get('start_time',dr.get('format',{}).get('start_time')),'resolution':[rs['width'],rs['height']]},'comparison':{'path':str(final),'sha256':sha(final),'fps':str(rate),'frames':frames,'duration':duration,'left':'source','right':'result','speed_change':False,'alignment_shift_seconds':0,'initial_timestamp_normalization':'each input starts at zero; no content alignment correction','source_tail_hold_seconds':max(0,duration-effective_source),'result_tail_hold_seconds':max(0,duration-rd),'aspect_ratio_preserved':True,'full_decode_passed':True,'audio_from_result_packets_equal':audio_equal},'samples':[{'requested_seconds':a,'actual_seconds':b,'image':str(p)} for a,b,p in thumbs]}
    write(out/'verification.json',metadata)
    categories=['identity','hairstyle_and_clothing','motion_timing_and_direction','pose_and_screen_position','expression_and_gaze','scene_and_camera','temporal_and_hand_artifacts']
    acceptance={'technical_checks':'pass','execution':'unreviewed','identity':'unreviewed','motion':'unreviewed','overall':'visual_review_required','checks':{k:{'status':'unreviewed','evidence':[]} for k in categories},'note':'Agent must inspect full videos and event transitions. No automated identity/motion score or pass is inferred.'}
    if not (out/'acceptance.json').exists():write(out/'acceptance.json',acceptance)
    stage.complete()
    return metadata


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--result',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--source-duration',type=float,help='Optional explicitly authorized source excerpt from its start; otherwise compare full source')
    p.add_argument('--performance',type=Path);p.add_argument('--cell-width',type=int,default=384);p.add_argument('--sample-interval',type=float,default=1.)
    a=p.parse_args();r=compare(a.source,a.result,a.out,a.performance,a.cell_width,a.sample_interval,a.source_duration)
    print(r['comparison']['path']);print('Technical checks passed; identity and motion still require visual review.')
if __name__=='__main__':configure_stdio();main()
