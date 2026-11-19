"""Prepare sparse FAST containers with durable per-shard checkpoints."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from .core import json_text, safe_path
from .fast_container import OBJECT_BYTES, PAYLOAD_BYTES, SEED, SHARD_BYTES, SHARDS, SLOTS_PER_SHARD, TRANSFER_BYTES, shard_name
from .file_layout import require_layout
from .lifecycle import _existing_parent, _storage_target

LOCK = '.fast.lock'
STATE = '.fast-state.json'
STATE_TEMP = '.fast-state.tmp'
CHECKPOINT_SLOTS = 4096


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
        yield
    finally:
        os.close(fd)


def check_layout(path, mount, confirm_nfs, *, directory=False):
    result = require_layout(path, directory=directory, stop=mount['target'], fstype=mount['fstype'])
    if result['layout'] is None and not confirm_nfs:
        raise ValueError('NFS does not expose the layout; verify 4 MiB objects and stripe count 1 on the backend, then pass --confirm-nfs-layout')
    return result['verification']


def identity(spec):
    return {key: spec[key] for key in ('schema', 'seed', 'files', 'object_bytes', 'payload_bytes', 'payload_offset')}


def save_state(root, state):
    path = safe_path(root/STATE_TEMP)
    with path.open('w') as stream:
        stream.write(json_text(state))
        stream.flush()
        os.fsync(stream.fileno())
    path.replace(root/STATE)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_state(root, spec):
    path = safe_path(root/STATE)
    allowed = {LOCK, STATE, STATE_TEMP, *(shard_name(i) for i in range(SHARDS))}
    if any(child.name not in allowed or child.is_symlink() for child in root.iterdir()):
        raise ValueError('FAST data directory contains unrelated files or symlinks')
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    parts = state['parts']
    if state['identity'] != identity(spec) or len(parts) != SHARDS:
        raise ValueError('FAST preparation state does not match this layout')
    for entry in parts:
        if entry is not None and (
            type(entry['inode']) is not int or type(entry['slots']) is not int
            or not 0 <= entry['slots'] <= SLOTS_PER_SHARD
        ):
            raise ValueError('invalid FAST preparation checkpoint')
    return state


def verify_file(path, info, entry):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_ino != entry['inode']
            or (info.st_size != SHARD_BYTES and not (entry['slots'] == 0 and info.st_size == 0))):
        raise ValueError(f'FAST file identity or length differs from preparation state: {path}')


def check(spec, confirm_nfs):
    root, mount = _storage_target(spec['data_root'])
    state = load_state(root, spec)
    if state is None or any(entry is None or entry['slots'] != SLOTS_PER_SHARD for entry in state['parts']):
        raise ValueError('FAST preparation is incomplete')
    layouts = set()
    for index, entry in enumerate(state['parts']):
        file = safe_path(root/shard_name(index))
        verify_file(file, file.stat(), entry)
        layouts.add(check_layout(file, mount, confirm_nfs))
    return dict(state='ready', data_root=str(root), files=SHARDS, logical_bytes=spec['capacity_bytes'],
                payload_bytes=spec['effective_bytes'], objects=spec['object_count'], layout=sorted(layouts))


def write_slot(fd, index, slot):
    global_slot = index*SLOTS_PER_SHARD + slot
    salt = (SEED ^ global_slot).to_bytes(8, 'little')
    payload = memoryview(hashlib.shake_256(salt).digest(PAYLOAD_BYTES))
    offset = slot*OBJECT_BYTES + TRANSFER_BYTES
    while payload:
        count = os.pwrite(fd, payload, offset)
        if count <= 0:
            raise OSError('FAST payload write made no progress')
        payload = payload[count:]
        offset += count


def prepare(spec, confirm_nfs):
    root, mount = _storage_target(spec['data_root'])
    ancestor = _existing_parent(root)
    check_layout(ancestor, mount, confirm_nfs, directory=True)
    root.mkdir(parents=True, exist_ok=True)
    with dataset_lock(root, create=True):
        state = load_state(root, spec)
        if state is None:
            if any(file.name not in (LOCK, STATE_TEMP) for file in root.iterdir()):
                raise ValueError('FAST requires an empty directory or its own preparation state')
            state = dict(identity=identity(spec), parts=[None]*SHARDS)
            save_state(root, state)
        pending = sum(SLOTS_PER_SHARD - (entry['slots'] if entry else 0) for entry in state['parts'])
        space = os.statvfs(root)
        required = (pending*PAYLOAD_BYTES*105 + 99) // 100
        if space.f_bavail*space.f_frsize < required:
            raise ValueError(f'FAST remaining payload plus 5% headroom requires {required} available bytes')

        for index, entry in enumerate(state['parts']):
            file = safe_path(root/shard_name(index))
            if entry is not None and entry['slots'] == SLOTS_PER_SHARD:
                verify_file(file, file.stat(), entry)
                check_layout(file, mount, confirm_nfs)
                continue
            flags = os.O_RDWR | os.O_NOFOLLOW
            if entry is None and not file.exists():
                flags |= os.O_CREAT | os.O_EXCL
            fd = os.open(file, flags, 0o644)
            try:
                info = os.fstat(fd)
                if entry is None:
                    # A crash can leave an empty shard before its inode is checkpointed.
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0:
                        raise ValueError(f'unowned FAST shard is not an empty regular file: {file}')
                    entry = dict(inode=info.st_ino, slots=0)
                    state['parts'][index] = entry
                    save_state(root, state)
                verify_file(file, info, entry)
                check_layout(file, mount, confirm_nfs)
                if info.st_size == 0:
                    os.ftruncate(fd, SHARD_BYTES)
                print(f'FAST {file.name} starting at slot {entry["slots"]}/{SLOTS_PER_SHARD}', file=sys.stderr, flush=True)
                for slot in range(entry['slots'], SLOTS_PER_SHARD):
                    write_slot(fd, index, slot)
                    if (slot + 1) % CHECKPOINT_SLOTS == 0 or slot + 1 == SLOTS_PER_SHARD:
                        os.fdatasync(fd)
                        entry['slots'] = slot + 1
                        save_state(root, state)
            finally:
                os.close(fd)
        return check(spec, confirm_nfs)
