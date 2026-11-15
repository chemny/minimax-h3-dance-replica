"""Non-generating regression checks: recipes, routing and HTTP submission guards."""
from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from routes import recipe, direct_graph, assert_gateway_recipe
from h3_run import build
from dance import Pipeline
from direct_routes import DirectPipeline
from workflow_client import WorkflowClient
from segments import plan


@contextmanager
def gateway(graph, lose_first=False):
    state={'post':[],'unique':{},'lost':False,'graph':graph}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send_json(self,value):
            data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['post'].append(self.path)
            if self.path=='/v1/prepare':return self.send_json({'submitted':False,'graph':state['graph']})
            if self.path!='/v1/jobs':return self.send_error(404)
            key=self.headers.get('Idempotency-Key');assert key
            state['unique'].setdefault(key,'stable-job')
            if lose_first and not state['lost']:
                state['lost']=True;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            return self.send_json({'id':'stable-job'})
        def do_GET(self):
            if self.path=='/v1/jobs/stable-job':
                return self.send_json({'id':'stable-job','status':'succeeded','outputs':[{'filename':'result.mp4','url':'/v1/jobs/stable-job/files/0'}]})
            if self.path=='/v1/jobs/stable-job/files/0':
                self.send_response(200);self.end_headers();self.wfile.write(b'non-video-test-payload');return
            return self.send_error(404)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield WorkflowClient('http://127.0.0.1:'+str(server.server_port),key_env='H3_TEST_UNUSED_KEY'),state
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


class RouteTests(unittest.TestCase):
    def test_depth_legacy_recipe_and_matching_steps(self):
        self.assertEqual(recipe({})['route'],'depth')
        for steps in (4,8):
            r=recipe({'route':'depth','steps':steps})
            g=build('first.png','last.png','depth.mp4','pose',3,768,1344,steps,5,recipe=r)
            self.assertIn('fl2va',g['1']['inputs']['unet_name'])
            self.assertIn(f'fl2v_turbo_{steps}step',g['23']['inputs']['lora_name'])
            self.assertEqual(g['13']['inputs']['steps'],steps)
            self.assertEqual(g['2']['inputs']['shift_video'],6)
            self.assertEqual(g['63']['class_type'],'MiniMaxH3FunControlNetApply')

    def test_reference_uses_ref2v_not_fl2v_no_extra_controls(self):
        for steps in (4,8):
            r=recipe({'route':'ref2v','steps':steps});g,ids=direct_graph(r,'identity.png','motion.mp4','roles',3,8)
            self.assertIn('ref2va',g['1']['inputs']['unet_name'])
            self.assertIn(f'ref2v_turbo_{steps}step',g['23']['inputs']['lora_name'])
            self.assertEqual(g['13']['inputs']['steps'],steps)
            self.assertEqual(g['9']['inputs']['ref_images.ref_image_0'],['100',0])
            kinds={v['class_type'] for v in g.values()}
            self.assertNotIn('MiniMaxH3AddGuide',kinds);self.assertNotIn('ModelPatchLoader',kinds)
            self.assertEqual(r['motion_reference_long_edge'],512);self.assertEqual(ids,['17'])

    def test_character_swap_two_adapters_author_sampling(self):
        r=recipe({'route':'character_swap'});g,ids=direct_graph(r,'identity.png','source.mp4','roles',7,8)
        self.assertEqual(g['adapter']['inputs']['lora_name'],'h3_character_swap_v1.5.safetensors')
        self.assertEqual(g['adapter']['inputs']['model'],['turbo',0])
        self.assertIn('fl2v_turbo_8step',g['turbo']['inputs']['lora_name'])
        self.assertEqual(g['guider']['inputs']['cfg'],1)
        self.assertEqual(g['sampler']['inputs']['sampler_name'],'er_sde')
        self.assertEqual(g['schedule']['inputs']['scheduler'],'normal')
        self.assertEqual(g['schedule']['inputs']['steps'],8)
        self.assertEqual(g['plan']['inputs']['window_frames'],'175')
        self.assertEqual(g['plan']['inputs']['history_frames'],'18')
        self.assertNotIn('audio',g['video']['inputs']);self.assertEqual(ids,['save'])

    def test_reject_wrong_route_parameters_and_lora_family(self):
        for c in [{'route':'unknown'},{'route':'character_swap','steps':4},
                  {'route':'character_swap','width':768},{'route':'ref2v','depth_strength':.5},
                  {'route':'ref2v','sampler':'er_sde'},{'route':'ref2v','steps':True},
                  {'route':'ref2v','model_overrides':{'lora_8':'minimax_h3_fl2v_turbo_8step_bad.safetensors'}}]:
            with self.subTest(c=c),self.assertRaises(ValueError):recipe(c)

    def test_unknown_profile_does_not_silently_map(self):
        with self.assertRaises(ValueError):recipe({'route':'ref2v','model_overrides':{'controlnet':'x'}})

    def test_gateway_rejects_wrong_weights_before_submission(self):
        r=recipe({'route':'ref2v'});wrong=recipe({'route':'ref2v','steps':4})
        graph=direct_graph(wrong,'photo','motion','roles',3,1)[0]
        with tempfile.TemporaryDirectory() as temp,gateway(graph) as (client,state):
            with self.assertRaises(ValueError):client.stage(Path(temp),{'workflow':'video.reference'},r)
            self.assertNotIn('/v1/jobs',state['post'])

    def test_gateway_rejects_prompt_rewriting_before_submission(self):
        r=recipe({'route':'ref2v'});graph=direct_graph(r,'photo','motion','Unexpected prefix',3,1)[0]
        with tempfile.TemporaryDirectory() as temp,gateway(graph) as (client,state):
            with self.assertRaisesRegex(ValueError,'changed the supplied prompt'):
                client.stage(Path(temp),{'workflow':'video.reference','prompt':'Keep exactly this text.'},r)
            self.assertNotIn('/v1/jobs',state['post'])

    def test_lost_submit_response_reuses_idempotency_key(self):
        r=recipe({'route':'ref2v'});graph=direct_graph(r,'photo','motion','roles',3,1)[0]
        with tempfile.TemporaryDirectory() as temp,gateway(graph,lose_first=True) as (client,state):
            body={'workflow':'video.reference'}
            with self.assertRaises(Exception):client.stage(Path(temp),body,r)
            result=client.stage(Path(temp),body,r)
            self.assertEqual(len(state['unique']),1)
            self.assertEqual(result[0].read_bytes(),b'non-video-test-payload')
            count=len(state['post']);client.stage(Path(temp),body,r)
            self.assertEqual(len(state['post']),count)

    def test_gateway_exact_recipe_and_shift_attention_checks(self):
        for route in ('ref2v','character_swap'):
            r=recipe({'route':route});g=direct_graph(r,'photo','motion','roles',3,1)[0]
            assert_gateway_recipe({'submitted':False,'graph':g},r)
            bad=copy.deepcopy(g);key='shift' if route=='character_swap' else '2';bad[key]['inputs']['shift_video']=6
            with self.assertRaises(ValueError):assert_gateway_recipe({'submitted':False,'graph':bad},r)

    def test_render_and_run_require_execute_before_touching_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'config.json';p.write_text(json.dumps({'route':'ref2v','out':'out','source':'missing.mp4'}))
            for action in ('render','run','depth'):
                result=subprocess.run([sys.executable,str(ROOT/'scripts/dance.py'),action,'--config',str(p)],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0);self.assertIn('--execute',result.stderr)
                self.assertFalse((Path(temp)/'out').exists())

    def test_cli_init_all_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            for route in ('depth','ref2v','character_swap'):
                p=Path(temp)/(route+'.json')
                subprocess.run([sys.executable,str(ROOT/'scripts/dance.py'),'init','--route',route,'--config',str(p)],check=True,capture_output=True)
                c=json.loads(p.read_text());self.assertEqual(c['route'],route);self.assertEqual(c['steps'],8)
                self.assertEqual('performance' in c,route=='depth')

    def test_task_route_and_recipe_cannot_change_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'source.mp4').write_bytes(b'source');(root/'photo.png').write_bytes(b'photo')
            p=root/'config.json';c={'route':'ref2v','source':'source.mp4','identity':'photo.png','out':'out','seed':1}
            p.write_text(json.dumps(c));f=DirectPipeline(p);self.assertEqual(f.recipe['route'],'ref2v')
            c['route']='character_swap';p.write_text(json.dumps(c))
            with self.assertRaises(ValueError):DirectPipeline(p)

    def test_shared_depth_segment_plan_preserves_complete_duration(self):
        p=plan(30)
        self.assertGreaterEqual(p['segment_count'],3)
        self.assertEqual(sum(s['delivery_frames'] for s in p['segments'])-p['segment_count']+1,p['delivery_frames'])
        for left,right in zip(p['segments'],p['segments'][1:]):self.assertEqual(left['last_anchor'],right['first_anchor'])

    def test_direct_preview_uses_correct_lifecycle_without_image_or_depth_stage(self):
        for route in ('ref2v','character_swap'):
            with self.subTest(route=route),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source.mp4';source.write_bytes(b'source');(root/'photo.png').write_bytes(b'photo')
                c={'route':route,'source':'source.mp4','identity':'photo.png','out':'out','seed':2,'prompt':'Use the provided roles.'}
                p=root/'config.json';p.write_text(json.dumps(c));f=DirectPipeline(p)
                commands=[]
                def prepare(args):
                    args.out.mkdir();(args.out/'metadata.json').write_text(json.dumps({'duration_seconds':3.,'delivery_frames':72,'internal_frames':73,'resolution':[768,1344]}))
                def command(args):
                    commands.append(args);Path(args[-1]).write_bytes(b'normalized-reference')
                info=({}, {'duration_ts':72000,'time_base':'1/24000','width':288,'height':512},3.)
                with patch('media.video_info',return_value=info),patch('media.prepare',side_effect=prepare),patch('direct_routes.run',side_effect=command):
                    graph,_=f.preview();f.prepare()
                self.assertEqual((root/'out/prompt.txt').read_text(),c['prompt'])
                self.assertFalse((root/'out/anchors').exists());self.assertFalse((root/'out/depth').exists())
                self.assertEqual(len(commands),1 if route=='ref2v' else 0)
                if route=='ref2v':
                    self.assertIn('scale=512:512',commands[0][commands[0].index('-vf')+1])
                    self.assertEqual(graph['9']['inputs']['width'],768)
                else:self.assertEqual(f.meta()['motion_input'],str(source.resolve()))

    def test_full_source_duration_uses_fraction_and_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'source.mp4').write_bytes(b's');(root/'photo.png').write_bytes(b'p')
            p=root/'config.json';p.write_text(json.dumps({'route':'ref2v','source':'source.mp4','identity':'photo.png','out':'out'}))
            f=DirectPipeline(p)
            with patch('media.video_info',return_value=({}, {'duration_ts':125440,'time_base':'1/15360'},8.166667)):
                planned=f.plan()
            self.assertEqual(planned['delivery_frames'],196);self.assertEqual(planned['required_anchors'],0)
            self.assertEqual(planned['internal_frames'],209)
            with patch('media.video_info',return_value=({}, {'duration_ts':720000,'time_base':'1/24000'},30.)):
                with self.assertRaises(ValueError):f.plan()

    def test_registered_task_preserves_submitted_recipe_when_preset_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'source.mp4').write_bytes(b's');(root/'photo.png').write_bytes(b'p')
            p=root/'config.json';p.write_text(json.dumps({'route':'ref2v','source':'source.mp4','identity':'photo.png','out':'out'}))
            f=DirectPipeline(p);original=f.recipe.copy()
            job=root/'out/render/job/job.json';job.parent.mkdir(parents=True);job.write_text('{}')
            changed=copy.deepcopy(original);changed['lora_8']='different.safetensors'
            with patch('dance.route_recipe',return_value=changed):
                self.assertEqual(DirectPipeline(p).recipe,original)

    def test_direct_prompt_can_use_performance_without_default_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'source.mp4').write_bytes(b's');(root/'photo.png').write_bytes(b'p')
            data={'duration_seconds':3,'identity':'Reference identity.','scene_camera':'Source camera.',
                  'segments':[{'start':0,'end':3,'confidence':'checked','visibility':'visible','body':'Observed motion.'}]}
            (root/'performance.json').write_text(json.dumps(data))
            p=root/'config.json';p.write_text(json.dumps({'route':'ref2v','source':'source.mp4','identity':'photo.png','out':'out','prompt':'','performance':'performance.json'}))
            f=DirectPipeline(p);prepared=root/'out/prepared';prepared.mkdir();(prepared/'metadata.json').write_text(json.dumps({'duration_seconds':3}))
            f.compile();self.assertIn('Observed motion.',(root/'out/prompt.txt').read_text())
            self.assertNotIn('Replace the person',(root/'out/prompt.txt').read_text())

    def test_gateway_credentials_are_not_saved_in_stage_artifacts(self):
        r=recipe({'route':'ref2v'});g=direct_graph(r,'photo','motion','roles',3,1)[0]
        secret='TEST_SECRET_NOT_FOR_JOURNALS'
        with tempfile.TemporaryDirectory() as temp,patch.dict('os.environ',{'H3_TEST_UNUSED_KEY':secret}),gateway(g) as (client,state):
            client.stage(Path(temp),{'workflow':'video.reference'},r)
            text=''.join(p.read_text() for p in Path(temp).glob('*.json'))
            self.assertNotIn(secret,text)

    def test_changed_prepared_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'source.mp4').write_bytes(b's');(root/'photo.png').write_bytes(b'p')
            p=root/'config.json';p.write_text(json.dumps({'route':'ref2v','source':'source.mp4','identity':'photo.png','out':'out'}))
            f=DirectPipeline(p);prepared=root/'out/prepared';prepared.mkdir();motion=prepared/'motion.mp4';motion.write_bytes(b'changed')
            (prepared/'metadata.json').write_text(json.dumps({'route':'ref2v','motion_input':str(motion),'motion_input_sha256':'old'}))
            with patch('media.video_info',return_value=({}, {'duration_ts':72000,'time_base':'1/24000'},3.)):
                with self.assertRaisesRegex(ValueError,'Prepared motion changed'):f.prepare()


if __name__=='__main__':unittest.main()
