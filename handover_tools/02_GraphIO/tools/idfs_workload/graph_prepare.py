"""Explicit independent graph-file preparation; never invoked by the read test."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time

from .core import json_text,safe_path,validate_bundle
from .file_layout import require_layout


def markers(file):
    file=Path(file)
    return file.with_name('.'+file.name+'.preparing.json'),file.with_name('.'+file.name+'.ready.json')


def _sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def preflight(bundle):
    from . import graph_zipf,lifecycle
    manifest=validate_bundle(bundle);file=safe_path(manifest['data_file'])
    intent,ready=markers(file)
    if intent.exists() or intent.is_symlink():raise ValueError('graph preparation is incomplete or active;inspect its intent record')
    if file.exists():return graph_zipf.check_ready(bundle)
    if ready.exists() or ready.is_symlink():raise ValueError('graph preparation marker exists without its data file;inspect before retrying')
    mount=lifecycle.mount_info(file)
    if mount['fstype'] not in ('ceph','idfs','fuse.ceph','fuse.ceph-fuse','fuse.idfs','fuse.idfs-fuse','nfs','nfs4'):
        raise ValueError('graph preparation target must be an IDFS/CephFS/NFS mount')
    parent=lifecycle._existing_parent(file.parent)
    if not parent.is_dir() or not os.access(parent,os.W_OK):raise ValueError('graph data parent is not writable')
    fs=os.statvfs(parent);available=fs.f_bavail*fs.f_frsize
    required=(manifest['capacity_bytes']*105+99)//100
    if available<required:raise ValueError('insufficient free space for graph preparation including5% headroom')
    layout=require_layout(parent,directory=True,stop=mount['target'],fstype=mount['fstype'])
    return dict(id=manifest['id'],data_file=str(file),data_root=str(file.parent),mount=mount,
                capacity_bytes=manifest['capacity_bytes'],required_free_bytes=required,available_bytes=available,
                state='missing-file',layout=layout['layout'],layout_verification=layout['verification'])


def write_all(fd,total,chunk_bytes,progress):
    """Fill every byte,including short-write retries; no truncate/fallocate holes."""
    if type(total) is not int or total<=0 or type(chunk_bytes) is not int or chunk_bytes<=0:
        raise ValueError('file length and preparation chunk must be positive integers')
    written=0
    while written<total:
        payload=memoryview(os.urandom(min(chunk_bytes,total-written)))
        while payload:
            n=os.pwrite(fd,payload,written)
            if n<=0:raise OSError('graph preparation write made no progress')
            written+=n;payload=payload[n:]
        progress(written)
    return written


def prepare(bundle, *, output, execute=False, chunk_mib=16, vdbench=None):
    if not execute:raise ValueError('graph preparation writes data;explicit --execute is required')
    if type(chunk_mib) is not int or not 1<=chunk_mib<=1024:
        raise ValueError('preparation chunk MiB must be in1..1024')
    from . import graph_zipf,lifecycle
    bundle=Path(bundle).absolute();manifest=validate_bundle(bundle);report=preflight(bundle)
    if report['state']=='existing-file-ready':return dict(report,reused_existing_data=True)
    file=safe_path(manifest['data_file']);intent,ready=markers(file)
    destination=lifecycle._output(output,file.parent,bundle);destination.mkdir()
    file.parent.mkdir(parents=True,exist_ok=True)
    require_layout(file.parent,directory=True,stop=report['mount']['target'],fstype=report['mount']['fstype'])
    record=dict(id=manifest['id'],data_file=str(file),capacity_bytes=manifest['capacity_bytes'],
                layout_sha256=manifest['layout_sha256'],utc=datetime.now(timezone.utc).isoformat(),
                preparation_chunk_bytes=chunk_mib*2**20)
    with intent.open('x') as stream:
        stream.write(json_text(dict(record,state='preparing')))
        stream.flush();os.fsync(stream.fileno())
    # Persist the guard before any data can be created. A crash must not leave
    # an incomplete file looking like an externally prepared,complete input.
    _sync_directory(file.parent)
    started=time.monotonic();last=started
    def progress(written):
        nonlocal last
        now=time.monotonic()
        if now-last>=5 or written==manifest['capacity_bytes']:
            message=dict(written_bytes=written,total_bytes=manifest['capacity_bytes'],
                         percent=100*written/manifest['capacity_bytes'],elapsed_seconds=now-started)
            with (destination/'progress.jsonl').open('a') as stream:stream.write(json.dumps(message)+'\n')
            print(json.dumps(message),file=sys.stderr,flush=True);last=now
    fd=None
    try:
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o644)
        written=write_all(fd,manifest['capacity_bytes'],chunk_mib*2**20,progress)
        if written!=manifest['capacity_bytes']:raise OSError('graph preparation byte count mismatch')
        os.fsync(fd)
    finally:
        if fd is not None:os.close(fd)
    # Failure leaves intent and partial file intact; read tests reject that state.
    inventory=graph_zipf.check_ready(bundle,allow_preparing=True)
    with ready.open('x') as stream:
        stream.write(json_text(dict(record,state='ready',inventory=inventory)))
        stream.flush();os.fsync(stream.fileno())
    _sync_directory(file.parent)
    intent.unlink()
    _sync_directory(file.parent)
    result=dict(report,state='ready',written_bytes=written,output=str(destination),inventory=inventory)
    (destination/'result.json').write_text(json_text(result))
    return result
