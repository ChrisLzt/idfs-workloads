"""NCAR-derived four-activity WRF synthetic I/O with analytic object Zipf0.99."""
from copy import deepcopy
import csv
import hashlib
import io
import json
import os
from pathlib import Path

from .user_runtime import JAR_SHA256, execution

PACKAGE = Path(__file__).parent
SOURCE = PACKAGE/'sources'/'wrf_conus1km60.json'
JAVA = PACKAGE/'wrf_user_java'
EXTENSION_JAR = 'wrf-user.jar'
GRAIN = 4194304
MAX_TRANSFER = 1048576
SEED = 1048596
MEMBERS = 8
SHARED_ROLES = ('lookup_qs', 'lookup_freeze')


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _source():
    payload = SOURCE.read_bytes()
    pin = json.loads((PACKAGE/'source_pin.json').read_text())['model_files']['wrf']
    if _sha(payload) != pin['sha256']:
        raise ValueError('WRF source summary checksum mismatch')
    source = json.loads(payload)
    if (source['schema'] != 1 or source['history_hours'] != [0, 6]
            or source['checkpoint_hours'] != [5]):
        raise ValueError('WRF requires the approved two-history source summary')
    return source, pin['sha256']


def build():
    source, source_sha = _source()
    files = []; capacity = 0
    def add_file(original, member):
        nonlocal capacity
        f = deepcopy(original)
        f['role'] = original['id']; f['member'] = member
        if member:
            f['id'] = f'member_{member:02d}_{original["id"]}'
            # Two histories retain matching relative ranks within each member.
            f['rank_seed'] += (member - 1) * 1000
        f['extent_bytes'] = (f['template_bytes']+4095)//4096*4096
        f['slot_bytes'] = (f['extent_bytes']+GRAIN-1)//GRAIN*GRAIN
        f['base_bytes'] = capacity
        capacity += f['slot_bytes']; files.append(f)

    for original in source['logical_files']:
        if original['id'] in SHARED_ROLES:
            add_file(original, 0)
    for member in range(1, MEMBERS + 1):
        for original in source['logical_files']:
            if original['id'] not in SHARED_ROLES:
                add_file(original, member)

    def channels(*roles):
        return [dict(file=f['id'], operation=operation,
                     weight=f['extent_bytes'] * (MEMBERS if f['member'] == 0 else 1))
                for role, operation in roles for f in files if f['role'] == role]

    phases = [dict(name='startup', start_s=0, end_s=60,
                   channels=channels(('input', 'read'), ('lookup_qs', 'read'), ('lookup_freeze', 'read'))),
              dict(name='history_00h', start_s=60, end_s=150,
                   channels=channels(('history_00h', 'write'), ('boundary', 'read'))),
              dict(name='checkpoint_05h', start_s=150, end_s=510,
                   channels=channels(('checkpoint_05h', 'write'), ('boundary', 'read'))),
              dict(name='history_06h', start_s=510, end_s=600,
                   channels=channels(('history_06h', 'write')))]
    # Nominal exposure shares are for preview only; actual max-rate IOPS vary by activity.
    read_exposure = 0.
    for phase in phases:
        total = sum(c['weight'] for c in phase['channels'])
        read_exposure += (phase['end_s']-phase['start_s'])*sum(
            c['weight'] for c in phase['channels'] if c['operation']=='read')/total
    lanes = [dict(bin='container', operation=op, weight=weight, xfersize=MAX_TRANSFER,
                  fileio='random', fileselect='random', threads=1, file_sharing=True)
             for op, weight in [('read', read_exposure), ('write', 600-read_exposure)]]
    runtime_phase = dict(name='continuous', seconds=600, lanes=lanes)
    return dict(id='hpc_wrf', access_engine='user',
        bins=[dict(id='container', group='wrf', files=1, size_bytes=capacity)],
        profiles={'current': [deepcopy(runtime_phase)], 'baseline': [deepcopy(runtime_phase)]},
        logical_files=files, business_phases=phases,
        ensemble=dict(members=MEMBERS, shared_readonly_roles=list(SHARED_ROLES),
                      member_selection='uniform within each activity; shared startup reads aggregate all members'),
        capacity_policy=dict(kind='parametric_inventory', expected_bytes=capacity,
                             min_bytes=capacity, max_bytes=capacity),
        user_timeline=dict(duration_us=600000000, slice_us=1000000, grain_bytes=GRAIN,
                           alpha=.99, max_transfer_bytes=MAX_TRANSFER, threads=64,
                           seed=SEED, request_quota=None),
        provenance=dict(workload_label='WRF CONUS-derived1km-e_vert60 eight-member synthetic Zipf',
            source=source['website'], archive_url=source['archive_url'],
            archive_sha256=source['archive_sha256'], source_summary_sha256=source_sha,
            source_metadata_sha256=source['metadata_sha256'], grid=source['grid'],
            spacing_m=source['spacing_m'], source_grid=source['source_grid'],
            source_spacing_m=source['source_spacing_m'], grid_refinement=source['refinement'],
            simulated_hours=6, history_hours=[0, 6], checkpoint_hours=[5], native_trace=False,
            adaptation='NCAR WRF role/order-based synthetic I/O; eight member-specific states/outputs and shared read-only lookup tables; four timed activities; added all-object Zipf0.99; no original event replay',
            capacity_basis=source['capacity_scope']+'; eight member-specific scientific inventories and one shared lookup-table pair',
            template_bytes=sum(f['template_bytes'] for f in files),
            aligned_extent_bytes=sum(f['extent_bytes'] for f in files),
            container_padding_bytes=sum(f['slot_bytes']-f['extent_bytes'] for f in files),
            object_mapping='each logical file starts at4MiB and owns whole exclusive objects; runtime does not access slot padding',
            zipf_scope='fixed seeded permutation of all object ranks within each logical file; history0/6 use the same relative rank permutation',
            channel_weights='equal member scientific-role weights; shared table startup weights aggregate eight members; representative aligned file extents define request probabilities, not byte quotas',
            transfer_rule='synthetic1MiB requests inside a selected object; final sub-block shortened to actual4KiB-aligned extent; not measured native WRF sizes',
            boundary_rule='horizontally refined uncompressed boundary-field template read in0h-output and checkpoint activities; boundary updates coarsened into mixed phases',
            timeline_rule='memory-computation gaps compressed; 60/90/360/90s storage budgets; same activity throughout each1s slice; no quota or per-second rank reset',
            profiles='current and baseline are compatibility names for the same approved Zipf plan',
            limitation='repeated Zipf writes are a controlled adaptation, not complete scientific snapshot generation; coverage/counts/whole-run mix depend on actual performance',
            source_limitations=source['limitations']))


def validate_model(model):
    if model != build():
        raise ValueError('WRF User model differs from the approved four-activity Zipf plan')


def reverse_address(model, address):
    if type(address) is not int or address < 0:
        raise ValueError('invalid container address')
    for f in model['logical_files']:
        local = address-f['base_bytes']
        if 0 <= local < f['slot_bytes']:
            if local >= f['extent_bytes']:
                raise ValueError('address is in logical-file slot padding')
            return f['id'], local
    raise ValueError('address exceeds WRF container')


def timeline_text(model):
    lines = [f'WRF_ZIPF1\t{model["bins"][0]["size_bytes"]}\t{GRAIN}\t600000000\t1000000\t{MAX_TRANSFER}\t0.99\t4']
    lines.extend(f'F\t{f["id"]}\t{f["base_bytes"]}\t{f["extent_bytes"]}\t{f["rank_seed"]}'
                 for f in model['logical_files'])
    for p in model['business_phases']:
        lines.append(f'P\t{p["name"]}\t{p["start_s"]*1000000}\t{p["end_s"]*1000000}')
        lines.extend(f'C\t{c["file"]}\t{c["operation"]}\t{c["weight"]}' for c in p['channels'])
    return '\n'.join(lines)+'\n'


def file_map_text(model):
    output = io.StringIO(newline='')
    columns = ['id', 'member', 'role', 'source_name', 'template_bytes', 'extent_bytes', 'base_bytes', 'slot_bytes', 'rank_seed', 'size_basis']
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator='\n')
    writer.writeheader(); writer.writerows(model['logical_files'])
    return output.getvalue()


def _runtime():
    meta = json.loads((JAVA/'runtime.json').read_text())
    if meta['stock_jar_sha256'] != JAR_SHA256 or meta['java_class_version'] != 52:
        raise ValueError('WRF extension must target stock Vdbench5.04.06 and Java8')
    for name, expected in meta['source_sha256'].items():
        if _sha((JAVA/name).read_bytes()) != expected:
            raise ValueError('WRF Java source differs from built extension')
    if _sha((JAVA/EXTENSION_JAR).read_bytes()) != meta['jar_sha256']:
        raise ValueError('WRF extension JAR checksum mismatch')
    return meta


def _assets(model):
    validate_model(model); _runtime()
    return {EXTENSION_JAR: (JAVA/EXTENSION_JAR).read_bytes(),
            'vdbench': (JAVA/'vdbench').read_bytes(),
            'wrf.timeline': timeline_text(model).encode(),
            'file-map.csv': file_map_text(model).encode()}


def materialize(model, output):
    output = Path(output); assets = _assets(model)
    for name in assets:
        path = output/name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError('WRF asset path must not be a symlink')
    for name, payload in assets.items():
        (output/name).write_bytes(payload)
    (output/'vdbench').chmod(0o755)
    return {name: _sha(payload) for name, payload in assets.items()}


def validate_assets(model, bundle, recorded):
    expected = {name: _sha(payload) for name, payload in _assets(model).items()}
    if recorded != expected:
        raise ValueError('WRF asset inventory differs from the approved model')
    for name, digest in expected.items():
        path = Path(bundle)/name
        if (path.is_symlink() or any(p.is_symlink() for p in path.parents)
                or not path.is_file() or _sha(path.read_bytes()) != digest):
            raise ValueError('WRF asset missing, linked or modified: '+name)
    if not os.access(Path(bundle)/'vdbench', os.X_OK):
        raise ValueError('WRF launcher must be executable')


def _wd_line():
    return (f'wd=synthetic,sd=wrf,xfersize={MAX_TRANSFER},seekpct=100,rdpct=50,skew=100,'
            f'user=(idfswrf.TimelineWorkload,wrf.timeline,seed{SEED})')


def render_profile(model, data_root, phases, rate, runtime_root):
    validate_model(model)
    if rate != 'max':
        raise ValueError('WRF User requires iorate=max without per-second quotas')
    size = model['bins'][0]['size_bytes']
    data_file = Path(data_root)/'container'/'vdb.1_1.dir'/'vdb_f0000.file'
    lines = ['* NCAR-derived WRF synthetic four-activity object Zipf0.99; stock5.04.06 User',
             'data_errors=1', 'messagescan=no', 'formatsds=no',
             f'hd=default,jvms=1,vdbench={Path(runtime_root).absolute()}',
             f'sd=wrf,lun={data_file},size={size},threads=64,openflags=o_direct,offset=0,align=4096',
             _wd_line(), 'rd=continuous,wd=synthetic*,iorate=max,elapsed=600,interval=1,warmup=0']
    business = []
    for p in model['business_phases']:
        total = sum(c['weight'] for c in p['channels'])
        read = sum(c['weight'] for c in p['channels'] if c['operation']=='read')/total
        business.append(dict(name=p['name'], start_s=p['start_s'], end_s=p['end_s'],
                             seconds=p['end_s']-p['start_s'], read_share=read, write_share=1-read,
                             channels=deepcopy(p['channels'])))
    read = phases[0]['lanes'][0]['weight']/600
    stats = [dict(name='continuous', seconds=600, fwd_count=0, wd_count=1, target_iops='max',
                  read_share=read, write_share=1-read, share_basis='time-weighted nominal; not a guarantee of measured totals',
                  business_phases=business)]
    return '\n'.join(lines)+'\n', stats


def validate_run_text(text):
    lines = [l for l in text.splitlines() if l and not l.startswith('*')]
    if len(lines)!=7 or any(sum(l.startswith(p) for l in lines)!=1 for p in ('hd=', 'sd=', 'wd=', 'rd=')):
        raise ValueError('WRF User requires one host, SD, WD and RD')
    if (next(l for l in lines if l.startswith('wd=')) != _wd_line()
            or next(l for l in lines if l.startswith('rd=')) != 'rd=continuous,wd=synthetic*,iorate=max,elapsed=600,interval=1,warmup=0'
            or 'threads=64,openflags=o_direct,offset=0,align=4096' not in next(l for l in lines if l.startswith('sd='))):
        raise ValueError('WRF runtime differs from the approved max-rate User plan')
