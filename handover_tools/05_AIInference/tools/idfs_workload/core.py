"""Pure validation and deterministic emission for source-sized IDFS workloads."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from .user_engine import implementation

MAX_FWD = 256
BATCH_SIZE = 20
NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def safe_path(path):
    path = Path(path).absolute()
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)) or ".." in path.parts:
        raise ValueError(f"Vdbench path must be absolute and contain no parameter metacharacters: {path}")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f'symlink is not allowed in configuration/data path: {item}')
    return path


def _positive(value, label, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be positive and finite")
    if integer and not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f"invalid identifier: {value!r}")


def validate_model(model):
    engine = implementation(model)
    if model['id'] == 'graph_zipf':
        return engine.validate_model(model)
    _name(model['id'])
    if model.get('access_engine') != 'user':
        raise ValueError('this archive uses the User access engine')
    engine.validate_model(model)
    bins = model['bins']
    if not bins or len({b['id'] for b in bins}) != len(bins):
        raise ValueError('bins must be nonempty and unique')
    lookup = {b['id']: b for b in bins}
    for b in bins:
        _name(b['id'])
        _positive(b['files'], 'file count', True)
        _positive(b['size_bytes'], 'file size', True)
        if b['size_bytes'] % 4096:
            raise ValueError('file size must be 4 KiB aligned')
    if 'baseline' not in model['profiles']:
        raise ValueError('baseline profile required')
    used = set()
    for profile, phases in model['profiles'].items():
        _name(profile)
        if not phases or len({p['name'] for p in phases}) != len(phases):
            raise ValueError('profile phases must be nonempty and unique')
        for phase in phases:
            _name(phase['name'])
            _positive(phase['seconds'], 'elapsed', True)
            _positive(phase.get('rate_multiplier', 1), 'rate multiplier')
            lanes = phase['lanes']
            if ('phase_zipf' in model or 'static_native' in model) and len(lanes)>256:
                raise ValueError('business phase exceeds 256 active FWDs')
            if not 0 < len(lanes) <= MAX_FWD:
                raise ValueError('each RD must expand to 1..256 FWDs')
            concurrency = Counter()
            for lane in lanes:
                b = lookup.get(lane['bin'])
                if b is None:
                    raise ValueError('lane references unknown bin')
                used.add(b['id'])
                if lane.get('threads', 1) != 1:
                    raise ValueError('v2 fixed bin plan requires one thread per FWD')
                concurrency[b['id']] += 1
                _positive(lane['weight'], 'lane probability')
                if lane.get('operation', 'read') not in ('read', 'write'):
                    raise ValueError('measurement operation must be read or write')
                if lane['fileio'] not in ('random', 'sequential') or lane['fileselect'] not in ('random', 'sequential'):
                    raise ValueError('unsupported access selection')
                if not isinstance(lane.get('file_sharing', False), bool):
                    raise ValueError('file_sharing must be boolean')
                if lane.get('file_sharing') and lane['fileio'] != 'random':
                    raise ValueError('stock Vdbench file sharing requires random I/O')
                xfer = lane['xfersize']
                pairs = xfer if isinstance(xfer, list) else [[xfer, 1]]
                if not pairs:
                    raise ValueError('empty transfer histogram')
                for size, weight in pairs:
                    _positive(size, 'transfer bytes', True)
                    _positive(weight, 'transfer probability')
                    if size % 4096 or size > b['size_bytes']:
                        raise ValueError('transfer must be aligned and fit every file in its bin')
                if 'stopafter' in lane:
                    _positive(lane['stopafter'], 'stopafter', True)
            for key, count in concurrency.items():
                if count > lookup[key]['files']:
                    if not all(l.get('file_sharing') for l in lanes if l['bin']==key):
                        raise ValueError(f'bin {key} has fewer files than concurrent FWDs')
        if sum(p['seconds'] for p in phases) != 600:
            raise ValueError('each profile must total 600 seconds')
    if used != set(lookup):
        raise ValueError('unreferenced prepared bins')
    return sum(b['files'] * b['size_bytes'] for b in bins)


def _prepare_header(model, data_root):
    label = ('IDFS '+model.get('provenance',{}).get('trace_label','trace inventory')
             if model.get('capacity_policy', {}).get('kind') == 'trace_inventory' else 'IDFS 400 GiB')
    if model.get('capacity_policy', {}).get('kind') in ('parametric_inventory', 'aligned_container_inventory'):
        label = 'IDFS '+model['provenance']['workload_label']
    lines = [f"* {label} {model['id']}; synthetic workload with source read/write semantics",
             'data_errors=1', 'messagescan=no', 'create_anchors=yes',
             'fsd=default,depth=1,width=1,shared=yes,openflags=o_direct']
    for b in model['bins']:
        lines.append(f"fsd={b['id']},anchor={data_root / b['id']},files={b['files']},size={b['size_bytes']}")
    return lines


def config_texts(model, data_root, rate, *, runtime_root=None):
    engine = implementation(model)
    if model['id'] == 'graph_zipf':
        return engine.config_texts(model, data_root, rate, runtime_root)
    validate_model(model)
    if rate != 'max':
        _positive(rate, 'target mean IOPS')
    texts = {}
    lines = _prepare_header(model, data_root)
    lines.append('fwd=format,threads=1,xfersize=4m')
    for b in model['bins']:
        lines.append(f"fwd=prep_{b['id']},fsd={b['id']},operation=read,threads=1")
    for start in range(0, len(model['bins']), BATCH_SIZE):
        names = ','.join('prep_' + b['id'] for b in model['bins'][start:start+BATCH_SIZE])
        lines.append(f'rd=prepare_{start//BATCH_SIZE:03d},fwd=({names}),format=(restart,only),fwdrate=max')
    texts['prepare_data.vdb'] = '\n'.join(lines) + '\n'
    profiles = {}
    runtime = runtime_root if runtime_root is not None else Path(__file__).parents[1]/model['id']/'rendered'
    for profile, phases in model['profiles'].items():
        texts[f'run_{profile}.vdb'], profiles[profile] = engine.render_profile(
            model, data_root, phases, rate, runtime)
    return texts, profiles


def render(model, *, data_root, output, rate=100, data_file=None, clients=None, threads=64, stock_home=None):
    engine = implementation(model)
    if model['id'] == 'graph_zipf':
        return engine.render(model, data_root=data_root, output=output, rate=rate, data_file=data_file,
                             clients=clients, threads=threads, stock_home=stock_home)
    if data_file is not None or clients is not None or threads!=64 or stock_home is not None:
        raise ValueError('existing-file/client options apply only to the graph Zipf workload')
    data_root, output = safe_path(data_root), safe_path(output)
    if output == data_root or data_root in output.parents or output in data_root.parents:
        raise ValueError('configuration and data directories must be separate')
    capacity = validate_model(model)
    texts, profiles = config_texts(model, data_root, rate, runtime_root=output)
    layout = [{'id':b['id'], 'files':b['files'], 'size_bytes':b['size_bytes'], 'anchor':str(data_root/b['id'])} for b in model['bins']]
    manifest = {'schema':1, 'id':model['id'], 'data_root':str(data_root), 'capacity_bytes':capacity,
                'file_count':sum(b['files'] for b in layout), 'bins':layout, 'profiles':profiles,
                'max_run_fwd':max(p['fwd_count'] for phases in profiles.values() for p in phases),
                'max_prepare_fwd':min(BATCH_SIZE, len(layout)), 'rate':rate,
                'layout_sha256':digest(json_text(layout)), 'model_sha256':digest(json_text(model)),
                'provenance':model.get('provenance', {}),
                'config_sha256':{name:digest(text) for name,text in texts.items()}}
    manifest['access_engine'] = model['access_engine']
    manifest['max_run_wd'] = max(p['wd_count'] for phases in profiles.values() for p in phases)
    manifest['total_run_wd'] = max(sum(p['wd_count'] for p in phases) for phases in profiles.values())
    output.mkdir(parents=True, exist_ok=True)
    manifest['asset_sha256'] = engine.materialize(model, output)
    for name, text in {**texts, 'model.json':json_text(model), 'manifest.json':json_text(manifest)}.items():
        destination = safe_path(output/name)
        destination.write_text(text)
    validate_bundle(output)
    return manifest


def validate_bundle(output):
    """Verify all emitted bytes against the pure model, not only stored checksums."""
    output = Path(output)
    manifest = json.loads((output/'manifest.json').read_text())
    engine = implementation(manifest)
    if manifest['id'] == 'graph_zipf':
        return engine.validate_bundle(output)
    model = json.loads((output/'model.json').read_text())
    if model['id'] != manifest['id']:
        raise ValueError('model identity differs from manifest')
    if digest(json_text(model)) != manifest['model_sha256']:
        raise ValueError('model checksum differs from manifest')
    if validate_model(model) != manifest['capacity_bytes']:
        raise ValueError('capacity mismatch')
    data_root = safe_path(manifest['data_root'])
    layout = [{'id':b['id'], 'files':b['files'], 'size_bytes':b['size_bytes'], 'anchor':str(data_root/b['id'])} for b in model['bins']]
    if layout != manifest['bins'] or digest(json_text(layout)) != manifest['layout_sha256']:
        raise ValueError('layout mismatch')
    texts, profiles = config_texts(model, data_root, manifest['rate'], runtime_root=output)
    if profiles != manifest['profiles'] or set(texts) != set(manifest['config_sha256']):
        raise ValueError('profile/config inventory mismatch')
    if (manifest.get('access_engine') != model['access_engine']
            or manifest.get('max_run_fwd') != 0
            or manifest.get('max_run_wd') != max(p['wd_count'] for ps in profiles.values() for p in ps)
            or manifest.get('total_run_wd') != max(sum(p['wd_count'] for p in ps) for ps in profiles.values())):
        raise ValueError('User engine channel inventory mismatch')
    engine.validate_assets(model, output, manifest.get('asset_sha256'))
    from .validate_vdbench import validate_prepare_text
    for name, expected in texts.items():
        actual = (output/name).read_text()
        if actual != expected or digest(actual) != manifest['config_sha256'][name]:
            raise ValueError(f'generated configuration was changed: {name}')
        if name.startswith('prepare'):
            validate_prepare_text(actual)
        else:
            engine.validate_run_text(actual)
    return manifest
