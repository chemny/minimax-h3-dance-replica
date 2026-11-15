"""CPU-only regressions. Media fixtures are synthetic; no ComfyUI jobs are sent."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from stages import Stage
from routes import recipe
from direct_routes import DirectPipeline
from runtime_checks import server_modules
from model_inventory import inventory
from segments import SegmentedPipeline
from dance import Pipeline
import media
import compare
import check_environment


class PortabilityTests(unittest.TestCase):
    def test_cli_helpers_handle_chinese_paths_with_legacy_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'中文 路径';root.mkdir()
            record=root/'表演.json';record.write_text(json.dumps({'duration_seconds':2,'identity':'人物一致','scene_camera':'固定机位','segments':[{'start':0,'end':2,'confidence':'checked','visibility':'visible','body':'按原片抬手。'}]},ensure_ascii=False),encoding='utf-8-sig')
            prompt=root/'提示词.txt'
            env=dict(os.environ,PYTHONIOENCODING='cp1252',PYTHONDONTWRITEBYTECODE='1')
            result=subprocess.run([sys.executable,'-X','utf8=0','-B',str(ROOT/'scripts/prompt.py'),str(record),'--out',str(prompt)],capture_output=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            graphdir=root/'图'
            result=subprocess.run([sys.executable,'-X','utf8=0','-B',str(ROOT/'scripts/h3_run.py'),'build','--first','first.png','--last','last.png','--depth','depth.mp4','--prompt',str(prompt),'--duration','2','--out',str(graphdir)],capture_output=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            graph=json.loads((graphdir/'graph.json').read_text(encoding='utf-8'))
            self.assertIn('按原片抬手',graph['9']['inputs']['prompt'])

    def test_model_inventory_selects_recipe_without_downloading(self):
        r=inventory({'route':'ref2v','steps':8})
        names={m['filename'] for m in r['models']}
        self.assertTrue(any('ref2v_turbo_8step' in n for n in names))
        self.assertFalse(any('fl2v_turbo' in n or 'sam3' in n for n in names))
        self.assertFalse(r['downloaded'])
        self.assertTrue(all(m['sha256'] and m['revision'] and '/resolve/' in m['url'] for m in r['models']))

    def test_non_utf8_default_and_bom_config_keep_chinese_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'中文目录';root.mkdir()
            (root/'source.mp4').write_bytes(b'fixture');(root/'photo.png').write_bytes(b'fixture')
            text='照片决定人物。2.5秒右手举过头顶。'
            config=root/'任务.json';config.write_text(json.dumps({'route':'ref2v','source':'source.mp4','identity':'photo.png','out':'out','prompt':text},ensure_ascii=False),encoding='utf-8-sig')
            flow=DirectPipeline(config)
            original=Path.read_text
            def legacy_read(path,*args,**kwargs):
                kwargs.setdefault('encoding','cp1252')
                return original(path,*args,**kwargs)
            with patch.object(flow,'prepare'),patch.object(flow,'meta',return_value={'duration_seconds':3}),patch.object(Path,'read_text',legacy_read):
                graph,_=flow.preview()
            self.assertEqual(graph['9']['inputs']['prompt'],text)

    def test_character_swap_does_not_require_unused_sage(self):
        self.assertNotIn('sageattention',server_modules(recipe({'route':'character_swap'})))
        self.assertIn('comfy_kitchen',server_modules(recipe({'route':'character_swap'})))
        self.assertIn('sageattention',server_modules(recipe({'route':'ref2v'})))
        self.assertNotIn('sageattention',server_modules(recipe({'route':'ref2v','sage':False})))

    def test_api_only_check_does_not_contact_local_comfy(self):
        requests=[]
        class Reply(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class Opener:
            def open(self,request,timeout):
                requests.append(request.full_url)
                return Reply(json.dumps({'workflows':{'video.reference':{}}}).encode())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);key=root/'key';key.write_text('fixture-only',encoding='utf-8-sig')
            config=root/'config.json'
            config.write_text(json.dumps({'route':'ref2v','connection':{'kind':'gateway','url':'http://wrong.invalid','key_file':'missing-key'}}),encoding='utf-8-sig')
            with patch('check_environment.client_checks',return_value=[]),patch('check_environment.urllib.request.build_opener',return_value=Opener()),patch.object(sys,'argv',['check','--config',str(config),'--api-url','http://example.invalid:8788','--key-file',str(key),'--out',str(root/'report.json')]):
                check_environment.main()
                report=json.loads((root/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(requests,['http://example.invalid:8788/v1/workflows'])
        self.assertEqual(report['mode'],'gateway')
        self.assertFalse(any(c['name'] in ('comfy_install','comfy_python','cuda') for c in report['checks']))

    def test_missing_client_requirement_fails_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'output'
            with patch('runtime_checks.client_checks',return_value=[{'name':'client_Pillow','status':'fail','next_action':'Install Pillow'}]):
                with self.assertRaisesRegex(RuntimeError,'Pillow'):
                    media.prepare(SimpleNamespace(out=dest))
            self.assertFalse(dest.exists())

    def test_checkpoint_refuses_changed_inputs_and_foreign_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);s=Stage(root/'task',{'input':'a'})
            with s.locked():s.file('data',root/'task/data.bin',lambda p:p.write_bytes(b'good'))
            with self.assertRaisesRegex(ValueError,'changed'):Stage(root/'task',{'input':'b'})
            (root/'task/data.bin').write_bytes(b'edited')
            with s.locked(),self.assertRaisesRegex(ValueError,'changed'):
                s.file('data',root/'task/data.bin',lambda p:p.write_bytes(b'bad'))
            foreign=root/'foreign';foreign.mkdir();(foreign/'user.txt').write_text('keep')
            with self.assertRaisesRegex(ValueError,'no checkpoint'):Stage(foreign,{'input':'a'})
            self.assertEqual((foreign/'user.txt').read_text(),'keep')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg is required for synthetic media checks')
class MediaResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)/'中文 素材';cls.root.mkdir()
        cls.source=cls.root/'原始.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=256x256:rate=24:duration=2','-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(cls.source)],check=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_prepare_resumes_without_encoding_motion_again(self):
        dest=self.root/'预处理';args=SimpleNamespace(source=self.source,duration=2.,width=256,height=256,sheet_fps=2,out=dest)
        original=media.run
        def interrupted(command):
            if 'review-frames' in command[-1]:raise RuntimeError('synthetic interruption')
            return original(command)
        with patch('media.run',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
            media.prepare(args)
        picture=(dest/'motion-24fps.mp4').read_bytes();commands=[]
        def resumed(command):commands.append(command);return original(command)
        with patch('media.run',side_effect=resumed):media.prepare(args)
        self.assertEqual((dest/'motion-24fps.mp4').read_bytes(),picture)
        self.assertFalse(any('tpad=' in ' '.join(c) for c in commands))
        self.assertTrue((dest/'metadata.json').is_file())

    def test_reference_encoding_resumes_with_same_prepared_source(self):
        root=self.root/'参考';root.mkdir();(root/'photo.png').write_bytes(b'fixture')
        p=root/'config.json';p.write_text(json.dumps({'route':'ref2v','source':str(self.source),'identity':'photo.png','out':'out','prompt':'Follow the video.','width':256,'height':256}),encoding='utf-8')
        flow=DirectPipeline(p);original=media.run
        def interrupted(command):
            if 'motion-reference.partial' in command[-1]:raise RuntimeError('synthetic interruption')
            return original(command)
        with patch('media.run',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
            flow.prepare()
        commands=[]
        def resumed(command):commands.append(command);return original(command)
        with patch('media.run',side_effect=resumed):flow.prepare()
        self.assertEqual(len(commands),1)
        self.assertIn('motion-reference.partial',commands[0][-1])
        self.assertTrue(Path(flow.meta()['motion_input']).is_file())

    def test_finish_resumes_after_picture_without_reencoding_or_gpu(self):
        dest=self.root/'交付';args=SimpleNamespace(raw=self.source,source=self.source,duration=2.,out=dest)
        original=media.run
        def interrupted(command):
            if '-vn' in command:raise RuntimeError('synthetic interruption')
            return original(command)
        with patch('media.run',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
            media.finish(args)
        before=(dest/'picture.mp4').read_bytes();commands=[]
        def resumed(command):commands.append(command);return original(command)
        with patch('media.run',side_effect=resumed),patch('native_client.Client.stage',side_effect=AssertionError('GPU submission forbidden')):
            media.finish(args)
        self.assertEqual((dest/'picture.mp4').read_bytes(),before)
        self.assertFalse(any('-c:v' in c for c in commands))
        verification=json.loads((dest/'verification.json').read_text())
        self.assertEqual(verification['frames'],48)
        self.assertTrue(verification['source_audio_packets_equal'])

    def test_pipeline_finish_checks_saved_video_before_reusing_report(self):
        root=self.root/'交付缓存';dest=root/'delivery'
        media.finish(SimpleNamespace(raw=self.source,source=self.source,duration=2.,out=dest))
        video=dest/'result.mp4';original=video.read_bytes()
        flows=[]
        for cls in (Pipeline,DirectPipeline,SegmentedPipeline):
            flow=object.__new__(cls);flow.out=root;flows.append(flow)
        with patch('native_client.Client.stage',side_effect=AssertionError('GPU submission forbidden')):
            for flow in flows:flow.finish()
            video.write_bytes(original+b'changed')
            for flow in flows:
                with self.assertRaisesRegex(ValueError,'delivery changed'):flow.finish()
            self.assertEqual(video.read_bytes(),original+b'changed')
            video.unlink()
            for flow in flows:
                with self.assertRaisesRegex(ValueError,'missing'):flow.finish()

    def test_pipeline_compare_rejects_stale_acceptance_and_changed_comparison(self):
        root=self.root/'验收缓存';result=root/'delivery/result.mp4'
        result.parent.mkdir(parents=True);shutil.copyfile(self.source,result)
        dest=root/'comparison';compare.compare(self.source,result,dest,cell_width=128,interval=1)
        flows=[]
        for cls in (Pipeline,DirectPipeline,SegmentedPipeline):
            flow=object.__new__(cls);flow.out=root;flow.source=self.source
            flow.c={'source':self.source,'performance':None};flow.resolve=lambda p:p
            flows.append(flow)
        acceptance=dest/'acceptance.json';review=acceptance.read_bytes()
        original=result.read_bytes()
        for flow in flows:flow.compare()
        result.write_bytes(original+b'changed')
        for flow in flows:
            with self.assertRaisesRegex(ValueError,'inputs changed'):flow.compare()
        self.assertEqual(acceptance.read_bytes(),review)
        result.write_bytes(original)
        comparison=dest/'comparison.mp4';comparison.write_bytes(comparison.read_bytes()+b'changed')
        for flow in flows:
            with self.assertRaisesRegex(ValueError,'comparison changed'):flow.compare()
        self.assertEqual(acceptance.read_bytes(),review)

    def test_compare_resumes_snapshots_and_preserves_acceptance(self):
        dest=self.root/'对照';original=compare.ffmpeg
        def interrupted(command):
            if any('select=eq' in str(x) for x in command):raise RuntimeError('synthetic interruption')
            return original(command)
        with patch('compare.ffmpeg',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
            compare.compare(self.source,self.source,dest,cell_width=128,interval=1)
        commands=[]
        def resumed(command):commands.append(command);return original(command)
        with patch('compare.ffmpeg',side_effect=resumed):compare.compare(self.source,self.source,dest,cell_width=128,interval=1)
        self.assertFalse(any('-filter_complex' in c for c in commands))
        acceptance=dest/'acceptance.json';data=json.loads(acceptance.read_text());data['user_acceptance']='accepted';acceptance.write_text(json.dumps(data),encoding='utf-8')
        compare.compare(self.source,self.source,dest,cell_width=128,interval=1)
        self.assertEqual(json.loads(acceptance.read_text())['user_acceptance'],'accepted')

    def test_segment_join_resumes_without_trimming_again(self):
        root=self.root/'分段';root.mkdir();source=root/'source4.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-stream_loop','1','-i',str(self.source),'-t','4','-c','copy',str(source)],check=True)
        children=[]
        for i in range(2):
            out=root/f'child{i}';(out/'delivery').mkdir(parents=True);dest=out/'delivery/result.mp4'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(self.source),'-vf','tpad=stop_mode=clone:stop_duration=0.1','-frames:v',str(49 if i==0 else 48),'-an','-c:v','libx264',str(dest)],check=True)
            children.append(SimpleNamespace(out=out,finish=lambda:None))
        flow=object.__new__(SegmentedPipeline);flow.out=root/'case';flow.out.mkdir();flow.source=source
        flow.p={'segments':[{'delivery_frames':49},{'delivery_frames':48}],'delivery_frames':96,'source_duration_seconds':4.,'segment_count':2}
        flow.children=lambda:children
        original=media.run
        def interrupted(command):
            if 'concat' in command:raise RuntimeError('synthetic interruption')
            return original(command)
        with patch('media.run',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
            flow.finish()
        commands=[]
        def resumed(command):commands.append(command);return original(command)
        with patch('media.run',side_effect=resumed):flow.finish()
        self.assertFalse(any('trim=start_frame' in ' '.join(c) for c in commands))
        self.assertEqual(json.loads((flow.out/'delivery/verification.json').read_text())['frames'],96)


if __name__=='__main__':unittest.main()
