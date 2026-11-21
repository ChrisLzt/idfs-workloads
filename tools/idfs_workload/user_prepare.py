"""Render and execute the common finite User pass used to populate containers."""
import os
from pathlib import Path
import re
import stat
import subprocess

from .core import safe_path
from .user_runtime import execution

MIB = 2**20
ALIGNMENT = 4096
CONFIG = 'prepare_data.vdb'
# RD_entry.NO_ELAPSED in the pinned 5.04.06 JAR. Omitting elapsed defaults to 30s.
NO_ELAPSED = 99_999_998


def plan(file, capacity, *, transfer=4*MIB, stride=None, offset=0):
    stride = transfer if stride is None else stride
    dense = stride == transfer and offset == 0
    if (any(type(n) is not int or n % ALIGNMENT for n in (capacity, transfer, stride, offset))
            or min(capacity, transfer, stride) <= 0 or transfer >= 2**31
            or not 0 <= offset <= stride - transfer or (not dense and capacity % stride)):
        raise ValueError('preparation extents and transfers must be 4 KiB aligned and fit each extent')
    slots = (capacity + stride - 1) // stride
    return dict(file=str(safe_path(file)), capacity_bytes=capacity, stride_bytes=stride,
                payload_offset=offset, transfer_bytes=transfer, slots=slots,
                written_bytes=capacity if dense else slots*transfer)


def config_text(spec, bundle, fingerprint, *, first=0, end=None):
    end = spec['slots'] if end is None else end
    if not 0 <= first < end <= spec['slots']:
        raise ValueError('preparation range exceeds the file layout')
    size = spec['transfer_bytes']
    # Bound the outstanding I/O buffers for large dense write chunks.
    threads = min(16, max(1, 64*MIB//size))
    return '\n'.join([
        '* Separate User preparation pass; populate the configured file extents to EOF',
        'data_errors=1', 'messagescan=no', 'formatsds=no',
        f'hd=default,jvms=1,vdbench={safe_path(bundle)}',
        'hd=local,system=localhost',
        f'sd=target,host=local,lun={spec["file"]},size={spec["capacity_bytes"]},'
        f'threads={threads},openflags=o_direct,offset=0,align={ALIGNMENT}',
        f'wd=populate,sd=target,xfersize={size},seekpct=eof,rdpct=0,'
        f'user=(idfscommon.ContainerPrepare,extent{spec["capacity_bytes"]},stride{spec["stride_bytes"]},'
        f'offset{spec["payload_offset"]},payload{size},first{first},end{end},jarsha{fingerprint})',
        f'rd=prepare,wd=populate,iorate=max,elapsed={NO_ELAPSED},interval=1,warmup=0',
        '',
    ])


def expected_bytes(spec, first, end):
    stride = spec['stride_bytes']
    transfer = spec['transfer_bytes']
    final_lba = (end - 1)*stride + spec['payload_offset']
    return (end - first - 1)*transfer + min(transfer, spec['capacity_bytes'] - final_lba)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _require_owned_file(file, fd, size):
    live = file.lstat()
    opened = os.fstat(fd)
    if (not stat.S_ISREG(live.st_mode) or live.st_nlink != 1
            or (live.st_dev, live.st_ino) != (opened.st_dev, opened.st_ino)
            or live.st_size != size):
        raise ValueError(f'prepared file no longer matches the opened file and expected size: {file}')


def write_new_file(bundle, manifest, vdbench, output, mount):
    """Create the first file under a caller's durable preparation intent."""
    from .file_layout import require_layout

    file = safe_path(manifest['preparation']['file'])
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        require_layout(file, fstype=mount['fstype'])
        os.ftruncate(fd, manifest['capacity_bytes'])
        return write_pass(bundle, manifest, vdbench, output, fd)
    finally:
        os.close(fd)


def write_pass(bundle, manifest, vdbench, output, fd, *, first=0, end=None, lock_fd=None):
    bundle, output = safe_path(bundle), safe_path(output)
    spec = manifest['preparation']
    file = safe_path(spec['file'])
    end = spec['slots'] if end is None else end
    fingerprint = manifest['asset_sha256'][manifest['user_jar']]
    generated = config_text(spec, bundle, fingerprint, first=first, end=end)
    config = bundle/CONFIG
    if generated != config.read_text():
        config = output/CONFIG
        with config.open('x') as stream:
            stream.write(generated)
    reports = output/'vdbench'
    options = execution(bundle, vdbench, config, reports)
    _require_owned_file(file, fd, spec['capacity_bytes'])
    # Retain the preparation lock in the local Vdbench master if the wrapper exits.
    subprocess.run(**options, check=True, pass_fds=() if lock_fd is None else (lock_fd,))
    # The stock postIO hook does not expose the native return code. Require
    # successful Vdbench exit, an error-free run and the exact callback receipt.
    errors = (reports/'errorlog.html').read_text(errors='replace')
    summary = (reports/'summary.html').read_text(errors='replace')
    receipt = re.findall(r'\bIDFS_PREPARE_COMPLETE (\d+) (\d+) (\d+)\s*$', summary, re.M)
    written = expected_bytes(spec, first, end)
    if receipt != [(str(first), str(end), str(written))] or re.search(
            r'\bop:\s+(?:read|write)\s+lun:.*\berrno:', errors):
        raise ValueError(f'Vdbench did not confirm the full write pass; inspect {reports}')
    _require_owned_file(file, fd, spec['capacity_bytes'])
    os.fsync(fd)
    return written
