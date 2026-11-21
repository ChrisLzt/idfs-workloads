"""Configure and launch a built User extension with the pinned stock Vdbench."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import re

from .core import safe_path

JAR_SHA256 = '8a87321b1b3cd6b60a5251a149359b628dd95a52b93efd7a990703478cfbf4d5'
DEFAULT_STOCK_HOME = '/home/chris/PDSL/vdbench50406'
PACKAGE = Path(__file__).parent
HOST = r'[A-Za-z0-9][A-Za-z0-9_.-]*'
USER = r'[A-Za-z_][A-Za-z0-9_.-]*'
CLIENT = re.compile(rf'(?:{USER}@)?(?P<host>{HOST})')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stock_home(path=None):
    return safe_path(path or os.environ.get('VDBENCH_HOME', DEFAULT_STOCK_HOME))


def client_settings(clients=None, threads=64, home=None, *, ssh_user=None):
    if isinstance(clients, str):
        clients = [host.strip() for host in clients.split(',')]
    else:
        clients = ['localhost'] if clients is None else list(clients)
    hosts = [CLIENT.fullmatch(client) if isinstance(client, str) else None for client in clients]
    if (not 1 <= len(hosts) <= 512 or None in hosts
            or len({match['host'].lower() for match in hosts}) != len(hosts)):
        raise ValueError('use 1..512 distinct client hostnames or IPv4 addresses, optionally prefixed by user@')
    if type(threads) is not int or not 1 <= threads <= 4096:
        raise ValueError('threads per client must be in 1..4096')
    ssh_user = getpass.getuser() if ssh_user is None else ssh_user
    if not isinstance(ssh_user, str) or not re.fullmatch(USER, ssh_user):
        raise ValueError('invalid default SSH account')
    return dict(clients=clients, threads=threads, stock_home=str(stock_home(home)), ssh_user=ssh_user)


def render_run(title, output, settings, *, device, file, capacity, wd, alignment=None):
    """Assign one SD to each client, all pointing to the same ordinary file."""
    lines = ['data_errors=1', 'messagescan=no', 'formatsds=no',
             f'hd=default,jvms=1,shell=ssh,user={settings["ssh_user"]},vdbench={safe_path(output)}']
    clients = settings['clients']
    for i, client in enumerate(clients):
        account, _, host = client.rpartition('@')
        user = f',user={account}' if account else ''
        lines.append(f'hd=h{i:03d},system={host}{user}')
    suffix = '' if alignment is None else f',align={alignment}'
    lines.extend(f'sd={device}_{i:03d},host=h{i:03d},lun={file},size={capacity},'
                 f'threads={settings["threads"]},openflags=o_direct,offset=0{suffix}'
                 for i in range(len(clients)))
    workload = wd.partition(',')[0].removeprefix('wd=')
    lines.extend([wd, f'rd=continuous,wd={workload},iorate=max,elapsed=600,interval=1,warmup=0'])
    if title:
        lines.insert(0, f'* {title}')
    return '\n'.join(lines) + '\n'


def validate_run(text, *, device, allowed_wds, alignment=None, expected_capacity=None):
    lines = [line for line in text.splitlines() if line and not line.startswith('*')]
    wds = [line for line in lines if line.startswith('wd=')]
    defaults = [line for line in lines if line.startswith('hd=default,')]
    hosts = [line for line in lines if line.startswith('hd=') and not line.startswith('hd=default,')]
    devices = [line for line in lines if line.startswith('sd=')]
    if len(wds) != 1 or wds[0] not in allowed_wds or len(defaults) != 1 or not devices:
        raise ValueError('expected one timed User workload and explicit client SDs')
    default = re.fullmatch(rf'hd=default,jvms=1,shell=ssh,user=(?P<user>{USER}),'
                           r'vdbench=(?P<bundle>/[A-Za-z0-9_./-]+)', defaults[0])
    names = [re.fullmatch(rf'hd=h\d{{3}},system=(?P<host>{HOST})(?:,user=(?P<user>{USER}))?', line)
             for line in hosts]
    suffix = '' if alignment is None else f',align={alignment}'
    first = re.fullmatch(rf'sd={device}_000,host=h000,lun=(/[A-Za-z0-9_./-]+),'
                         rf'size=(\d+),threads=(\d+),openflags=o_direct,offset=0{suffix}', devices[0])
    if default is None or first is None or any(name is None for name in names):
        raise ValueError('invalid User host or SD declaration')
    capacity = int(first[2])
    if capacity <= 0 or (expected_capacity is not None and capacity != expected_capacity):
        raise ValueError('unexpected User container size')
    clients = [f'{name["user"]}@{name["host"]}' if name['user'] else name['host'] for name in names]
    settings = client_settings(clients, int(first[3]), ssh_user=default['user'])
    expected = render_run(None, default['bundle'], settings, device=device, file=safe_path(first[1]),
                          capacity=capacity, wd=wds[0], alignment=alignment)
    if lines != expected.splitlines():
        raise ValueError('User test must assign one SD per client with the same file and thread count')


def compiled(key):
    from .user_build import BUILDS, source_hashes

    directory, extension, _ = BUILDS[key]
    source = PACKAGE/directory
    jar = (source/extension).read_bytes()
    metadata = (source/'runtime.json').read_bytes()
    runtime = json.loads(metadata)
    if runtime != dict(stock_jar_sha256=JAR_SHA256, java_class_version=52,
                       jar_sha256=hashlib.sha256(jar).hexdigest(), source_sha256=source_hashes(key)):
        raise ValueError(f'{key} User sources or runtime differ from the built Java 8 extension; rebuild explicitly')
    return extension, jar, metadata, runtime


def assets(key, bundle, home):
    extension, jar, metadata, runtime = compiled(key)
    home = stock_home(home)
    bundle = safe_path(bundle)
    home_file = (str(home)+'\n').encode()
    variables = {'RUNTIME_DIR': str(bundle), 'STOCK_HOME': str(home), 'STOCK_SHA': JAR_SHA256,
                 'EXTENSION': extension, 'EXTENSION_SHA': runtime['jar_sha256'],
                 'META_SHA': hashlib.sha256(metadata).hexdigest(),
                 'HOME_SHA': hashlib.sha256(home_file).hexdigest(),
                 'SLAVE_HEAP': '512m' if key in ('fast', 'graph') else '2048m'}
    launcher = (PACKAGE/'user_vdbench.sh').read_text()
    for marker, value in variables.items():
        launcher = launcher.replace(f'@{marker}@', value)
    return {extension: jar, 'runtime.json': metadata, 'stock-home': home_file, 'vdbench': launcher.encode()}


def asset_hashes(payloads):
    return {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}


def materialize(bundle, payloads):
    targets = {name:safe_path(bundle/name) for name in payloads}
    for name, payload in payloads.items():
        targets[name].write_bytes(payload)
    (bundle/'vdbench').chmod(0o755)
    return asset_hashes(payloads)


def validate_assets(bundle, recorded, payloads):
    if recorded != asset_hashes(payloads):
        raise ValueError('User runtime asset inventory differs from the built extension')
    for name, payload in payloads.items():
        if safe_path(bundle/name).read_bytes() != payload:
            raise ValueError(f'User runtime asset changed: {name}')
    if not os.access(bundle/'vdbench', os.X_OK):
        raise ValueError('User launcher is not executable')


def execution(bundle, vdbench, config, output):
    bundle = safe_path(bundle)
    vdbench = Path(vdbench).absolute()
    if (bundle/'stock-home').read_text().strip() != str(vdbench.parent):
        raise ValueError('Vdbench installation differs from this bundle; render again')
    if sha256(vdbench.parent/'vdbench.jar') != JAR_SHA256:
        raise ValueError('User workloads require the unmodified Vdbench 5.04.06 JAR')
    env = os.environ.copy()
    for key in ('JDK_JAVA_OPTIONS', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS'):
        env.pop(key, None)
    env['VDBENCH_HOME'] = str(vdbench.parent)
    return dict(args=[str(bundle/'vdbench'), '-f', str(config), '-o', str(output)],
                cwd=bundle, env=env)
