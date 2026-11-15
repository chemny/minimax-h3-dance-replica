#!/usr/bin/env python3
"""Build H3 FL2VA first/last/depth graphs with a standalone native API client."""
import argparse,json,math,os,secrets
from pathlib import Path

from profiles import GENERATION as G, MODELS
from runtime_checks import configure_stdio
MODEL=MODELS['base']
PATCH=MODELS['controlnet']

def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
def build(first,last,depth,prompt,seconds,width,height,steps,seed,strength=G['depth_strength'],end=G['control_end'],prefix='DanceReplica/video',recipe=None):
    if not 2<=seconds<=15 or not math.isfinite(seconds):raise ValueError('Single clip duration must be 2–15s')
    if steps not in (4,8):raise ValueError('Use matching 4 or 8 step LoRA')
    if any(x<256 or x%32 for x in (width,height)) or width*height>768*1344:raise ValueError('Invalid dimensions')
    if not 0<strength<=1 or not 0<=end<=1:raise ValueError('Invalid control strength/window')
    if type(seed) is not int or not 0<=seed<2**64:raise ValueError('Invalid seed')
    if not prompt.strip():raise ValueError('Prompt required')
    n=5+17*math.ceil((seconds*24-5)/17)
    g={
      '1':node('UNETLoader',unet_name=MODEL,weight_dtype='default'),
      '23':node('LoraLoaderModelOnly',model=['1',0],lora_name=MODELS['lora_'+str(steps)],strength_model=G['lora_strength']),
      '2':node('MiniMaxH3SigmaShift',model=['23',0],shift_video=G['shift_video'],shift_audio=G['shift_audio']),
      '3':node('PathchSageAttentionKJ',model=['63',0],sage_attention='sageattn_qk_int8_pv_fp16_cuda',allow_compile=False),
      '4':node('CLIPLoader',clip_name=MODELS['encoder'],type='minimax',device='default'),
      '5':node('VAELoader',vae_name=MODELS['vae']),
      '21':node('LoadImage',image=first),
      '9':node('MiniMaxH3ImageToVideo',clip=['4',0],vae=['5',0],prompt=prompt,width=width,height=height,length=n,first_frame=['21',0]),
      '40':node('LoadImage',image=last),
      '41':node('MiniMaxH3AddGuide',positive=['9',0],latent=['9',1],frame_idx=math.ceil(seconds*24)-1,vae=['5',0],image=['40',0]),
      '60':node('LoadVideo',file=depth),'61':node('GetVideoComponents',video=['60',0]),
      '62':node('ModelPatchLoader',name=PATCH),
      '63':node('MiniMaxH3FunControlNetApply',model=['2',0],model_patch=['62',0],vae=['5',0],strength=strength,start_percent=0.,end_percent=end,control_video=['61',0]),
      '10':node('RandomNoise',noise_seed=seed),'11':node('BasicGuider',model=['3',0],conditioning=['41',0]),
      '12':node('KSamplerSelect',sampler_name=G['sampler']),'13':node('BasicScheduler',model=['3',0],scheduler=G['scheduler'],steps=steps,denoise=1.),
      '14':node('SamplerCustomAdvanced',noise=['10',0],guider=['11',0],sampler=['12',0],sigmas=['13',0],latent_image=['9',1]),
      '15':node('VAEDecode',samples=['14',0],vae=['5',0]),
      '16':node('CreateVideo',images=['15',0],fps=24.,bit_depth=8,color_space='sRGB',codec='none'),
      '17':node('SaveVideo',video=['16',0],filename_prefix=prefix,format='mp4',**{'format.codec':'h264'})}
    if recipe:
        g['1']['inputs']['unet_name']=recipe['base'];g['4']['inputs']['clip_name']=recipe['encoder'];g['5']['inputs']['vae_name']=recipe['vae']
        g['23']['inputs'].update(lora_name=recipe['turbo_lora'],strength_model=recipe['lora_strength'])
        g['62']['inputs']['name']=recipe['controlnet']
        g['2']['inputs'].update(shift_video=recipe['shift_video'],shift_audio=recipe['shift_audio'])
        g['12']['inputs']['sampler_name']=recipe['sampler'];g['13']['inputs']['scheduler']=recipe['scheduler']
        if not recipe['sage']:
            del g['3'];g['11']['inputs']['model']=['63',0];g['13']['inputs']['model']=['63',0]
    return g


def main():
    from native_client import Client,write,sha,validate_graph
    p=argparse.ArgumentParser(description='Standalone native ComfyUI first/last/depth client')
    p.add_argument('--base',default=os.getenv('COMFY_URL','http://127.0.0.1:8188'))
    p.add_argument('--key-file',type=Path)
    sub=p.add_subparsers(dest='mode',required=True);sub.add_parser('inventory')
    for mode in ['build','run']:
        x=sub.add_parser(mode)
        for field in ['first','last','depth']:x.add_argument('--'+field,required=True)
        x.add_argument('--prompt',type=Path,required=True);x.add_argument('--duration',type=float,required=True)
        x.add_argument('--width',type=int,default=G['width']);x.add_argument('--height',type=int,default=G['height'])
        x.add_argument('--steps',type=int,choices=[4,8],default=G['steps']);x.add_argument('--seed',type=int)
        x.add_argument('--depth-strength',type=float,default=G['depth_strength']);x.add_argument('--control-end',type=float,default=G['control_end'])
        x.add_argument('--out',type=Path,required=True)
    x=sub.add_parser('resume');x.add_argument('--out',type=Path,required=True)
    a=p.parse_args();u=Client(a.base,a.key_file)
    if a.mode=='inventory':
        o=u.request('/object_info');print(json.dumps({'queue':u.request('/queue'),'nodes':sorted(o)},ensure_ascii=False));return
    if a.mode=='resume':
        graph=json.loads((a.out/'graph.json').read_text(encoding='utf-8'))
    else:
        if a.out.exists():raise ValueError('Use a new directory, or resume the existing task')
        prompt=a.prompt.read_text(encoding='utf-8');seed=a.seed if a.seed is not None else secrets.randbits(63)
        graph=build(a.first,a.last,a.depth,prompt,a.duration,a.width,a.height,a.steps,seed,a.depth_strength,a.control_end)
        if a.mode=='build':write(a.out/'graph.json',graph);return
        inputs={k:Path(getattr(a,k)).resolve() for k in ('first','last','depth')}
        for f in inputs.values():
            if not f.is_file():raise ValueError('Missing input: '+str(f))
        from media import video_info
        _,dv,dd=video_info(inputs['depth'])
        if (dv['width'],dv['height'])!=(a.width,a.height) or dd+.01<(5+17*math.ceil((a.duration*24-5)/17))/24:raise ValueError('Depth size/duration mismatch')
        validate_graph(u,graph);u.idle()
        remote={k:u.upload(v) for k,v in inputs.items()}
        graph=build(remote['first'],remote['last'],remote['depth'],prompt,a.duration,a.width,a.height,a.steps,seed,a.depth_strength,a.control_end,prefix='DanceReplica/'+secrets.token_hex(8)+'/video')
        validate_graph(u,graph);write(a.out/'graph.json',graph)
        write(a.out/'run.json',{'seed':seed,'inputs':{k:str(v) for k,v in inputs.items()},'sha256':{k:sha(v) for k,v in inputs.items()}})
    paths=u.stage(a.out/'native-job',graph,['17'])
    write(a.out/'raw-result.json',{'raw_video':str(paths[0].resolve()),'note':'Run media.py finish to restore music'})
    print(paths[0])
if __name__=='__main__':configure_stdio();main()
