"""Render and run the optional FAST small-file workload using shared containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from .core import json_text, safe_path
from .user_runtime import JAR_SHA256

OBJECT_BYTES = 4 * 1024**2
OBJECTS = 1_048_576
SHARDS = 8
SLOTS_PER_SHARD = OBJECTS // SHARDS
SHARD_BYTES = SLOTS_PER_SHARD * OBJECT_BYTES
PAYLOAD_BYTES = 400 * 1024
TRANSFER_BYTES = 128 * 1024
SEED = 1048596

EXTENSION = 'fast-user.jar'
CONFIG = 'run_fast.vdb'
PACKAGE = Path(__file__).parent
JAVA = PACKAGE/'fast_user_java'
SOURCES = (JAVA/'idfsfast/FastWorkload.java', PACKAGE/'graph_zipf_java/idfsgraphzipf/ZipfDistribution.java')
BUNDLE_FILES = {EXTENSION, CONFIG, 'manifest.json', 'vdbench'}


def shard_name(index):
    return f'fast{index:02d}.bin'


def separate_paths(first, second):
    if first == second or first in second.parents or second in first.parents:
        raise ValueError(f'FAST requires separate directory trees: {first} and {second}')


def specification(data_root, stock_home, clients, threads):
    if (not 1 <= len(clients) <= 8 or clients[0] != 'localhost' or len(set(clients)) != len(clients)
            or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host) for host in clients)):
        raise ValueError('use 1 to 8 distinct hostnames or IPv4 addresses, starting with localhost')
    if type(threads) is not int or not len(clients) <= threads <= 2**31 - 1:
        raise ValueError('threads must be a Vdbench integer and at least the client count')
    return dict(schema=1, data_root=str(safe_path(data_root)), stock_home=str(safe_path(stock_home)),
                clients=list(clients), threads=threads, seed=SEED, capacity_bytes=SHARDS*SHARD_BYTES,
                effective_bytes=OBJECTS*PAYLOAD_BYTES, object_count=OBJECTS, object_bytes=OBJECT_BYTES,
                files=[dict(name=shard_name(i), bytes=SHARD_BYTES) for i in range(SHARDS)],
                elapsed=600, iorate='max', read_share=.90, zipf_alpha=.99, transfer_bytes=TRANSFER_BYTES,
                payload_bytes=PAYLOAD_BYTES, payload_offset=TRANSFER_BYTES)


def config_text(spec, bundle, fingerprint):
    lines = ['data_errors=1', 'messagescan=no', 'formatsds=no', 'concatenatesds=yes',
             f'hd=default,jvms=1,shell=ssh,vdbench={bundle}']
    aliases = [f'h{i:03d}' for i in range(len(spec['clients']))]
    for alias, host in zip(aliases, spec['clients']):
        lines.append(f'hd={alias},system={host}')
    names = [f'fast{i:02d}' for i in range(SHARDS)]
    for name, file in zip(names, spec['files']):
        lines.append(f"sd={name},lun={spec['data_root']}/{file['name']},size={file['bytes']},openflags=o_direct")
    lines.append(f"wd=fast,host=({','.join(aliases)}),sd=({','.join(names)}),threads={spec['threads']},"
                 f"xfersize=128k,seekpct=100,rdpct=90,user=(idfsfast.FastWorkload,jarsha{fingerprint})")
    lines.append('rd=continuous,wd=fast,iorate=max,elapsed=600,interval=1,warmup=0')
    return '\n'.join(lines) + '\n'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def launcher(spec, bundle, fingerprint):
    text = (JAVA/'vdbench').read_text()
    for marker, value in {'@STOCK_HOME@': spec['stock_home'], '@RUNTIME_DIR@': str(bundle),
                          '@STOCK_SHA@': JAR_SHA256, '@EXTENSION_SHA@': fingerprint}.items():
        text = text.replace(marker, value)
    return text


def compile_extension(stock_jar, destination):
    if sha(stock_jar) != JAR_SHA256:
        raise ValueError('FAST requires the original Vdbench 5.04.06 JAR')
    with tempfile.TemporaryDirectory(prefix='fast-java-') as temp:
        subprocess.run(['javac', '-J-XX:-UsePerfData', '-J-Djava.io.tmpdir='+temp,
                        '--release', '8', '-cp', str(stock_jar), '-d', temp,
                        *map(str, SOURCES)], check=True)
        classes = Path(temp)
        with zipfile.ZipFile(destination, 'w') as archive:
            for file in sorted(classes.rglob('*.class')):
                name = file.relative_to(classes).as_posix()
                entry = zipfile.ZipInfo(name)
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, file.read_bytes())


def render(spec, bundle):
    bundle = safe_path(bundle)
    separate_paths(bundle, safe_path(spec['data_root']))
    if bundle.exists():
        if not bundle.is_dir() or any(file.name not in BUNDLE_FILES for file in bundle.iterdir()):
            raise ValueError('bundle directory contains unrelated files')
        for name in BUNDLE_FILES:
            safe_path(bundle/name)
    else:
        bundle.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='fast-render-') as temp:
        staging = Path(temp)
        compile_extension(Path(spec['stock_home'])/'vdbench.jar', staging/EXTENSION)
        fingerprint = sha(staging/EXTENSION)
        (staging/CONFIG).write_text(config_text(spec, bundle, fingerprint))
        (staging/'vdbench').write_text(launcher(spec, bundle, fingerprint))
        (staging/'vdbench').chmod(0o755)
        (staging/'manifest.json').write_text(json_text(dict(spec, jar_sha256=fingerprint)))
        for name in (EXTENSION, CONFIG, 'vdbench', 'manifest.json'):
            target = bundle/name
            target.write_bytes((staging/name).read_bytes())
            target.chmod((staging/name).stat().st_mode & 0o777)
    return load_bundle(bundle)


def load_bundle(bundle):
    bundle = safe_path(bundle)
    manifest = json.loads(safe_path(bundle/'manifest.json').read_text())
    spec = specification(manifest['data_root'], manifest['stock_home'], manifest['clients'], manifest['threads'])
    separate_paths(bundle, safe_path(spec['data_root']))
    fingerprint = sha(safe_path(bundle/EXTENSION))
    if manifest != dict(spec, jar_sha256=fingerprint):
        raise ValueError('FAST bundle manifest or User JAR mismatch')
    if safe_path(bundle/CONFIG).read_text() != config_text(spec, bundle, fingerprint):
        raise ValueError('FAST benchmark configuration changed')
    if safe_path(bundle/'vdbench').read_text() != launcher(spec, bundle, fingerprint):
        raise ValueError('FAST launcher changed or bundle was moved')
    return manifest


def launch_options(bundle, output, *, simulate=False):
    bundle, output = safe_path(bundle), safe_path(output)
    manifest = load_bundle(bundle)
    separate_paths(output, bundle)
    separate_paths(output, Path(manifest['data_root']))
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('use a new or empty output directory for FAST')
    args = [str(bundle/'vdbench')]
    if simulate:
        args.append('-s')
    args.extend(['-f', str(bundle/CONFIG), '-o', str(output)])
    return dict(args=args, cwd=bundle)


def main():
    from . import fast_prepare

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    render_cli = commands.add_parser('render')
    render_cli.add_argument('--data-root', required=True)
    render_cli.add_argument('--stock-home', default=os.environ.get('VDBENCH_HOME', '/home/chris/PDSL/vdbench50406'))
    render_cli.add_argument('--client', action='append')
    render_cli.add_argument('--threads', type=int, default=64)
    render_cli.add_argument('--bundle', required=True)
    for command in ('prepare', 'check', 'run'):
        cli = commands.add_parser(command)
        cli.add_argument('--bundle', required=True)
        cli.add_argument('--confirm-nfs-layout', action='store_true')
        if command in ('prepare', 'run'):
            cli.add_argument('--execute', action='store_true')
        if command == 'run':
            cli.add_argument('--output', required=True)
            cli.add_argument('--simulate', action='store_true')

    args = parser.parse_args()
    try:
        if args.command == 'render':
            spec = specification(args.data_root, args.stock_home, args.client or ['localhost'], args.threads)
            result = render(spec, args.bundle)
        elif args.command == 'run' and args.simulate:
            return subprocess.run(**launch_options(args.bundle, args.output, simulate=True)).returncode
        elif args.command == 'run':
            if not args.execute:
                raise ValueError('the timed FAST workload performs reads and writes; pass --execute to start it')
            opts = launch_options(args.bundle, args.output)
            spec = load_bundle(args.bundle)
            with fast_prepare.dataset_lock(Path(spec['data_root']), create=False):
                fast_prepare.check(spec, args.confirm_nfs_layout)
                return subprocess.run(**opts).returncode
        else:
            spec = load_bundle(args.bundle)
            if args.command == 'prepare':
                if not args.execute:
                    raise ValueError('FAST preparation writes 400 GiB; pass --execute to start it')
                result = fast_prepare.prepare(spec, args.confirm_nfs_layout)
            else:
                result = fast_prepare.check(spec, args.confirm_nfs_layout)
        print(json_text(result), end='')
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(2, f'FAST: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
