"""Explicit preparation and readiness gates; never mount, clean, or repair data."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from datetime import datetime, timezone

from .core import RUN_CONFIG, json_text, safe_path, validate_bundle

READY = '.single-v2-ready.json'
INTENT = '.single-v2-preparing.json'


def no_symlinks(path):
    path = safe_path(path)
    for item in reversed((path, *path.parents)):
        if item.is_symlink():
            raise ValueError(f'symlink is not allowed in data path: {item}')
    return path


def _existing_parent(path):
    while not path.exists():
        if path == path.parent:
            raise ValueError('no existing ancestor')
        path = path.parent
    return path


def mount_info(path):
    """Read mount identity only; intentionally omit mount options/credentials."""
    path = Path(path).absolute()
    matches = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields, tail = left.split(), right.split()
        target = Path(re.sub(r'\\([0-7]{3})', lambda m:chr(int(m[1],8)), fields[4]))
        if path == target or target in path.parents:
            matches.append((len(target.parts), {'target':str(target), 'fstype':tail[0], 'source':tail[1]}))
    if not matches:
        raise ValueError(f'cannot determine mount: {path}')
    return max(matches, key=lambda pair:pair[0])[1]


def _storage_target(root):
    root = no_symlinks(root)
    info = mount_info(root)
    if info['fstype'] not in ('idfs', 'fuse.idfs', 'fuse.idfs-fuse', 'ceph', 'fuse.ceph', 'fuse.ceph-fuse','nfs','nfs4'):
        raise ValueError(f"target is not an IDFS/CephFS/NFS mount: {info['target']} ({info['fstype']})")
    if root == Path(info['target']):
        raise ValueError('data root must be a dedicated directory below the mount')
    return root, info


def preflight(bundle):
    manifest = validate_bundle(bundle)
    if manifest['id']=='bigdata_fast':
        from .fast_prepare import preflight as preflight_fast
        return preflight_fast(manifest)
    if manifest['id']=='graph_zipf':
        from .graph_prepare import preflight as graph_preflight
        return graph_preflight(bundle)
    root, mount = _storage_target(manifest['data_root'])
    if manifest.get('access_engine') == 'user' and (root/READY).is_file() and not (root/INTENT).exists():
        ready = check_ready(bundle)
        fs = os.statvfs(root)
        return dict(ready, data_root=str(root), required_free_bytes=0, available_bytes=fs.f_bavail*fs.f_frsize)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f'data root already contains content; no automatic overwrite or cleanup: {root}')
    parent = _existing_parent(root)
    fs = os.statvfs(parent)
    available = fs.f_bavail * fs.f_frsize
    required = (manifest['capacity_bytes'] * 105 + 99) // 100
    if available < required:
        raise ValueError(f'insufficient free space: {available} bytes available, {required} required including 5% headroom')
    if not os.access(parent, os.W_OK):
        raise ValueError(f'target parent is not writable: {parent}')
    if manifest.get('access_engine')=='user':
        from .file_layout import require_layout
        layout=require_layout(parent,directory=True,stop=mount['target'],fstype=mount['fstype'])
    return {'id':manifest['id'], 'data_root':str(root), 'mount':mount,
            'capacity_bytes':manifest['capacity_bytes'], 'required_free_bytes':required,
            'available_bytes':available, 'existing_empty_directory':root.exists(),
            'layout_verification':layout['verification'] if manifest.get('access_engine')=='user' else 'not checked'}


def verify_inventory(manifest):
    """Verify the file inventory from metadata."""
    if manifest.get('access_engine') == 'user':
        return _inspect_container(manifest)[0]
    root = no_symlinks(manifest['data_root'])
    expected = {b['id'] for b in manifest['bins']}
    if not root.is_dir():
        raise ValueError(f'missing data root: {root}')
    for child in root.iterdir():
        if child.name not in expected | {READY, INTENT}:
            raise ValueError(f'unexpected content in data root: {child}')
        if child.is_symlink():
            raise ValueError(f'symlink in data root: {child}')
    total_files = total_bytes = 0
    for b in manifest['bins']:
        anchor = no_symlinks(b['anchor'])
        if not anchor.is_dir():
            raise ValueError(f'missing bin anchor: {anchor}')
        count = 0
        expected_files = {f'vdb.1_1.dir/vdb_f{i:04d}.file' for i in range(b['files'])}
        actual_files = set()
        for directory, dirs, files in os.walk(anchor, followlinks=False):
            for name in dirs:
                p = Path(directory)/name
                if p.is_symlink() or p.relative_to(anchor).as_posix() != 'vdb.1_1.dir':
                    raise ValueError(f'unexpected or symlink directory: {p}')
            for name in files:
                p = Path(directory)/name
                st = p.lstat()
                if not stat.S_ISREG(st.st_mode):
                    raise ValueError(f'not a regular file: {p}')
                relative = p.relative_to(anchor).as_posix()
                if relative == 'vdb_control.file':
                    continue
                if relative not in expected_files:
                    raise ValueError(f'unexpected file in bin: {p}')
                if st.st_size != b['size_bytes']:
                    raise ValueError(f'wrong file size: {p}')
                if st.st_blocks * 512 < st.st_size:
                    raise ValueError(f'sparse or incompletely allocated data file: {p}')
                actual_files.add(relative)
                count += 1
                total_bytes += st.st_size
        if actual_files != expected_files:
            raise ValueError(f"bin {b['id']} file identity inventory differs from Vdbench layout")
        if count != b['files']:
            raise ValueError(f"bin {b['id']} has {count} data files; expected {b['files']}")
        total_files += count
    if total_bytes != manifest['capacity_bytes'] or total_files != manifest['file_count']:
        raise ValueError('total file inventory mismatch')
    return {'file_count':total_files, 'capacity_bytes':total_bytes, 'verification':'stat counts/sizes/allocated extents; no content read',
            'layout_verification':'not checked'}


def _inspect_container(manifest):
    from .file_layout import require_layout

    preparation = manifest['preparation']
    file, mount = _storage_target(preparation['file'])
    info = file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size != manifest['capacity_bytes']:
        raise ValueError(f'expected a regular container of {manifest["capacity_bytes"]} bytes: {file}')
    allocated = info.st_blocks * 512
    if allocated < preparation['written_bytes']:
        raise ValueError(f'incompletely allocated container: {file}')
    if not os.access(file, os.R_OK):
        raise ValueError(f'container is not readable: {file}')
    layout = require_layout(file, fstype=mount['fstype'])
    return dict(id=manifest['id'], state='ready', data_root=manifest['data_root'], data_file=str(file),
                mount=mount, file_count=1, capacity_bytes=info.st_size,
                payload_bytes=preparation['written_bytes'], allocated_bytes=allocated,
                layout=layout['layout'], layout_verification=layout['verification'],
                verification='file size, allocated bytes and layout metadata'), info


def _record(manifest, **extra):
    return {'id':manifest['id'], 'layout_sha256':manifest['layout_sha256'],
            'capacity_bytes':manifest['capacity_bytes'], 'utc':datetime.now(timezone.utc).isoformat(), **extra}


def container_markers(manifest):
    if manifest.get('input_policy', {}).get('mode') == 'existing_file':
        from .graph_prepare import markers
        return markers(manifest['preparation']['file'])
    root = Path(manifest['data_root'])
    return root/INTENT, root/READY


def record_container_ready(manifest):
    from .user_prepare import sync_directory

    _, marker = container_markers(manifest)
    marker = safe_path(marker)
    if marker.exists():
        raise FileExistsError(f'container already has a readiness record: {marker}')
    inventory, info = _inspect_container(manifest)
    pending = safe_path(marker.with_name(marker.name+'.tmp'))
    with pending.open('w') as stream:
        stream.write(json_text(_record(manifest, state='ready', inode=info.st_ino, inventory=inventory)))
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(marker)
    sync_directory(marker.parent)
    return inventory


def container_ready(manifest):
    intent, marker = container_markers(manifest)
    if intent.exists() or intent.is_symlink():
        raise ValueError('preparation is incomplete or active; test cannot start')
    marker = safe_path(marker)
    inode = None
    if marker.exists():
        ready = json.loads(marker.read_text())
        if (ready.get('state') != 'ready'
                or ready.get('id', manifest['id']) != manifest['id']
                or ready.get('layout_sha256') != manifest['layout_sha256']
                or ready.get('capacity_bytes') != manifest['capacity_bytes']):
            raise ValueError('prepared data belongs to a different layout')
        inode = ready.get('inode')
    elif manifest['id'] == 'bigdata_fast':
        from .fast_prepare import completed_inode
        inode = completed_inode(Path(manifest['data_root']))
    elif manifest.get('input_policy', {}).get('mode') != 'existing_file':
        raise ValueError('ready marker absent; preparation must finish successfully before measurement')
    inventory, info = _inspect_container(manifest)
    if inode is not None and inode != info.st_ino:
        raise ValueError(f'container identity differs from preparation record: {inventory["data_file"]}')
    return inventory


def _executable(vdbench):
    vdbench = Path(vdbench).absolute()
    if not vdbench.is_file() or not os.access(vdbench, os.X_OK):
        raise ValueError(f'Vdbench executable unavailable: {vdbench}')
    return vdbench


def validate_outputs(manifests, bundles, results):
    """Check every selected destination before any workload creates data/results."""
    results = safe_path(results)
    excluded = [safe_path(m['data_root']) for m in manifests] + [safe_path(b) for b in bundles]
    for manifest in manifests:
        output = safe_path(results/manifest['id'])
        if output.exists():
            raise ValueError(f'refuse existing output directory: {output}')
        for path in excluded:
            if output == path or path in output.parents or output in path.parents:
                raise ValueError(f'output overlaps selected data or configuration: {output}')
    return None


def _output(output, root, bundle):
    output = safe_path(output)
    if output.exists():
        raise ValueError(f'refuse existing output directory: {output}')
    for excluded in (root, Path(bundle).absolute()):
        if output == excluded or excluded in output.parents or output in excluded.parents:
            raise ValueError('results, data and configuration paths must be separate')
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def prepare(bundle, *, vdbench, output, execute=False):
    if not execute:
        raise ValueError('preparation writes data; explicit --execute is required')
    bundle = Path(bundle).absolute()
    manifest = validate_bundle(bundle)
    vdbench = _executable(vdbench)
    if manifest.get('access_engine') == 'user':
        from . import user_prepare, user_runtime
        # Check the runtime before creating any data, including the file extent.
        user_runtime.execution(bundle, vdbench, bundle/'prepare_data.vdb', output)
    if manifest['id']=='bigdata_fast':
        from .fast_prepare import prepare as prepare_fast
        output = _output(output, Path(manifest['data_root']), bundle)
        output.mkdir(exist_ok=False)
        report = dict(prepare_fast(manifest, bundle=bundle, vdbench=vdbench, output=output), output=str(output))
        (output/'prepare.json').write_text(json_text(report))
        return report
    if manifest['id']=='graph_zipf':
        from .graph_prepare import prepare as prepare_graph
        return prepare_graph(bundle,output=output,execute=execute,vdbench=vdbench)
    report = preflight(bundle)
    if report.get('state') == 'ready':
        return dict(report, reused_existing_data=True)
    root = Path(manifest['data_root'])
    output = _output(output, root, bundle)
    root.mkdir(parents=True, exist_ok=True)
    # Exclusive intent file prevents duplicate preparations; leftovers require inspection.
    with (root/INTENT).open('x') as stream:
        stream.write(json_text(_record(manifest, state='preparing', output=str(output))))
        stream.flush()
        os.fsync(stream.fileno())
    if manifest.get('access_engine') == 'user':
        output.mkdir()
        user_prepare.sync_directory(root)
        file = safe_path(manifest['preparation']['file'])
        file.parent.mkdir(parents=True)
        user_prepare.write_new_file(bundle, manifest, vdbench, output, report['mount'])
        user_prepare.sync_directory(file.parent)
        user_prepare.sync_directory(file.parent.parent)
        inventory = record_container_ready(manifest)
    else:
        subprocess.run([str(vdbench), '-f', str(bundle/'prepare_data.vdb'), '-o', str(output)], check=True)
        inventory = verify_inventory(manifest)
        with (root/READY).open('x') as stream:
            stream.write(json_text(_record(manifest, state='ready', inventory=inventory)))
            stream.flush()
            os.fsync(stream.fileno())
    (root/INTENT).unlink()
    result = {**report, **inventory, 'state':'ready', 'output':str(output)}
    if manifest.get('access_engine') == 'user':
        user_prepare.sync_directory(root)
        (output/'prepare.json').write_text(json_text(result))
    return result


def check_ready(bundle):
    manifest = validate_bundle(bundle)
    if manifest.get('access_engine') == 'user':
        return container_ready(manifest)
    root, mount = _storage_target(manifest['data_root'])
    if (root/INTENT).exists() or (root/INTENT).is_symlink():
        raise ValueError('preparation is incomplete or active; test cannot start')
    marker = root/READY
    if not marker.is_file() or marker.is_symlink():
        raise ValueError('ready marker absent; preparation must finish successfully before measurement')
    ready = json.loads(marker.read_text())
    if ready.get('state') != 'ready' or ready.get('layout_sha256') != manifest['layout_sha256'] or ready.get('capacity_bytes') != manifest['capacity_bytes']:
        raise ValueError('prepared data belongs to a different layout')
    return {**verify_inventory(manifest), 'id':manifest['id'], 'mount':mount, 'state':'ready'}


def run(bundle, *, vdbench, output, execute=False):
    if not execute:
        raise ValueError('measurement requires explicit --execute')
    bundle = Path(bundle).absolute()
    manifest = validate_bundle(bundle)
    if manifest['id']=='bigdata_fast':
        from .fast_prepare import dataset_lock
        from .user_runtime import execution
        with dataset_lock(Path(manifest['data_root']), create=False) as lock:
            check_ready(bundle)
            stock = _executable(vdbench)
            output = _output(output, Path(manifest['data_root']), bundle)
            subprocess.run(**execution(bundle, stock, bundle/RUN_CONFIG, output), check=True, pass_fds=(lock,))
        return {'id':manifest['id'], 'output':str(output)}
    check_ready(bundle)
    vdbench = _executable(vdbench)
    root = Path(manifest['data_root'])
    output = _output(output, root, bundle)
    config = bundle/RUN_CONFIG
    if manifest.get('access_engine')=='user':
        from .user_runtime import execution
        subprocess.run(**execution(bundle, vdbench, config, output), check=True)
    else:
        subprocess.run([str(vdbench), '-f', str(config), '-o', str(output)], check=True)
    return {'id':manifest['id'], 'output':str(output)}
