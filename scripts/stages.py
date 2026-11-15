"""Small local checkpoints. Never submit or repeat a GPU job."""
from contextlib import contextmanager
import json
import os
from pathlib import Path

from native_client import sha, write


class Stage:
    def __init__(self, directory, spec):
        self.directory = Path(directory)
        self.manifest = self.directory / '.stage.json'
        if self.manifest.exists():
            self.data = json.loads(self.manifest.read_text(encoding='utf-8-sig'))
            if self.data.get('spec') != spec:
                raise ValueError('Stage inputs/settings changed; use a new output directory')
        else:
            if self.directory.exists() and any(self.directory.iterdir()):
                raise ValueError('Existing output has no checkpoint; preserve it and use a new directory')
            self.directory.mkdir(parents=True, exist_ok=True)
            self.data = {'version': 1, 'spec': spec, 'steps': {}}
            write(self.manifest, self.data)

    @contextmanager
    def locked(self):
        lock = self.directory / '.active.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError('Local stage is locked; confirm its process stopped before removing .active.lock') from None
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(str(os.getpid()))
        try:
            yield self
        finally:
            lock.unlink(missing_ok=True)

    def file(self, name, destination, produce):
        destination = Path(destination)
        relative = str(destination.relative_to(self.directory))
        previous = self.data['steps'].get(name)
        if previous and previous.get('status') == 'done':
            if not destination.is_file() or sha(destination) != previous['sha256']:
                raise ValueError('Completed stage output changed: ' + relative)
            return destination
        if destination.exists() and not previous:
            raise ValueError('Untracked output will not be overwritten: ' + relative)
        self.data['steps'][name] = {'status': 'pending', 'file': relative}
        write(self.manifest, self.data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.stem + '.partial' + destination.suffix)
        # Only this stage's unfinished temporary file may be replaced on retry.
        produce(partial)
        if not partial.is_file() or not partial.stat().st_size:
            raise ValueError('Stage produced no data: ' + relative)
        partial.replace(destination)
        self.data['steps'][name] = {'status': 'done', 'file': relative, 'sha256': sha(destination)}
        write(self.manifest, self.data)
        return destination

    def complete(self):
        self.data['complete'] = True
        write(self.manifest, self.data)


def input_file(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path)}
