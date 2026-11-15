"""Client requirements only; GPU packages belong to the ComfyUI interpreter."""
from functools import lru_cache
import importlib.metadata
import importlib.util
import platform
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 10)
REQUIRED_FILTERS = {'fps', 'scale', 'pad', 'tpad', 'select', 'trim', 'setpts', 'hstack'}


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')


def install_hint(component):
    if component == 'Pillow':
        return 'Install client requirements with this Python: -m pip install -r requirements.txt'
    if platform.system() == 'Windows':
        return 'Install FFmpeg, add its bin folder to PATH, then open a new terminal and rerun check.'
    return 'Install FFmpeg with your system package manager, then rerun check.'


@lru_cache(maxsize=1)
def client_checks():
    result = []
    def add(name, ok, detail, action=''):
        result.append({'name': name, 'status': 'pass' if ok else 'fail', 'detail': detail, 'next_action': action})
    add('client_python', sys.version_info >= MIN_PYTHON, sys.version.split()[0], 'Use Python 3.10 or newer for the client.')
    pillow = importlib.util.find_spec('PIL') is not None
    try:
        version = importlib.metadata.version('Pillow') if pillow else 'missing'
        pillow = pillow and int(version.split('.')[0]) >= 10
    except (importlib.metadata.PackageNotFoundError, ValueError):
        version = 'unknown'; pillow = False
    add('client_Pillow', pillow, version, install_hint('Pillow'))
    for executable in ('ffmpeg', 'ffprobe'):
        path = shutil.which(executable)
        if not path:
            add(executable, False, 'Not on PATH', install_hint(executable)); continue
        try:
            p = subprocess.run([path, '-version'], capture_output=True, encoding='utf-8', errors='replace', timeout=15)
            add(executable, p.returncode == 0, p.stdout.splitlines()[0] if p.stdout else 'Cannot run', install_hint(executable))
            if executable == 'ffmpeg' and p.returncode == 0:
                codecs = subprocess.run([path, '-hide_banner', '-encoders'], capture_output=True, encoding='utf-8', errors='replace', timeout=15)
                filters = subprocess.run([path, '-hide_banner', '-filters'], capture_output=True, encoding='utf-8', errors='replace', timeout=15)
                names = {parts[1] for line in filters.stdout.splitlines() if len(parts := line.split()) >= 2}
                missing = sorted(REQUIRED_FILTERS - names)
                add('ffmpeg_features', codecs.returncode == filters.returncode == 0 and 'libx264' in codecs.stdout and not missing,
                    {'libx264': 'libx264' in codecs.stdout, 'missing_filters': missing}, install_hint(executable))
        except (OSError, subprocess.TimeoutExpired):
            add(executable, False, 'Executable failed or timed out', install_hint(executable))
    return result


def require_client():
    failures = [c for c in client_checks() if c['status'] == 'fail']
    if failures:
        raise RuntimeError('Client requirements missing: ' + '; '.join(c['name'] + ': ' + c['next_action'] for c in failures))


def server_modules(recipe):
    modules = ['torch', 'numpy', 'safetensors', 'av', 'PIL', 'aiohttp']
    if recipe['route'] in ('depth', 'character_swap'):
        modules.append('cv2')
    if recipe.get('sage'):
        modules.append('sageattention')
    if recipe.get('attention_backend') == 'comfy kitchen attention':
        modules.append('comfy_kitchen')
    return modules
