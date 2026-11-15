"""Windows-only client smoke test; fake gateway, no GPU or external service."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]


def main():
    if os.name!='nt':
        raise SystemExit('Run this entry-point check on Windows.')
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            requests.append(('GET',self.path))
            if self.path!='/v1/workflows' or self.headers.get('Authorization')!='Bearer fixture-only-key':
                self.send_error(400);return
            data=json.dumps({'workflows':{'video.reference':{}}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            requests.append(('POST',self.path));self.send_error(405)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='H3-Windows-') as temporary:
            base=Path(temporary)/'中文 路径';base.mkdir()
            installed=base/'minimax-h3-dance-replica'
            shutil.copytree(ROOT,installed,ignore=shutil.ignore_patterns('.git','__pycache__'))
            key=base/'密钥';key.write_text('fixture-only-key',encoding='utf-8-sig')
            config=base/'任务.json'
            config.write_text(json.dumps({'route':'ref2v','steps':8,'connection':{
                'kind':'gateway','url':f'http://127.0.0.1:{server.server_port}','key_file':key.name}},ensure_ascii=False),encoding='utf-8-sig')
            def run(args,**kwargs):
                p=subprocess.run(args,cwd=installed,stdin=subprocess.DEVNULL,capture_output=True,timeout=120,**kwargs)
                if p.returncode:
                    raise AssertionError(json.dumps({'command':str(args),'returncode':p.returncode,
                        'stdout':p.stdout.decode('utf-8','replace'),'stderr':p.stderr.decode('utf-8','replace')},ensure_ascii=True))
                return p
            run([sys.executable,'-X','utf8=0','-B','scripts/dance.py','routes'])
            new_config=base/'新任务.json'
            run([sys.executable,'-X','utf8=0','-B','scripts/dance.py','init','--route','ref2v','--config',str(new_config)])
            assert json.loads(new_config.read_text(encoding='utf-8'))['route']=='ref2v'
            run([sys.executable,'-X','utf8=0','-B','scripts/dance.py','check','--config',str(config)])
            run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
                 str(installed/'scripts/check_environment.ps1'),'-Config',str(config),'-ClientPython',sys.executable])
            command=f'call "{installed / "Check-Environment.bat"}" -Config "{config}" -ClientPython "{sys.executable}"'
            # cmd parses its own quoting; list2cmdline escapes quotes for a C runtime.
            run('cmd.exe /d /s /c \"'+command+'\"')
            assert len(requests)==3 and all(r==('GET','/v1/workflows') for r in requests),requests
            report=json.loads((base/'environment-report.json').read_text(encoding='utf-8-sig'))
            assert report['mode']=='gateway' and report['route']=='ref2v' and report['steps']==8
            assert not any(x['name'] in ('comfy_install','comfy_python','cuda') for x in report['checks'])
            assert not any(x['status']=='fail' for x in report['checks']),report
            assert 'fixture-only-key' not in json.dumps(report)
            print(json.dumps({'windows_entrypoints':'pass','installed_in_chinese_path':True,
                'python_entry':True,'powershell_entry':True,'batch_entry':True,'bom_config_and_key':True,
                'requests':requests,'gpu_jobs_submitted':0},ensure_ascii=True))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=10)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8')
    main()
