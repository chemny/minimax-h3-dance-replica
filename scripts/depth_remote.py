#!/usr/bin/env python3
"""Use existing DA3 + DA3Render by default; custom_cdf is an explicit experiment."""
import json,os,pathlib,shutil,subprocess,sys,time,urllib.request


def render_frames(depth, sky=None, renderer='da3render', render_seed=0):
    """Render the same raw DA3 predictions; preserve their order and geometry."""
    import numpy as np
    if renderer not in {'da3render','custom_cdf'}:
        raise ValueError('renderer must be da3render or custom_cdf')
    if depth.ndim!=3 or not np.isfinite(depth).all():
        raise ValueError('Expected finite raw DA3 depth with shape [frames,height,width]')
    if sky is not None and sky.shape!=depth.shape:raise ValueError('Sky/depth shapes differ')
    if renderer=='da3render':
        import torch
        from comfy_extras.nodes_depth_anything_3 import DA3Render
        torch.manual_seed(render_seed)
        normal=np.empty_like(depth,dtype=np.float32)
        for i in range(0,len(depth),4):
            geometry={'depth':torch.from_numpy(depth[i:i+4]),'mode':'mono'}
            if sky is not None:geometry['sky']=torch.from_numpy(sky[i:i+4])
            image=DA3Render.execute(geometry,{'output':'depth','normalization':'v2_style','apply_sky_clip':False})[0]
            normal[i:i+4]=image[...,0].cpu().numpy()
        params={'normalization':'v2_style','normalization_scope':'per-frame','per_frame_contrast':True,
                'output':'depth','apply_sky_clip':False,'sky_included':sky is not None,'render_seed':render_seed}
    else:
        # Optional historical contrast experiment; never applied after DA3Render.
        sample=depth[:,::8,::8].reshape(-1)
        lo,hi=np.quantile(sample,[.005,.995]);assert hi>lo+1e-6 and lo>0
        log_normal=np.clip((np.log(hi)-np.log(np.clip(depth,lo,hi)))/(np.log(hi)-np.log(lo)),0,1)
        sample_log=log_normal[:,::8,::8].ravel()
        h256=np.histogram(sample_log,bins=256,range=(0,1))[0].astype(np.float64)
        p256=h256/h256.sum();w=np.cumsum(p256);mu=np.cumsum(p256*np.arange(256))
        score=(mu[-1]*w-mu)**2/(w*(1-w)+1e-12)
        cut=float(np.clip(np.argmax(score[:-1])/255,.15,.75))
        hist,edges=np.histogram(sample_log[sample_log>=cut],bins=4096,range=(cut,1))
        hist=hist.astype(np.float64);limit=hist.mean()*5
        excess=np.maximum(hist-limit,0).sum();hist=np.minimum(hist,limit)+excess/len(hist)
        k=np.exp(-.5*(np.arange(-16,17)/5)**2);k/=k.sum();hist=np.convolve(hist,k,mode='same')
        cdf=np.cumsum(hist);cdf/=cdf[-1];centers=(edges[:-1]+edges[1:])/2
        normal=np.empty_like(log_normal)
        for i,z in enumerate(log_normal):
            eq=np.interp(z,centers,cdf,left=0,right=1)
            t=np.clip((z-cut)/(1-cut),0,1);near=.25*t+.75*eq
            normal[i]=np.where(z<cut,(12+36*z/cut)/255,(48+200*near)/255)
        params={'normalization':'clip-wide foreground weighted CDF on log depth','normalization_scope':'clip-wide',
                'per_frame_contrast':False,'bounds':[float(lo),float(hi)],'foreground_threshold':cut,
                'histogram_limit':5,'foreground_linear_blend':.25}
    if not np.isfinite(normal).all():raise ValueError('Non-finite rendered output')
    return normal,params


def main(cfg):
    renderer=cfg.get('renderer','da3render')
    if renderer not in {'da3render','custom_cdf'}:raise ValueError('Unknown renderer')
    core=pathlib.Path(cfg['comfy_root'])
    shared=pathlib.Path(cfg['shared_root'])
    dest=pathlib.Path(cfg['dest']);report_path=dest.with_suffix('.json')
    assert 2<=cfg['frames']/24<=15 and cfg['padded']>=cfg['frames']
    assert cfg['width']%32==0 and cfg['height']%32==0
    assert not dest.exists(),'Choose a new destination instead of overwriting an existing control'
    dest.parent.mkdir(parents=True,exist_ok=True)
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base=cfg.get('comfy_url','http://127.0.0.1:8188').rstrip('/')
    q=json.load(op.open(base+'/queue'))
    assert not q['queue_running'] and not q['queue_pending'],'GPU queue is busy'
    op.open(urllib.request.Request(base+'/free',data=json.dumps({'unload_models':True,'free_memory':True}).encode(),headers={'Content-Type':'application/json'})).read()
    os.chdir(core);sys.path.insert(0,str(core));sys.argv=[str(core/'main.py')]
    import comfy.options
    comfy.options.enable_args_parsing()
    import folder_paths
    from utils.extra_config import load_extra_path_config
    if (core/'extra_model_paths.yaml').exists():load_extra_path_config(str(core/'extra_model_paths.yaml'))
    for config in cfg.get('extra_model_configs',[]):
        if pathlib.Path(config).exists():load_extra_path_config(config)
    import torch,numpy as np,cv2
    from comfy_extras.nodes_depth_anything_3 import LoadDA3Model,_run_da3
    import comfy.model_management as mm
    existing=[]
    for category in folder_paths.folder_names_and_paths:
        try:existing.extend(folder_paths.get_filename_list(category))
        except (OSError,ValueError):pass
    model_name='depth_anything_3_mono_large.safetensors'
    assert any(pathlib.Path(x).name==model_name for x in existing),'Existing DA3 weight missing; do not auto-download'
    source=shared/'input'/cfg['source'];cap=cv2.VideoCapture(str(source));images=[]
    for i in range(cfg['frames']):
        ok,bgr=cap.read();assert ok,i;images.append(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB))
    cap.release();assert images[0].shape[:2]==(cfg['height'],cfg['width'])
    started=time.monotonic();model=LoadDA3Model.execute(model_name,'fp16')[0];depths=[];skies=[]
    for i in range(0,len(images),4):
        batch=torch.from_numpy(np.stack(images[i:i+4])).to(torch.float32)/255
        depth,_,sky=_run_da3(model,batch,1344,method='upper_bound_resize')
        depths.append(depth.cpu().numpy().astype(np.float32))
        if sky is not None:skies.append(sky.cpu().numpy().astype(np.float32))
        print('DEPTH_PROGRESS',min(i+4,len(images)),len(images),'seconds',round(time.monotonic()-started,1),flush=True)
    depth=np.concatenate(depths,axis=0);sky=np.concatenate(skies,axis=0) if skies else None
    mm.unload_all_models();mm.soft_empty_cache()
    normal,params=render_frames(depth,sky,renderer,int(cfg.get('render_seed',0)))
    ffmpeg=cfg.get('ffmpeg') or shutil.which('ffmpeg')
    if not ffmpeg:raise ValueError('Set ffmpeg in config or add it to PATH')
    partial=dest.with_name(dest.stem+'.partial.mp4')
    cmd=[ffmpeg,'-v','error','-f','rawvideo','-pix_fmt','gray','-s',str(cfg['width'])+'x'+str(cfg['height']),'-r','24','-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','12','-pix_fmt','yuv420p','-y',str(partial)]
    p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for i in range(cfg['padded']):
            frame=np.rint(np.clip(normal[min(i,cfg['frames']-1)],0,1)*255).astype(np.uint8)
            p.stdin.write(frame.tobytes())
        p.stdin.close();err=p.stderr.read().decode('utf-8',errors='replace');assert p.wait()==0,err
    except:
        p.kill();raise
    os.replace(partial,dest)
    report={'path':str(dest),'model':model_name,'renderer':renderer,'renderer_parameters':params,
            'inference_resolution':1344,'input_frames_inferred':cfg['frames'],'padded_frames':cfg['padded'],
            'fps':24,'resolution':[cfg['width'],cfg['height']],'pixel_temporal_average':False,
            'sampled_gray_levels':int(len(np.unique((normal[:,::16,::16]*255).round().astype(np.uint8)))),
            'finite_fraction':float(np.isfinite(depth).mean()),'elapsed_seconds':time.monotonic()-started,
            'new_downloads':False,'rgb_texture_fusion':False}
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DEPTH_READY',json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] in ('-h','--help'):
        print('Usage: depth_remote.py JSON_CONFIG; default renderer=da3render (depth/v2_style/sky_clip=false); optional renderer=custom_cdf. Run in existing ComfyUI Python.');sys.exit(0)
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8')
    main(json.loads(sys.argv[1]))
