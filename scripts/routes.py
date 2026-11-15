"""Explicit H3 recipes and native graphs. No network, uploads or model execution."""
import copy
import json
import math
from pathlib import Path

from profiles import PRESET

ROOT = Path(__file__).resolve().parent.parent
DIRECT = json.loads((ROOT / 'presets/routes.json').read_text(encoding='utf-8'))
NAMES = ('depth', 'ref2v', 'character_swap')


def name(config):
    route = config.get('route', 'depth')
    if route not in NAMES:
        raise ValueError('route must be depth, ref2v or character_swap')
    return route


def recipe(config):
    route = name(config)
    pinned={'sampler','scheduler','shift_video','shift_audio','cfg','adapter_strength','attention_backend','activation_memory_gib','window_frames','history_frames'}
    if set(config)&pinned:raise ValueError('Sampling recipe fields are pinned in route presets, not silently overridden: '+', '.join(sorted(set(config)&pinned)))
    if route == 'depth':
        r = {**copy.deepcopy(PRESET['models']), **copy.deepcopy(PRESET['generation']),
             'depth': copy.deepcopy(PRESET['depth']), 'cfg': 1.0, 'sage': True}
    else:
        r = copy.deepcopy(DIRECT[route])
    model_keys = {'base', 'encoder', 'vae', 'lora_4', 'lora_8', 'adapter', 'audio_vae', 'sam', 'controlnet'}
    overrides = config.get('model_overrides', {})
    if not isinstance(overrides, dict) or set(overrides) - (model_keys & r.keys()):
        raise ValueError('Unsupported model_overrides for selected route')
    if any(not isinstance(v, str) or not v.strip() for v in overrides.values()):
        raise ValueError('Model overrides must be explicit nonempty filenames')
    r.update(overrides)
    fields = {'steps', 'width', 'height', 'lora_strength', 'sage', 'depth_strength', 'control_end'} if route == 'depth' else (
        {'steps', 'width', 'height', 'lora_strength', 'sage', 'motion_reference_long_edge'} if route == 'ref2v' else
        {'steps', 'resolution_preset', 'mask_expansion'})
    route_fields = {'steps', 'width', 'height', 'lora_strength', 'sage', 'depth_strength', 'control_end',
                    'motion_reference_long_edge', 'resolution_preset', 'mask_expansion'}
    if (set(config) & route_fields) - fields:
        raise ValueError('Parameters do not apply to '+route+': '+', '.join(sorted((set(config) & route_fields)-fields)))
    for key in fields & config.keys():
        r[key] = config[key]
    if type(r['steps']) is not int or r['steps'] not in ((8,) if route == 'character_swap' else (4, 8)):
        raise ValueError('Character Swap requires its 8-step recipe; other routes support matching 4/8-step LoRAs')
    if route != 'character_swap':
        if any(type(r[k]) is not int or r[k] < 256 or r[k] % 32 for k in ('width', 'height')) or r['width']*r['height'] > 768*1344:
            raise ValueError('Use 32-aligned dimensions, at most 768x1344 pixels')
        if type(r['sage']) is not bool:
            raise ValueError('sage must be boolean')
        if type(r['lora_strength']) not in (int, float) or not math.isfinite(r['lora_strength']) or not 0 < r['lora_strength'] <= 1:
            raise ValueError('lora_strength must be >0 and <=1')
    if route == 'ref2v':
        edge = r['motion_reference_long_edge']
        if type(edge) is not int or edge % 32 or not 256 <= edge <= 1536:
            raise ValueError('motion_reference_long_edge must be 256..1536 and 32-aligned')
    if route == 'character_swap':
        if r['resolution_preset'] not in ('480', '768'):
            raise ValueError('resolution_preset must be "480" or "768"')
        if type(r['mask_expansion']) is not int or not 0 <= r['mask_expansion'] <= 256:
            raise ValueError('mask_expansion must be an integer from 0 to 256')
    r.update(route=route, turbo_lora=r['lora_'+str(r['steps'])], fps=24)
    wrong='_fl2v_' if route=='ref2v' else '_ref2v_'
    if wrong in r['turbo_lora'].lower():raise ValueError('Turbo LoRA family does not match selected route')
    return r


def node(kind, **inputs):
    return {'class_type': kind, 'inputs': inputs}


def reference_graph(r, identity, motion, prompt, seconds, seed, prefix):
    if not prompt.strip() or not 2 <= seconds <= 15:
        raise ValueError('Nonempty prompt and duration 2..15s required')
    n = 5+17*math.ceil((seconds*24-5)/17)
    g = {
        '1': node('UNETLoader', unet_name=r['base'], weight_dtype='default'),
        '23': node('LoraLoaderModelOnly', model=['1', 0], lora_name=r['turbo_lora'], strength_model=r['lora_strength']),
        '2': node('MiniMaxH3SigmaShift', model=['23', 0], shift_video=r['shift_video'], shift_audio=r['shift_audio']),
        '4': node('CLIPLoader', clip_name=r['encoder'], type='minimax', device='default'),
        '5': node('VAELoader', vae_name=r['vae']),
        '100': node('LoadImage', image=identity), '7': node('LoadVideo', file=motion),
        '8': node('GetVideoComponents', video=['7', 0]),
        '9': node('MiniMaxH3ReferenceToVideo', clip=['4', 0], vae=['5', 0], prompt=prompt,
                  width=r['width'], height=r['height'], length=n, ref_image_size='match',
                  **{'ref_images.ref_image_0': ['100', 0], 'ref_videos.ref_video_0': ['8', 0]}),
        '10': node('RandomNoise', noise_seed=seed), '12': node('KSamplerSelect', sampler_name=r['sampler']),
        '15': node('VAEDecode', samples=['14', 0], vae=['5', 0]),
        '16': node('CreateVideo', images=['15', 0], fps=24., bit_depth=8, color_space='sRGB', codec='none'),
        '17': node('SaveVideo', video=['16', 0], filename_prefix=prefix, format='mp4', **{'format.codec': 'h264'})}
    model = ['2', 0]
    if r['sage']:
        g['3'] = node('PathchSageAttentionKJ', model=model, sage_attention='sageattn_qk_int8_pv_fp16_cuda', allow_compile=False)
        model = ['3', 0]
    g['11'] = node('BasicGuider', model=model, conditioning=['9', 0])
    g['13'] = node('BasicScheduler', model=model, scheduler=r['scheduler'], steps=r['steps'], denoise=1.)
    g['14'] = node('SamplerCustomAdvanced', noise=['10', 0], guider=['11', 0], sampler=['12', 0], sigmas=['13', 0], latent_image=['9', 1])
    return g, ['17']


def character_graph(r, identity, motion, prompt, seconds, seed, prefix):
    if not prompt.strip() or not 2 <= seconds <= 15:
        raise ValueError('Nonempty prompt and duration 2..15s required')
    g = json.loads((ROOT / 'assets/character-swap-api.json').read_text(encoding='utf-8'))
    for key, field, value in [('model', 'unet_name', r['base']), ('text', 'clip_name', r['encoder']),
                              ('video_vae', 'vae_name', r['vae']), ('audio_vae', 'vae_name', r['audio_vae']),
                              ('sam', 'ckpt_name', r['sam']), ('source', 'file', motion), ('reference', 'image', identity)]:
        g[key]['inputs'][field] = value
    g['shift']['inputs'].update(shift_video=r['shift_video'], shift_audio=r['shift_audio'])
    g['turbo']['inputs'].update(lora_name=r['turbo_lora'], strength_model=r['lora_strength'])
    g['adapter']['inputs'].update(lora_name=r['adapter'], strength_model=r['adapter_strength'])
    g['attention']['inputs']['attention'] = r['attention_backend']
    g['prepare']['inputs'].update(resolution=r['resolution_preset'], duration_seconds=seconds)
    g['plan']['inputs'].update(seed=seed, window_frames=str(r['window_frames']), history_frames=str(r['history_frames']))
    g['expand_mask']['inputs']['expansion_px'] = r['mask_expansion']
    g['reference_conditioning']['inputs']['prompt'] = prompt
    g['sampling_memory']['inputs']['activation_memory_gib'] = r['activation_memory_gib']
    g['guider']['inputs']['cfg'] = r['cfg']
    g['sampler']['inputs']['sampler_name'] = r['sampler']
    g['schedule']['inputs'].update(scheduler=r['scheduler'], steps=r['steps'])
    g['video']['inputs'].pop('audio', None)
    g['save']['inputs']['filename_prefix'] = prefix
    return g, ['save']


def direct_graph(r, identity, motion, prompt, seconds, seed, prefix='DanceReplica/video'):
    if r['route'] == 'ref2v':
        return reference_graph(r, identity, motion, prompt, seconds, seed, prefix)
    if r['route'] == 'character_swap':
        return character_graph(r, identity, motion, prompt, seconds, seed, prefix)
    raise ValueError('Use h3_run.build for the depth route')


def assert_gateway_recipe(plan, r):
    """Reject a gateway which silently chose another base, LoRA or sampler."""
    if plan.get('submitted') is not False or not isinstance(plan.get('graph'), dict):
        raise ValueError('Gateway must expose a non-submitting /v1/prepare graph')
    graph = plan['graph']
    values = list(graph.values())
    def find(kind):
        return [n['inputs'] for n in values if n['class_type'] == kind]
    def base(value):
        return value.replace('\\', '/').rsplit('/', 1)[-1]
    if [base(n['unet_name']) for n in find('UNETLoader')] != [base(r['base'])]:
        raise ValueError('Gateway base model does not match selected recipe')
    actual = {(base(n['lora_name']), n['strength_model']) for n in find('LoraLoaderModelOnly')}
    expected = {(base(r['turbo_lora']), r['lora_strength'])}
    if r['route'] == 'character_swap':
        expected.add((base(r['adapter']), r['adapter_strength']))
    if actual != expected:
        raise ValueError('Gateway LoRAs differ from selected recipe; use an explicit model_overrides mapping or matching native backend')
    if not any(n.get('steps') == r['steps'] and n.get('scheduler') == r['scheduler'] for n in find('BasicScheduler')):
        raise ValueError('Gateway sampling steps/scheduler differ from selected recipe')
    if not any(n.get('sampler_name') == r['sampler'] for n in find('KSamplerSelect')):
        raise ValueError('Gateway sampler differs from selected recipe')
    if not any(n.get('shift_video') == r['shift_video'] and n.get('shift_audio') == r['shift_audio'] for n in find('MiniMaxH3SigmaShift')):
        raise ValueError('Gateway shifts differ from selected recipe')
    if {base(n['clip_name']) for n in find('CLIPLoader')} != {base(r['encoder'])}:
        raise ValueError('Gateway text encoder differs from selected recipe')
    vaes={base(n['vae_name']) for n in find('VAELoader')}
    expected_vaes={base(r['vae'])} | ({base(r['audio_vae'])} if r['route']=='character_swap' else set())
    if vaes!=expected_vaes:raise ValueError('Gateway VAEs differ from selected recipe')
    if r['route']=='character_swap':
        if not find('CFGGuider') or any(n.get('cfg')!=1.0 for n in find('CFGGuider')):raise ValueError('Character Swap requires CFG1')
        if {base(n['ckpt_name']) for n in find('CheckpointLoaderSimple')}!={base(r['sam'])}:raise ValueError('Gateway SAM model differs')
        if not any(n.get('attention')==r['attention_backend'] for n in find('ModelAttentionBackend')):raise ValueError('Gateway attention backend differs')
        if not any(str(n.get('window_frames'))==str(r['window_frames']) and str(n.get('history_frames'))==str(r['history_frames']) for n in find('H3RelayWindowPlan')):raise ValueError('Gateway continuation window recipe differs')
        expansions=[n['inputs'].get('expansion_px') for n in values if n['class_type'] in ('H3RelayExpandMask','H3SwapExpandedMaskCache')]
        if expansions!=[r['mask_expansion']]:raise ValueError('Gateway mask expansion differs')
    else:
        refs=find('MiniMaxH3ReferenceToVideo')
        if len(refs)!=1 or any(refs[0].get(k)!=r[k] for k in ('width','height')):raise ValueError('Gateway output canvas differs')
        if not find('BasicGuider') or find('CFGGuider') or find('ModelPatchLoader'):raise ValueError('Gateway reference route has unexpected guidance/control')
        if bool(find('PathchSageAttentionKJ'))!=r['sage']:raise ValueError('Gateway Sage setting differs')
    return graph
