"""Explicit independent graph-file preparation; never invoked by the read test."""
import os
from pathlib import Path

from .core import json_text,safe_path,validate_bundle
from .file_layout import require_layout
from .user_prepare import sync_directory, write_new_file


def markers(file):
    file=Path(file)
    return file.with_name('.'+file.name+'.preparing.json'),file.with_name('.'+file.name+'.ready.json')


def preflight(bundle):
    from . import lifecycle
    manifest=validate_bundle(bundle);file=safe_path(manifest['data_file'])
    intent,ready=markers(file)
    if intent.exists() or intent.is_symlink():raise ValueError('graph preparation is incomplete or active;inspect its intent record')
    if file.exists():
        inventory = lifecycle.container_ready(manifest)
        fs = os.statvfs(file.parent)
        return dict(inventory, required_free_bytes=0, available_bytes=fs.f_bavail * fs.f_frsize)
    if ready.exists() or ready.is_symlink():raise ValueError('graph preparation marker exists without its data file;inspect before retrying')
    _, mount = lifecycle._storage_target(file)
    parent=lifecycle._existing_parent(file.parent)
    if not parent.is_dir() or not os.access(parent,os.W_OK):raise ValueError('graph data parent is not writable')
    fs=os.statvfs(parent);available=fs.f_bavail*fs.f_frsize
    required=(manifest['capacity_bytes']*105+99)//100
    if available<required:raise ValueError('insufficient free space for graph preparation including5% headroom')
    layout=require_layout(parent,directory=True,stop=mount['target'],fstype=mount['fstype'])
    return dict(id=manifest['id'],data_file=str(file),data_root=str(file.parent),mount=mount,
                capacity_bytes=manifest['capacity_bytes'],required_free_bytes=required,available_bytes=available,
                state='missing-file',layout=layout['layout'],layout_verification=layout['verification'])


def prepare(bundle, *, output, execute=False, vdbench=None):
    if not execute:raise ValueError('graph preparation writes data;explicit --execute is required')
    from . import lifecycle
    bundle=Path(bundle).absolute();manifest=validate_bundle(bundle);report=preflight(bundle)
    if report['state']=='ready':return dict(report,reused_existing_data=True)
    vdbench=lifecycle._executable(vdbench or Path(manifest['runtime']['stock_home'])/'vdbench')
    from .user_runtime import execution
    execution(bundle, vdbench, bundle/'prepare_data.vdb', output)
    file=safe_path(manifest['data_file']);intent,_=markers(file)
    destination=lifecycle._output(output,file.parent,bundle);destination.mkdir()
    file.parent.mkdir(parents=True,exist_ok=True)
    require_layout(file.parent,directory=True,stop=report['mount']['target'],fstype=report['mount']['fstype'])
    with intent.open('x') as stream:
        stream.write(json_text(lifecycle._record(manifest, state='preparing', output=str(destination))))
        stream.flush();os.fsync(stream.fileno())
    # Persist the guard before any data can be created. A crash must not leave
    # an incomplete file looking like an externally prepared,complete input.
    sync_directory(file.parent)
    written=write_new_file(bundle, manifest, vdbench, destination, report['mount'])
    inventory=lifecycle.record_container_ready(manifest)
    intent.unlink()
    sync_directory(file.parent)
    result=dict(report,state='ready',written_bytes=written,output=str(destination),inventory=inventory)
    (destination/'prepare.json').write_text(json_text(result))
    return result
