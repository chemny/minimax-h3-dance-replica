#!/usr/bin/env python3
"""Validate a visually reviewed performance record and compile its timed prompt."""
import argparse,json,math
from pathlib import Path

def compile_record(d):
    duration=d['duration_seconds']
    if type(duration) not in (float,int) or not math.isfinite(duration) or duration<=0:
        raise ValueError('duration_seconds must be finite and positive')
    lines=[]
    for key in ('identity','scene_camera'):
        if not isinstance(d.get(key),str) or not d[key].strip():raise ValueError(key+' is required')
        lines.append(d[key].strip())
    if d.get('baseline_expression'):lines.append('基础表情：'+d['baseline_expression'].strip())
    last=0.0
    if not d.get('segments'):raise ValueError('segments required')
    for s in d['segments']:
        a,b=s['start'],s['end']
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)):
            raise ValueError('invalid time')
        if abs(a-last)>1e-4 or not a<b<=duration+1e-4:raise ValueError('segments must cover the clip continuously without overlap')
        if s.get('confidence')!='checked':raise ValueError('review uncertain segments before generating')
        visible=s.get('visibility')
        if visible not in {'visible','back','occluded','out_of_frame'}:raise ValueError('invalid visibility')
        if not s.get('body','').strip():raise ValueError('observed body action required')
        if visible!='visible' and (s.get('expression') or s.get('gaze')):
            raise ValueError('do not invent expression/gaze for an invisible face')
        parts=[s['body'].strip()]
        if s.get('expression'):parts.append('表情：'+s['expression'].strip())
        if s.get('gaze'):parts.append('视线：'+s['gaze'].strip())
        if visible!='visible':parts.append('面部不可见，保持真实朝向与遮挡，不强行露脸。')
        lines.append(f'{a:.2f}—{b:.2f}秒：'+ ' '.join(parts));last=b
    if abs(last-duration)>1e-4:raise ValueError('segments do not reach clip end')
    return '\n\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('record',type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    text=compile_record(json.loads(a.record.read_text(encoding='utf-8-sig')))
    if a.out.exists():raise SystemExit('Output exists; choose a new filename')
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(text,encoding='utf-8');print(a.out)
if __name__=='__main__':
    from runtime_checks import configure_stdio
    configure_stdio();main()
