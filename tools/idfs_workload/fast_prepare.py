"""Prepare the sparse shared FAST container with durable checkpoints."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

from .core import json_text, safe_path
from .fast_container import CAPACITY_BYTES, FILE_NAME, OBJECT_BYTES, OBJECTS, PAYLOAD_BYTES, SEED, TRANSFER_BYTES
from .file_layout import require_layout
from .lifecycle import READY, _existing_parent, _storage_target, container_ready, record_container_ready
from .user_prepare import sync_directory, write_pass

LOCK = '.fast.lock'
STATE = '.fast-state.json'
STATE_TEMP = '.fast-state.tmp'
# A stock Vdbench JVM startup and report pair per 25 GiB at the formal scale.
CHECKPOINT_SLOTS = 65536


@contextmanager
def dataset_lock(root, *, create):
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    fd = os.open(root/LOCK, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('FAST lock must be a regular file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def identity():
    # Keep the readiness identity independent of the Vdbench bundle and JAR.
    return dict(schema=2, seed=SEED, files=[dict(name=FILE_NAME, bytes=CAPACITY_BYTES)],
                object_bytes=OBJECT_BYTES, payload_bytes=PAYLOAD_BYTES, payload_offset=TRANSFER_BYTES)


def save_state(root, state):
    path = safe_path(root/STATE_TEMP)
    with path.open('w') as stream:
        stream.write(json_text(state))
        stream.flush()
        os.fsync(stream.fileno())
    path.replace(root/STATE)
    sync_directory(root)


def load_state(root):
    path = safe_path(root/STATE)
    allowed = {LOCK, STATE, STATE_TEMP, FILE_NAME, READY, READY+'.tmp'}
    if any(child.name not in allowed or child.is_symlink() for child in root.iterdir()):
        raise ValueError('FAST data directory contains unrelated files or symlinks')
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    if state['identity'] != identity():
        raise ValueError('FAST preparation state does not match this layout')
    entry = state['file']
    if entry is not None and (
        type(entry['inode']) is not int or type(entry['slots']) is not int
        or not 0 <= entry['slots'] <= OBJECTS
    ):
        raise ValueError('invalid FAST preparation checkpoint')
    return state


def verify_file(path, info, entry):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_ino != entry['inode']
            or (info.st_size != CAPACITY_BYTES and not (entry['slots'] == 0 and info.st_size == 0))):
        raise ValueError(f'FAST file identity or length differs from preparation state: {path}')


def completed_inode(root):
    """Recognize a completed container from the previous state-file format."""
    root = safe_path(root)
    if not root.is_dir():
        raise ValueError('FAST preparation is incomplete')
    state = load_state(root)
    if state is None or state['file'] is None or state['file']['slots'] != OBJECTS:
        raise ValueError('FAST preparation is incomplete')
    return state['file']['inode']


def available_space(path, slots):
    required = ((OBJECTS - slots)*PAYLOAD_BYTES*105 + 99) // 100
    space = os.statvfs(path)
    available = space.f_bavail*space.f_frsize
    if available < required:
        raise ValueError(f'FAST remaining payload plus 5% headroom requires {required} available bytes')
    return required, available


def preflight(spec):
    root, mount = _storage_target(spec['data_root'])
    ancestor = _existing_parent(root)
    state = load_state(root) if root.exists() else None
    entry = state['file'] if state is not None else None
    if entry is not None and entry['slots'] == OBJECTS:
        ready = container_ready(spec)
        required, available = available_space(root, OBJECTS)
        return dict(ready, required_free_bytes=required, available_bytes=available)
    if (root/READY).exists():
        raise ValueError('FAST ready record conflicts with preparation progress')
    layout = require_layout(ancestor, directory=True, stop=mount['target'], fstype=mount['fstype'])
    file = safe_path(root/FILE_NAME)
    if entry is not None:
        verify_file(file, file.stat(), entry)
        layout = require_layout(file, fstype=mount['fstype'])
    elif file.exists() and (state is None or not file.is_file() or file.stat().st_size != 0):
        raise ValueError('FAST data directory contains an unowned container')
    required, available = available_space(ancestor, entry['slots'] if entry else 0)
    return dict(id=spec['id'], mount=mount, data_root=str(root), capacity_bytes=spec['capacity_bytes'],
                required_free_bytes=required, available_bytes=available, layout_verification=layout['verification'])


def prepare(spec, *, bundle, vdbench, output):
    root, mount = _storage_target(spec['data_root'])
    ancestor = _existing_parent(root)
    require_layout(ancestor, directory=True, stop=mount['target'], fstype=mount['fstype'])
    root.mkdir(parents=True, exist_ok=True)
    with dataset_lock(root, create=True) as lock:
        state = load_state(root)
        if state is None:
            if any(file.name not in (LOCK, STATE_TEMP) for file in root.iterdir()):
                raise ValueError('FAST requires an empty directory or its own preparation state')
            state = dict(identity=identity(), file=None)
            save_state(root, state)
        entry = state['file']
        if entry is not None and entry['slots'] == OBJECTS:
            return dict(container_ready(spec), reused_existing_data=True)
        if (root/READY).exists():
            raise ValueError('FAST ready record conflicts with preparation progress')
        available_space(root, entry['slots'] if entry else 0)

        file = safe_path(root/FILE_NAME)
        flags = os.O_RDWR | os.O_NOFOLLOW
        if entry is None and not file.exists():
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(file, flags, 0o644)
        try:
            info = os.fstat(fd)
            if entry is None:
                # A crash can leave an empty file before its inode is checkpointed.
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0:
                    raise ValueError(f'unowned FAST container is not an empty regular file: {file}')
                entry = dict(inode=info.st_ino, slots=0)
                state['file'] = entry
                save_state(root, state)
            verify_file(file, info, entry)
            require_layout(file, fstype=mount['fstype'])
            if info.st_size == 0:
                os.ftruncate(fd, CAPACITY_BYTES)
            print(f'FAST {file.name} starting at slot {entry["slots"]}/{OBJECTS}', file=sys.stderr, flush=True)
            for first in range(entry['slots'], OBJECTS, CHECKPOINT_SLOTS):
                end = min(first + CHECKPOINT_SLOTS, OBJECTS)
                attempt = Path(output)/f'objects-{first:07d}-{end:07d}'
                attempt.mkdir()
                write_pass(bundle, spec, vdbench, attempt, fd, first=first, end=end, lock_fd=lock)
                entry['slots'] = end
                save_state(root, state)
        finally:
            os.close(fd)
        verify_file(file, file.lstat(), entry)
        return record_container_ready(spec)
