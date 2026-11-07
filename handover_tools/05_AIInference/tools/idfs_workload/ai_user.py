"""SES-derived file and request-size mixes with pure random object/offset I/O."""
from collections import defaultdict
from copy import deepcopy
import csv
import hashlib
import io
import json
import os
from pathlib import Path

from .user_runtime import JAR_SHA256, execution

PACKAGE = Path(__file__).parent
JAVA = PACKAGE/'ai_user_java'
EXTENSION_JAR = 'ai-user.jar'
GRAIN = 4194304
MAX_CONTAINER_BYTES = 6 * 2**40
SEED = 1048596
CASES = {'ai_training': 'ai_training_ses', 'ai_inference': 'ai_inference_ses'}


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def build(case, target_bytes):
    from .models import AI_TARGET_BYTES, _capacity, _counts, _phases, load_source
    if case not in CASES:
        raise ValueError('unsupported SES User workload')
    if type(target_bytes) is not int or target_bytes <= 0:
        raise ValueError('target_bytes must be a positive integer')
    source = load_source(case)
    original = _capacity(source)
    if target_bytes <= original:
        raise ValueError('expansion target must exceed source capacity')
    inventory = {}
    inference = case == 'ai_inference'
    for b in source['bins']:
        size = b.get('logical_size_bytes', b['size_bytes'])
        group = inventory.setdefault(b['group'], dict(count=0, size=size))
        if group['size'] != size:
            raise ValueError('mixed sizes in SES source class')
        group['count'] += 1 if inference else b['files']
    names = sorted(inventory)
    original_counts = [inventory[g]['count'] for g in names]
    sizes = [inventory[g]['size'] for g in names]
    count_scale = 11 if case == 'ai_training' and target_bytes == AI_TARGET_BYTES[case] else 1
    if count_scale == 11:
        # Preserve the approved rounded 400GiB reference inventory exactly.
        # Re-rounding the source at 4400GiB would change its class counts.
        counts = [count * count_scale for count in _counts(original_counts, sizes, 400 * 2**30)]
    else:
        counts = _counts(original_counts, sizes, target_bytes)
    groups = []; capacity = payload = 0
    for name, count in zip(names, counts):
        size = inventory[name]['size']; fragments = 4 if inference else 1
        extent = size // fragments
        slot = (extent + GRAIN - 1) // GRAIN * GRAIN
        groups.append(dict(id=name, logical_files=count, logical_size_bytes=size,
                           fragments_per_logical_file=fragments, extent_bytes=extent,
                           slot_bytes=slot, base_bytes=capacity,
                           object_rank_seed=SEED ^ size))
        capacity += count * fragments * slot; payload += count * size
    if capacity > MAX_CONTAINER_BYTES:
        raise ValueError('SES aligned container exceeds the 6TiB User capacity limit')
    if not .98 * target_bytes <= payload <= 1.02 * target_bytes:
        raise ValueError('SES valid payload outside 2% target tolerance')
    source_phases = _phases(source, case)
    if len(source_phases) != 1 or source_phases[0]['seconds'] != 600:
        raise ValueError('SES User requires the approved single storage-access phase')
    channels = []
    for group in groups:
        data = source_phases[0]['groups'][group['id']]
        joint = defaultdict(float)
        for (operation, _, _), c in sorted(data['channels'].items()):
            for size, share in sorted(c['hist'].items()):
                joint[operation, size] += data['weight'] * c['share'] * share
        for (operation, size), weight in sorted(joint.items()):
            channels.append(dict(group=group['id'], operation=operation,
                                 fileio='random', xfersize=size, weight=weight))
    maximum = max(c['xfersize'] for c in channels)
    totals = defaultdict(float)
    for c in channels:
        if groups[names.index(c['group'])]['extent_bytes'] % c['xfersize']:
            raise ValueError('fixed SES transfer must divide its original file range')
        totals[c['operation']] += c['weight']
    # These lanes describe buffer/operation provisioning for the User queue.
    # ses_channels, not FWD file selectors, define the actual access patterns.
    lanes = [dict(bin='container', operation=op, weight=totals[op],
                  xfersize=maximum, fileio='random', fileselect='random',
                  threads=1, file_sharing=True) for op in ('read', 'write')]
    profiles = {p: [dict(name='continuous', seconds=600, user_profile=p,
                         lanes=deepcopy(lanes))] for p in ('current', 'baseline')}
    pin = json.loads((PACKAGE/'source_pin.json').read_text())
    provenance = deepcopy(source['provenance'])
    provenance.pop('current_suite', None)
    provenance.update(workload_label='CCF-SES '+('PERF_003 inference' if inference else 'PERF_002 training'),
        source='https://ses.ccf.org.cn/', native_trace=False, source_write_to_read=False,
        adaptation='SES1.2.0-derived logical inventory and read/write/transfer-size shares; all source access modes converted to pure random; added conditional object Zipf0.99 with IID per-I/O object and legal-offset selection; not SES certification',
        operation_mapping='preserve source reads and writes; overwrite prepared synthetic payload',
        object_mapping='4MiB-aligned exclusive original-file/fragment slots; reversible mapping; runtime excludes padding',
        preparation='full-container formatting allocates padding too; partial original-file objects become full-sized slots',
        access_mode_mapping='convert every SES sequential/random channel to random reads or writes; merge identical group/operation/transfer-size channels while preserving their summed probability',
        random_rule='uniform legal4KiB-aligned offsets inside the selected object; no object or logical-file boundary crossing',
        object_selection=f'Zipf0.99 over all4MiB objects within each original size class; baseline uniform objects; fixed Fisher-Yates ranks seeded by{SEED} XOR logical_size_bytes',
        sampling='per-channel IID pseudorandom uniforms through the object CDF; with replacement, repeated objects allowed; domain-separated run-seed streams distinct from request/offset RNG; no request or time quota',
        timing='one600sRD;1s internal time slices with stationary SES-derived probabilities; no reseeding at slice boundaries; no per-second quota',
        limitation='pure random adaptation does not retain the SES sequential/random mix; container changes inode/directory/open metadata; conditional class mixture is not one global Zipf; finite sampling and coverage still depend on I/O count; no native AI event replay',
        inventory_objects=capacity // GRAIN,
        idfs_expansion=dict(source_commit=pin['commit'], source_model_sha256=pin['model_files'][case]['sha256'],
            original_bytes=original, target_bytes=target_bytes, actual_bytes=payload,
            container_bytes=capacity,
            reference_target_bytes=400 * 2**30 if count_scale == 11 else None,
            reference_count_multiplier=count_scale,
            method='increase logical file counts, retain size classes and SES read/write/size shares; use pure random offsets in aligned independent object slots',
            runtime='unmodified Vdbench5.04.06 plus separate User extension; no SES installation'))
    return dict(id=CASES[case], access_engine='user', bins=[dict(id='container', group='ses', files=1, size_bytes=capacity)],
        profiles=profiles, logical_groups=groups, ses_channels=channels,
        valid_payload_bytes=payload, container_padding_bytes=capacity-payload,
        capacity_policy=dict(kind='aligned_container_inventory', expected_bytes=capacity,
                             min_bytes=capacity, max_bytes=capacity),
        user_timeline=dict(duration_us=600000000, slice_us=1000000, grain_bytes=GRAIN,
                           alpha=.99, max_transfer_bytes=maximum, threads=64, seed=SEED,
                           request_quota=None, zipf_scope='objects_within_size_class',
                           ios_per_object_selection=1, object_selection='iid_inverse_cdf',
                           offset_selection='iid_uniform_4k_aligned',
                           baseline_object_selection='uniform'),
        provenance=provenance)


def validate_model(model):
    cases = {value: key for key, value in CASES.items()}
    case = cases.get(model.get('id'))
    if case is None or model != build(case, model['provenance']['idfs_expansion']['target_bytes']):
        raise ValueError('SES User model differs from the approved inventory, request mix or object Zipf plan')


def reverse_address(model, address):
    if type(address) is not int or address < 0:
        raise ValueError('invalid container address')
    for g in model['logical_groups']:
        local = address - g['base_bytes']
        fragments = g['fragments_per_logical_file']
        if 0 <= local < g['slot_bytes'] * g['logical_files'] * fragments:
            physical, offset = divmod(local, g['slot_bytes'])
            if offset >= g['extent_bytes']:
                raise ValueError('address is in logical-file slot padding')
            logical, fragment = divmod(physical, fragments)
            return g['id'], logical, fragment, offset
    raise ValueError('address exceeds SES container')


def file_map_text(model):
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['group', 'logical_file', 'fragment', 'base_bytes', 'extent_bytes', 'slot_bytes'])
    for g in model['logical_groups']:
        count = g['logical_files']; fragments = g['fragments_per_logical_file']
        for logical in range(count):
            for fragment in range(fragments):
                base = g['base_bytes'] + (logical * fragments + fragment) * g['slot_bytes']
                writer.writerow([g['id'], logical, fragment, base, g['extent_bytes'], g['slot_bytes']])
    return output.getvalue()


def timeline_text(model):
    case = next(c for c, name in CASES.items() if name == model['id'])
    lines = [f'AI_SES_OBJECT3\t{case}\t{model["bins"][0]["size_bytes"]}\t{GRAIN}\t600000000\t1000000\t{model["user_timeline"]["max_transfer_bytes"]}\t0.99\t1\tiid']
    lines.extend(f'G\t{g["id"]}\t{g["logical_files"]}\t{g["logical_size_bytes"]}\t{g["fragments_per_logical_file"]}\t{g["base_bytes"]}\t{g["slot_bytes"]}\t{g["object_rank_seed"]}'
                 for g in model['logical_groups'])
    lines.extend(f'C\t{c["group"]}\t{c["operation"]}\t{c["fileio"]}\t{c["xfersize"]}\t{c["weight"]:.17g}' for c in model['ses_channels'])
    return '\n'.join(lines)+'\n'


def _runtime():
    meta = json.loads((JAVA/'runtime.json').read_text())
    if meta['stock_jar_sha256'] != JAR_SHA256 or meta['java_class_version'] != 52:
        raise ValueError('SES extension must target stock Vdbench5.04.06 and Java8')
    for name, expected in meta['source_sha256'].items():
        if _sha((JAVA/name).read_bytes()) != expected:
            raise ValueError('SES Java source differs from built extension')
    if _sha((JAVA/EXTENSION_JAR).read_bytes()) != meta['jar_sha256']:
        raise ValueError('SES extension JAR checksum mismatch')
    return meta


def _assets(model):
    validate_model(model); _runtime()
    return {EXTENSION_JAR: (JAVA/EXTENSION_JAR).read_bytes(),
            'vdbench': (JAVA/'vdbench').read_bytes(),
            'ai.timeline': timeline_text(model).encode(),
            'file-map.csv': file_map_text(model).encode()}


def materialize(model, output):
    output = Path(output); assets = _assets(model)
    for name in assets:
        path = output/name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError('SES asset path must not be a symlink')
    for name, payload in assets.items():
        (output/name).write_bytes(payload)
    (output/'vdbench').chmod(0o755)
    return {name: _sha(payload) for name, payload in assets.items()}


def validate_assets(model, bundle, recorded):
    expected = {name: _sha(payload) for name, payload in _assets(model).items()}
    if recorded != expected:
        raise ValueError('SES asset inventory differs from the approved model')
    for name, digest in expected.items():
        path = Path(bundle)/name
        if (path.is_symlink() or any(p.is_symlink() for p in path.parents)
                or not path.is_file() or _sha(path.read_bytes()) != digest):
            raise ValueError('SES asset missing, linked or modified: '+name)
    if not os.access(Path(bundle)/'vdbench', os.X_OK):
        raise ValueError('SES launcher must be executable')


def _wd_line(profile, maximum):
    return (f'wd=ses,sd=ai,xfersize={maximum},seekpct=100,rdpct=50,skew=100,'
            f'user=(idfsai.TimelineWorkload,ai.timeline,{profile},seed{SEED})')


def render_profile(model, data_root, phases, rate, runtime_root):
    validate_model(model)
    if rate != 'max':
        raise ValueError('SES User requires iorate=max without per-second quotas')
    profile = phases[0]['user_profile']
    if phases != model['profiles'][profile]:
        raise ValueError('SES User profile mismatch')
    size = model['bins'][0]['size_bytes']; maximum = model['user_timeline']['max_transfer_bytes']
    data_file = Path(data_root)/'container'/'vdb.1_1.dir'/'vdb_f0000.file'
    lines = ['* SES1.2.0-derived pure random I/O; aligned physical container; stock5.04.06 User',
             'data_errors=1', 'messagescan=no', 'formatsds=no',
             f'hd=default,jvms=1,vdbench={Path(runtime_root).absolute()}',
             f'sd=ai,lun={data_file},size={size},threads=64,openflags=o_direct,offset=0,align=4096',
             _wd_line(profile, maximum), 'rd=continuous,wd=ses*,iorate=max,elapsed=600,interval=1,warmup=0']
    total = sum(c['weight'] for c in model['ses_channels'])
    read = sum(c['weight'] for c in model['ses_channels'] if c['operation'] == 'read') / total
    stats = [dict(name='continuous', seconds=600, fwd_count=0, wd_count=1,
                  target_iops='max', read_share=read, write_share=1-read,
                  object_selection='conditional_Zipf0.99' if profile == 'current' else 'conditional_uniform',
                  ios_per_object_selection=1, object_sampling='iid_with_replacement',
                  share_basis='request sampling probabilities, not completion quotas')]
    return '\n'.join(lines)+'\n', stats


def validate_run_text(text):
    lines = [l for l in text.splitlines() if l and not l.startswith('*')]
    if len(lines) != 7 or any(sum(l.startswith(p) for l in lines) != 1 for p in ('hd=', 'sd=', 'wd=', 'rd=')):
        raise ValueError('SES User requires one host, ordinary SD, WD and RD')
    choices = {_wd_line(profile, maximum) for profile in ('current', 'baseline') for maximum in (1048576, 131072)}
    if (next(l for l in lines if l.startswith('wd=')) not in choices
            or next(l for l in lines if l.startswith('rd=')) != 'rd=continuous,wd=ses*,iorate=max,elapsed=600,interval=1,warmup=0'
            or 'threads=64,openflags=o_direct,offset=0,align=4096' not in next(l for l in lines if l.startswith('sd='))):
        raise ValueError('SES runtime differs from the max-rate single-container User plan')
