"""Shared pre-existing4TiB file:32B reads with a full-object Zipf distribution."""
import json
import os
from pathlib import Path
from . import user_prepare, user_runtime
from .core import RETIRED_CONFIG, RUN_CONFIG, retire_baseline

PACKAGE = Path(__file__).parent
JAVA = PACKAGE/'graph_zipf_java'
JAR_SHA256 = user_runtime.JAR_SHA256
CAPACITY = 4398046511104
OBJECT = 4194304
SEED = 1048596
EXTENSION = 'graph-zipf-user.jar'


def build():
    lane = dict(bin='edges', operation='read', weight=1, xfersize=32,
                fileio='random', fileselect='random', threads=1, file_sharing=True)
    return dict(id='graph_zipf', access_engine='user',
        bins=[dict(id='edges', group='graph', files=1, size_bytes=CAPACITY)],
        profiles={'current':[dict(name='zipf_all', seconds=600, lanes=[lane])]},
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
                        preparation='separate Vdbench User write pass or existing dense file; never part of the timed test',
                        scope='synthetic storage I/O based on supplied outline;not a graph algorithm or native trace',
                        limitation='application request distribution;actual coverage and module metrics require measurement'))


def validate_model(model):
    if model != build():
        raise ValueError('graph Zipf model must retain the4TiB/32B/global0.99 existing-file contract')
    return CAPACITY


def _runtime():
    return user_runtime.compiled('graph')[3]


def _wd_line(fingerprint):
    return (f'wd=graph,sd=graph_*,xfersize=32,seekpct=100,rdpct=100,'
            f'user=(idfsgraphzipf.ZipfWorkload,zipf,seed{SEED},jarsha{fingerprint})')


def _texts(data_file, runtime_root, settings):
    from .core import safe_path
    runtime_root = safe_path(runtime_root)
    fingerprint = _runtime()['jar_sha256']
    preparation = user_prepare.plan(data_file, CAPACITY)
    texts = {user_prepare.CONFIG: user_prepare.config_text(preparation, runtime_root, fingerprint)}
    texts[RUN_CONFIG] = user_runtime.render_run(
        'Shared 4 TiB graph file with object Zipf', runtime_root, settings, device='graph',
        file=data_file, capacity=CAPACITY, wd=_wd_line(fingerprint))
    phase = dict(name='zipf_all', seconds=600, fwd_count=0, wd_count=1,
                 target_iops='max', read_share=1., write_share=0.)
    return texts, {'current':[phase]}


def config_texts(model, data_root, rate, runtime_root=None, *, settings=None):
    from .core import DEFAULT_CONFIG_ROOT
    validate_model(model)
    if rate != 'max': raise ValueError('graph Zipf requires iorate=max without quotas')
    root = runtime_root or DEFAULT_CONFIG_ROOT/'graph_zipf'/'rendered'
    return _texts(Path(data_root)/'test.graph.edge.props', root, settings or user_runtime.client_settings())


def _assets(settings, runtime_root):
    return user_runtime.assets('graph', runtime_root, settings['stock_home'])


def _manifest(model, file, output, settings):
    from .core import json_text, digest
    texts, profiles = _texts(file, output, settings)
    layout = [dict(id='edges',files=1,size_bytes=CAPACITY,file=str(file),anchor=str(file.parent))]
    m = dict(schema=1,id='graph_zipf', access_engine='user', data_root=str(file.parent),
             data_file=str(file), capacity_bytes=CAPACITY,file_count=1,bins=layout,
             input_policy=model['input_policy'],profiles=profiles,rate='max',
             max_run_fwd=0,max_prepare_fwd=0,max_run_wd=1,total_run_wd=1,
             sd_count=len(settings['clients']),runtime=settings,
             user_jar=EXTENSION, preparation=user_prepare.plan(file, CAPACITY),
             layout_sha256=digest(json_text(layout)),model_sha256=digest(json_text(model)),
             config_sha256={n:digest(t) for n,t in texts.items()},
             asset_sha256=user_runtime.asset_hashes(_assets(settings,output)),provenance=model['provenance'])
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
    settings = user_runtime.client_settings(clients, threads, stock_home)
    manifest,texts = _manifest(model,file,output,settings)
    payloads = _assets(settings,output)
    expected = set(payloads)|set(texts)|{'model.json','manifest.json'}
    if output.exists() and (not output.is_dir() or any(p.name not in expected | {RETIRED_CONFIG} for p in output.iterdir())):
        raise ValueError('graph configuration directory contains unrelated files')
    output.mkdir(parents=True,exist_ok=True)
    retire_baseline(output, model['id'])
    user_runtime.materialize(output, payloads)
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
    runtime = manifest['runtime']
    settings = user_runtime.client_settings(runtime['clients'], runtime['threads'], runtime['stock_home'],
                                            ssh_user=runtime['ssh_user'])
    expected,texts = _manifest(model,file,output,settings)
    if manifest != expected: raise ValueError('graph Zipf manifest differs from rendered input/model')
    for name,text in texts.items():
        if (output/name).is_symlink() or (output/name).read_text() != text:
            raise ValueError('graph Zipf configuration changed: '+name)
    user_runtime.validate_assets(output, expected['asset_sha256'], _assets(settings, output))
    return manifest


def validate_run_text(text):
    fingerprint = _runtime()['jar_sha256']
    user_runtime.validate_run(text, device='graph', allowed_wds={_wd_line(fingerprint)},
                              expected_capacity=CAPACITY)


def check_ready(bundle):
    from .lifecycle import check_ready as check_container

    return check_container(bundle)


def execution(bundle, vdbench, config, output):
    validate_bundle(bundle)
    return user_runtime.execution(bundle, vdbench, config, output)
