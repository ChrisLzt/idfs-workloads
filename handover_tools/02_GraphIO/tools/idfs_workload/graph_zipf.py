"""Shared pre-existing4TiB file:32B reads with a full-object Zipf distribution."""
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat

PACKAGE = Path(__file__).parent
JAVA = PACKAGE/'graph_zipf_java'
JAR_SHA256 = '8a87321b1b3cd6b60a5251a149359b628dd95a52b93efd7a990703478cfbf4d5'
CAPACITY = 4398046511104
OBJECT = 4194304
SEED = 1048596
EXTENSION = 'graph-zipf-user.jar'
HOST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')


def build():
    lane = dict(bin='edges', operation='read', weight=1, xfersize=32,
                fileio='random', fileselect='random', threads=1, file_sharing=True)
    return dict(id='graph_zipf', access_engine='user',
        bins=[dict(id='edges', group='graph', files=1, size_bytes=CAPACITY)],
        profiles={name:[dict(name=label, seconds=600, lanes=[deepcopy(lane)])]
                  for name,label in [('current','zipf_all'), ('baseline','uniform_all')]},
        capacity_policy=dict(kind='existing_file_inventory', expected_bytes=CAPACITY,
                             min_bytes=CAPACITY, max_bytes=CAPACITY),
        input_policy=dict(mode='existing_file', relative_file='test.graph.edge.props',
                          prepare_allowed=True,
                          layout=dict(object_size=OBJECT, stripe_unit=OBJECT, stripe_count=1)),
        user_zipf=dict(alpha=.99, object_count=1048576, object_bytes=OBJECT,
                       transfer_bytes=32, mapping_seed=SEED,
                       mapping='SplittableRandom Fisher-Yates bijection',
                       sampling='independent draws from exact discrete CDF', request_quota=None),
        provenance=dict(workload_label='4TiB shared graph I/O with object Zipf',
                        document='课题5-测试大纲V1.2.docx',
                        document_sha256='ad02252820b46cf234269eda4133a17d30e2a9c860f78fd377bb481e7e2afc87',
                        sections=['7.1.12','7.1.13','A.6 Graph_IOPS.vdb'],
                        source_semantics='shared edge-property file;32-byte random reads',
                        adaptation='4TiB;shared global object Zipf0.99;O_DIRECT;600s single RD;max rate',
                        preparation='separate explicit preparer or external tool;never part of timed test;write transfer size is configurable',
                        scope='synthetic storage I/O based on supplied outline;not a graph algorithm or native trace',
                        limitation='application request distribution;actual coverage and module metrics require measurement'))


def validate_model(model):
    if model != build():
        raise ValueError('graph Zipf model must retain the4TiB/32B/global0.99 existing-file contract')
    return CAPACITY


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _runtime():
    meta = json.loads((JAVA/'runtime.json').read_text())
    if meta.get('stock_jar_sha256') != JAR_SHA256 or meta.get('java_class_version') != 52:
        raise ValueError('graph Zipf extension must target the stock5.04.06 JAR and Java8')
    sources = sorted((JAVA/'idfsgraphzipf').glob('*.java'))
    actual = {p.relative_to(JAVA).as_posix():_sha(p.read_bytes()) for p in sources}
    if len(sources) != 2 or actual != meta.get('source_sha256'):
        raise ValueError('graph Zipf Java sources differ from built extension; rebuild explicitly')
    if _sha((JAVA/EXTENSION).read_bytes()) != meta.get('jar_sha256'):
        raise ValueError('graph Zipf extension checksum mismatch')
    return meta


def _settings(clients=None, threads=64, stock_home=None):
    from .core import safe_path
    clients = ['localhost'] if clients is None else list(clients)
    if (not 1 <= len(clients) <= 512 or len(set(clients)) != len(clients)
            or any(not isinstance(h,str) or not HOST.fullmatch(h) for h in clients)):
        raise ValueError('graph clients must be1..512 distinct simple hostnames or IPv4 addresses')
    if type(threads) is not int or not 1 <= threads <= 4096:
        raise ValueError('graph threads per client must be in1..4096')
    home = safe_path(stock_home or os.environ.get('VDBENCH_HOME','/home/chris/PDSL/vdbench50406'))
    return dict(clients=clients, threads=threads, stock_home=str(home))


def _texts(data_file, runtime_root, settings):
    from .core import safe_path
    runtime_root = safe_path(runtime_root)
    fingerprint = 'jarsha'+_runtime()['jar_sha256']
    texts, profiles = {}, {}
    for profile,mode in [('current','zipf'), ('baseline','uniform')]:
        lines = ['* Shared existing4TiB graph storage model;synthetic object distribution',
                 'data_errors=1', 'messagescan=no', 'formatsds=no',
                 f'hd=default,jvms=1,vdbench={runtime_root}']
        for i,host in enumerate(settings['clients']):
            lines.append(f'hd=h{i:03d},system={host}')
        for i in range(len(settings['clients'])):
            lines.append(f'sd=graph_{i:03d},host=h{i:03d},lun={data_file},size={CAPACITY},'
                         f'threads={settings["threads"]},openflags=o_direct,offset=0')
        lines.extend([f'wd=graph,sd=graph*,xfersize=32,seekpct=100,rdpct=100,'
                      f'user=(idfsgraphzipf.ZipfWorkload,{mode},seed{SEED},{fingerprint})',
                      'rd=continuous,wd=graph*,iorate=max,elapsed=600,interval=1,warmup=0'])
        texts[f'run_{profile}.vdb'] = '\n'.join(lines)+'\n'
        profiles[profile] = [dict(name='zipf_all' if profile=='current' else 'uniform_all',
                                 seconds=600, fwd_count=0, wd_count=1,
                                 target_iops='max', read_share=1., write_share=0.)]
    return texts, profiles


def config_texts(model, data_root, rate, runtime_root=None):
    validate_model(model)
    if rate != 'max': raise ValueError('graph Zipf requires iorate=max without quotas')
    root = runtime_root or PACKAGE.parent/'graph_zipf'/'rendered'
    return _texts(Path(data_root)/'test.graph.edge.props', root, _settings())


def _assets(settings, runtime_root):
    meta = _runtime()
    payloads = {EXTENSION:(JAVA/EXTENSION).read_bytes(),
                'runtime.json':(JAVA/'runtime.json').read_bytes(),
                'stock-home':(settings['stock_home']+'\n').encode()}
    if _sha(payloads[EXTENSION]) != meta['jar_sha256']:
        raise ValueError('graph Zipf runtime asset mismatch')
    launcher = (JAVA/'vdbench').read_text()
    values = {'@RUNTIME_DIR@':str(runtime_root),'@STOCK_HOME@':settings['stock_home'],
              '@EXTENSION_SHA@':meta['jar_sha256'],'@META_SHA@':_sha(payloads['runtime.json']),
              '@HOME_SHA@':_sha(payloads['stock-home'])}
    for marker,value in values.items(): launcher=launcher.replace(marker,value)
    payloads['vdbench']=launcher.encode()
    return payloads


def _manifest(model, file, output, settings):
    from .core import json_text, digest
    texts, profiles = _texts(file, output, settings)
    layout = [dict(id='edges',files=1,size_bytes=CAPACITY,file=str(file),anchor=str(file.parent))]
    m = dict(schema=1,id='graph_zipf', access_engine='user', data_root=str(file.parent),
             data_file=str(file), capacity_bytes=CAPACITY,file_count=1,bins=layout,
             input_policy=model['input_policy'],profiles=profiles,rate='max',
             max_run_fwd=0,max_prepare_fwd=0,max_run_wd=1,total_run_wd=1,
             sd_count=len(settings['clients']),runtime=settings,
             layout_sha256=digest(json_text(layout)),model_sha256=digest(json_text(model)),
             config_sha256={n:digest(t) for n,t in texts.items()},
             asset_sha256={n:_sha(b) for n,b in _assets(settings,output).items()},provenance=model['provenance'])
    return m,texts


def render(model, *, data_root, output, rate, data_file=None, clients=None, threads=64, stock_home=None):
    from .core import safe_path, json_text
    validate_model(model)
    if rate != 'max': raise ValueError('graph Zipf requires iorate=max without quotas')
    root, output = safe_path(data_root),safe_path(output)
    file = safe_path(data_file if data_file is not None else root/'test.graph.edge.props')
    root = file.parent
    if output == root or root in output.parents or output in root.parents:
        raise ValueError('configuration and existing data paths must be separate')
    settings = _settings(clients,threads,stock_home)
    manifest,texts = _manifest(model,file,output,settings)
    payloads = _assets(settings,output)
    expected = set(payloads)|set(texts)|{'model.json','manifest.json'}
    if output.exists() and (not output.is_dir() or any(p.name not in expected for p in output.iterdir())):
        raise ValueError('graph configuration directory contains unrelated or preparation files')
    output.mkdir(parents=True,exist_ok=True)
    for name,data in payloads.items():
        safe_path(output/name).write_bytes(data)
    (output/'vdbench').chmod(0o755)
    for name,text in {**texts,'model.json':json_text(model),'manifest.json':json_text(manifest)}.items():
        safe_path(output/name).write_text(text)
    validate_bundle(output)
    return manifest


def validate_bundle(output):
    from .core import safe_path
    output = safe_path(output)
    model = json.loads((output/'model.json').read_text()); validate_model(model)
    manifest = json.loads((output/'manifest.json').read_text())
    file = safe_path(manifest['data_file'])
    settings = _settings(**manifest['runtime'])
    expected,texts = _manifest(model,file,output,settings)
    if manifest != expected: raise ValueError('graph Zipf manifest differs from rendered input/model')
    for name,text in texts.items():
        if (output/name).is_symlink() or (output/name).read_text() != text:
            raise ValueError('graph Zipf configuration changed: '+name)
    for name,digest in expected['asset_sha256'].items():
        p = safe_path(output/name)
        if not p.is_file() or _sha(p.read_bytes()) != digest:
            raise ValueError('graph Zipf runtime asset changed: '+name)
    if (output/'prepare_data.vdb').exists(): raise ValueError('existing-file graph must not include preparation')
    if not os.access(output/'vdbench',os.X_OK): raise ValueError('graph launcher is not executable')
    return manifest


def validate_run_text(text):
    """Closed grammar; bundle validation additionally regenerates every exact byte."""
    lines = [l for l in text.splitlines() if l and not l.startswith('*')]
    rds = [l for l in lines if l.startswith('rd=')]
    wds = [l for l in lines if l.startswith('wd=')]
    sds = [l for l in lines if l.startswith('sd=')]
    if (rds != ['rd=continuous,wd=graph*,iorate=max,elapsed=600,interval=1,warmup=0']
            or len(wds)!=1 or not 1<=len(sds)<=512):
        raise ValueError('graph Zipf requires one600s RD and at most512 SDs')
    fingerprint = 'jarsha'+_runtime()['jar_sha256']
    allowed = [f'wd=graph,sd=graph*,xfersize=32,seekpct=100,rdpct=100,'
               f'user=(idfsgraphzipf.ZipfWorkload,{mode},seed{SEED},{fingerprint})' for mode in ('zipf','uniform')]
    if wds[0] not in allowed or any('openflags=o_direct,offset=0' not in l for l in sds):
        raise ValueError('graph32B/random/read-only/O_DIRECT contract changed')
    defaults = [l for l in lines if l.startswith('hd=default,')]
    hosts = [l for l in lines if l.startswith('hd=') and not l.startswith('hd=default,')]
    if len(defaults)!=1 or len(hosts)!=len(sds):
        raise ValueError('graph client/SD inventory differs')
    default = re.fullmatch(r'hd=default,jvms=1,vdbench=(/[A-Za-z0-9_./-]+)',defaults[0])
    first = re.fullmatch(r'sd=graph_000,host=h000,lun=(/[A-Za-z0-9_./-]+),'
                         r'size=4398046511104,threads=(\d+),openflags=o_direct,offset=0',sds[0])
    matched = [re.fullmatch(r'hd=h\d{3},system=([A-Za-z0-9][A-Za-z0-9_.-]*)',l) for l in hosts]
    if default is None or first is None or any(m is None for m in matched):
        raise ValueError('graph client/SD grammar changed')
    settings = _settings([m[1] for m in matched],int(first[2]))
    generated,_ = _texts(Path(first[1]),Path(default[1]),settings)
    profile = 'current' if wds[0]==allowed[0] else 'baseline'
    expected = [l for l in generated[f'run_{profile}.vdb'].splitlines() if l and not l.startswith('*')]
    if lines != expected:
        raise ValueError('graph configuration contains unexpected parameters or hooks')


def check_ready(bundle, *, allow_preparing=False):
    """Metadata only;external completion markers are optional,active preparation is blocked."""
    from .lifecycle import mount_info
    from .core import safe_path
    manifest = validate_bundle(bundle);file = safe_path(manifest['data_file'])
    from .graph_prepare import markers
    intent,_=markers(file)
    if (intent.exists() or intent.is_symlink()) and not allow_preparing:
        raise ValueError('graph preparation is incomplete or active;test cannot start')
    mount = mount_info(file)
    if mount['fstype'] not in ('nfs','nfs4','ceph','idfs','fuse.ceph','fuse.ceph-fuse','fuse.idfs','fuse.idfs-fuse'):
        raise ValueError('existing graph input must be on a CephFS/IDFS/NFS data mount')
    st = file.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size != CAPACITY:
        raise ValueError('existing graph input must be a regular file of exactly4TiB')
    if st.st_blocks*512 < st.st_size:
        raise ValueError('existing graph input appears sparse or incompletely allocated')
    if not os.access(file,os.R_OK): raise ValueError('existing graph input is not readable')
    from .file_layout import require_layout
    inspected=require_layout(file,fstype=mount['fstype']);layout=inspected['layout']
    fs = os.statvfs(file.parent)
    return dict(id=manifest['id'],data_file=str(file),data_root=str(file.parent),mount=mount,
                file_count=1,capacity_bytes=CAPACITY,allocated_bytes=st.st_blocks*512,
                required_free_bytes=0,available_bytes=fs.f_bavail*fs.f_frsize,state='existing-file-ready',
                layout=layout,
                layout_verification=inspected['verification'],
                verification='stat/allocation/layout metadata only;does not prove payload or complete RADOS inventory')


def execution(bundle, vdbench, config, output):
    bundle = Path(bundle).absolute();vdbench = Path(vdbench).absolute()
    manifest = validate_bundle(bundle)
    if _sha((vdbench.parent/'vdbench.jar').read_bytes()) != JAR_SHA256:
        raise ValueError('graph Zipf requires the unmodified stock Vdbench5.04.06 JAR')
    if str(vdbench.parent) != manifest['runtime']['stock_home']:
        raise ValueError('Vdbench home changed;render again so remote JVMs use the same installation')
    env = os.environ.copy()
    for key in ('JDK_JAVA_OPTIONS','JAVA_TOOL_OPTIONS','_JAVA_OPTIONS'):
        env.pop(key,None)
    env['VDBENCH_HOME'] = str(vdbench.parent)
    return dict(args=[str(bundle/'vdbench'),'-f',str(config),'-o',str(output)],cwd=bundle,env=env)
