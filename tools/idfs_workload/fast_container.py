"""Render and run the FAST small-file workload on one shared container."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .core import RETIRED_CONFIG, RUN_CONFIG, digest, json_text, retire_baseline, safe_path
from . import user_prepare, user_runtime

OBJECT_BYTES = 4 * 1024**2
OBJECTS = 1_048_576
CAPACITY_BYTES = OBJECTS * OBJECT_BYTES
FILE_NAME = 'fast.bin'
PAYLOAD_BYTES = 400 * 1024
TRANSFER_BYTES = 128 * 1024
SEED = 1048596
WORKLOAD_ID = 'bigdata_fast'

EXTENSION = 'fast-user.jar'
PACKAGE = Path(__file__).parent
JAVA = PACKAGE/'fast_user_java'
BUNDLE_FILES = {EXTENSION, RUN_CONFIG, user_prepare.CONFIG, 'model.json', 'manifest.json',
                'runtime.json', 'stock-home', 'vdbench'}


def separate_paths(first, second):
    if first == second or first in second.parents or second in first.parents:
        raise ValueError(f'FAST requires separate directory trees: {first} and {second}')


def specification(data_root, stock_home, clients, threads, ssh_user=None):
    settings = user_runtime.client_settings(clients, threads, stock_home, ssh_user=ssh_user)
    return dict(settings, schema=2, id=WORKLOAD_ID, data_root=str(safe_path(data_root)),
                seed=SEED, capacity_bytes=CAPACITY_BYTES,
                effective_bytes=OBJECTS*PAYLOAD_BYTES, object_count=OBJECTS, object_bytes=OBJECT_BYTES,
                files=[dict(name=FILE_NAME, bytes=CAPACITY_BYTES)],
                elapsed=600, iorate='max', read_share=.90, zipf_alpha=.99, transfer_bytes=TRANSFER_BYTES,
                payload_bytes=PAYLOAD_BYTES, payload_offset=TRANSFER_BYTES)


def build():
    lane = dict(bin='container', fileio='random', fileselect='random', threads=1,
                file_sharing=True, xfersize=TRANSFER_BYTES)
    profiles = {'current':[dict(name='fast_analysis', seconds=600,
                                lanes=[dict(lane, operation='read', weight=9),
                                       dict(lane, operation='write', weight=1)])]}
    return dict(id=WORKLOAD_ID, access_engine='user', profiles=profiles,
                bins=[dict(id='container', group='fast', files=1, size_bytes=CAPACITY_BYTES)],
                capacity_policy=dict(kind='sparse_container_inventory', expected_bytes=CAPACITY_BYTES,
                                     min_bytes=CAPACITY_BYTES, max_bytes=CAPACITY_BYTES),
                container_mapping=dict(file=FILE_NAME, object_count=OBJECTS, object_bytes=OBJECT_BYTES,
                                       payload_bytes=PAYLOAD_BYTES, payload_offset=TRANSFER_BYTES,
                                       effective_bytes=OBJECTS*PAYLOAD_BYTES),
                user_zipf=dict(alpha=.99, mapping_seed=SEED, request_quota=None),
                provenance=dict(workload_label='FAST small-file analysis',
                                source_semantics='400 KiB logical files, 128 KiB random I/O, 90% reads',
                                preparation='sparse container, one write per payload extent'))


def _wd_line(fingerprint):
    return ('wd=fast,sd=fast_*,xfersize=128k,seekpct=100,rdpct=90,'
            f'user=(idfsfast.FastWorkload,jarsha{fingerprint})')


def config_text(spec, bundle, fingerprint):
    [file] = spec['files']
    return user_runtime.render_run('FAST shared sparse container', bundle, spec, device='fast',
                                   file=Path(spec['data_root'])/file['name'], capacity=file['bytes'],
                                   wd=_wd_line(fingerprint))


def validate_run_text(text):
    fingerprint = user_runtime.compiled('fast')[3]['jar_sha256']
    user_runtime.validate_run(text, device='fast', allowed_wds={_wd_line(fingerprint)},
                              expected_capacity=CAPACITY_BYTES)


def _manifest(spec, bundle, payloads):
    model = build()
    hashes = user_runtime.asset_hashes(payloads)
    fingerprint = hashes[EXTENSION]
    file = Path(spec['data_root'])/FILE_NAME
    preparation = user_prepare.plan(file, CAPACITY_BYTES, stride=OBJECT_BYTES,
                                    offset=TRANSFER_BYTES, transfer=PAYLOAD_BYTES)
    layout = [dict(id='container', files=1, size_bytes=CAPACITY_BYTES,
                   file=str(file), anchor=spec['data_root'])]
    texts = {RUN_CONFIG:config_text(spec, bundle, fingerprint)}
    texts[user_prepare.CONFIG] = user_prepare.config_text(preparation, bundle, fingerprint)
    phase = dict(name='fast_analysis', seconds=spec['elapsed'], fwd_count=0, wd_count=1,
                 target_iops='max', read_share=spec['read_share'], write_share=.10)
    manifest = dict(schema=1, id=WORKLOAD_ID, data_root=spec['data_root'], data_file=str(file),
                    file_count=1, capacity_bytes=CAPACITY_BYTES, access_engine='user', rate='max',
                    bins=layout, user_jar=EXTENSION, max_run_fwd=0, max_prepare_fwd=0,
                    max_run_wd=1, total_run_wd=1, sd_count=len(spec['clients']),
                    runtime={key:spec[key] for key in ('stock_home', 'clients', 'threads', 'ssh_user')},
                    profiles={'current':[phase]},
                    layout_sha256=digest(json_text(layout)), model_sha256=digest(json_text(model)),
                    provenance=model['provenance'], asset_sha256=hashes,
                    config_sha256={name:digest(text) for name,text in texts.items()},
                    preparation=preparation)
    return manifest, texts


def render(spec, bundle):
    bundle = safe_path(bundle)
    separate_paths(bundle, safe_path(spec['data_root']))
    if bundle.exists():
        if not bundle.is_dir() or any(file.name not in BUNDLE_FILES | {RETIRED_CONFIG} for file in bundle.iterdir()):
            raise ValueError('bundle directory contains unrelated files')
        for name in BUNDLE_FILES:
            safe_path(bundle/name)
    else:
        bundle.mkdir(parents=True)
    payloads = user_runtime.assets('fast', bundle, spec['stock_home'])
    manifest, texts = _manifest(spec, bundle, payloads)
    retire_baseline(bundle, WORKLOAD_ID)
    user_runtime.materialize(bundle, payloads)
    for name, value in {**texts, 'model.json':json_text(build()), 'manifest.json':json_text(manifest)}.items():
        safe_path(bundle/name).write_text(value)
    return load_bundle(bundle)


def load_bundle(bundle):
    bundle = safe_path(bundle)
    manifest = json.loads(safe_path(bundle/'manifest.json').read_text())
    runtime = manifest.get('runtime')
    if runtime is None:
        raise ValueError('render the FAST bundle again for the current model and build metadata')
    spec = specification(manifest['data_root'], **runtime)
    separate_paths(bundle, safe_path(spec['data_root']))
    payloads = user_runtime.assets('fast', bundle, spec['stock_home'])
    expected, texts = _manifest(spec, bundle, payloads)
    if manifest != expected or safe_path(bundle/'model.json').read_text() != json_text(build()):
        raise ValueError('FAST model or manifest differs from the current layout; render again')
    for name, text in texts.items():
        if safe_path(bundle/name).read_text() != text:
            raise ValueError(f'FAST configuration changed: {name}')
    user_runtime.validate_assets(bundle, manifest['asset_sha256'], payloads)
    return manifest


def launch_options(bundle, output, *, simulate=False, stock_home=None):
    bundle, output = safe_path(bundle), safe_path(output)
    manifest = load_bundle(bundle)
    home = Path(manifest['runtime']['stock_home'])
    if stock_home is not None and safe_path(stock_home) != home:
        raise ValueError('rerender FAST for the specified Vdbench installation')
    separate_paths(output, bundle)
    separate_paths(output, Path(manifest['data_root']))
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('use a new or empty output directory for FAST')
    options = user_runtime.execution(bundle, home/'vdbench', bundle/RUN_CONFIG, output)
    if simulate:
        options['args'].insert(1, '-s')
    return options


def main():
    from . import lifecycle

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    render_cli = commands.add_parser('render')
    render_cli.add_argument('--data-root', required=True)
    render_cli.add_argument('--stock-home', default=os.environ.get('VDBENCH_HOME', '/home/chris/PDSL/vdbench50406'))
    clients_cli = render_cli.add_mutually_exclusive_group()
    clients_cli.add_argument('--clients', help='comma-separated test clients ([user@]host); default localhost')
    clients_cli.add_argument('--client', action='append', help=argparse.SUPPRESS)
    render_cli.add_argument('--threads', type=int, default=64, help='I/O threads per test client')
    render_cli.add_argument('--bundle', required=True)
    for command in ('prepare', 'check', 'run'):
        cli = commands.add_parser(command)
        cli.add_argument('--bundle', required=True)
        if command in ('prepare', 'run'):
            cli.add_argument('--execute', action='store_true')
            cli.add_argument('--output', required=command == 'run')
        if command == 'run':
            cli.add_argument('--simulate', action='store_true')

    args = parser.parse_args()
    try:
        if args.command == 'render':
            spec = specification(args.data_root, args.stock_home,
                                 args.clients if args.clients is not None else args.client, args.threads)
            result = render(spec, args.bundle)
        elif args.command == 'run' and args.simulate:
            return subprocess.run(**launch_options(args.bundle, args.output, simulate=True)).returncode
        elif args.command == 'check':
            result = lifecycle.check_ready(args.bundle)
        else:
            spec = load_bundle(args.bundle)
            options = dict(vdbench=Path(spec['runtime']['stock_home'])/'vdbench', execute=args.execute)
            action = getattr(lifecycle, args.command)
            if args.output is None:
                with tempfile.TemporaryDirectory(prefix='fast-prepare-output-') as folder:
                    result = action(args.bundle, output=Path(folder)/WORKLOAD_ID, **options)
            else:
                result = action(args.bundle, output=args.output, **options)
        print(json_text(result), end='')
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(2, f'FAST: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
