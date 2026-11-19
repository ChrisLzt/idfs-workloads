"""Audited Region2 full-time key subset using the stock5.04.06 User API."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from .baleen_user_source import HEADER
from .user_runtime import JAR_SHA256, execution

PACKAGE=Path(__file__).parent
SOURCE=PACKAGE/'sources'/'baleen_region2_user'
PROFILE=PACKAGE/'sources'/'baleen_region2.json'
JAVA=PACKAGE/'baleen_user_java'
EXTENSION_JAR='baleen-user.jar'
GRAIN=4194304
SEED=1048596


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _index(profile):
    payload=(SOURCE/'index.json').read_bytes()
    pin=json.loads((PACKAGE/'source_pin.json').read_text())['user_sources']['baleen']['index_sha256']
    if _sha(payload)!=pin:raise ValueError('Baleen User source index checksum mismatch')
    index=json.loads(payload)
    if (index['schema']!=1 or index['grain_bytes']!=GRAIN or index['duration_us']!=600000000
            or index['slice_us']!=1000000 or len(index['rows'])!=600
            or index['source_trace_sha256']!=profile['provenance']['trace_sha256']
            or index['source_operations']!=profile['provenance']['source_operations']
            or index['key_count']!=profile['key_count']
            or index['capacity_bytes']!=profile['capacity_bytes']
            or index['source_time_range_unix_ns']!=profile['provenance']['source_time_range_unix_ns']):
        raise ValueError('Baleen User must retain the frozen Region2 full-time subset')
    for second,row in enumerate(index['rows']):
        if (row['second']!=second or row['start_us']!=second*1000000 or row['end_us']!=(second+1)*1000000
                or row['file']!=f'user_data/second_{second:03d}.bin.gz' or row['requests']<=0 or row['bytes']<=0
                or row['reads']<0 or row['writes']<0 or row['reads']+row['writes']!=row['requests']):
            raise ValueError('Baleen requires 600 nonempty native one-second slices')
    hist=profile['operation_histograms']
    if (sum(r['requests'] for r in index['rows'])!=index['requests']
            or sum(r['bytes'] for r in index['rows'])!=index['bytes']
            or sum(r['reads'] for r in index['rows'])!=index['source_operations']['GET']
            or sum(r['writes'] for r in index['rows'])!=index['source_operations']['PUT']
            or sum(s*n for h in hist.values() for s,n in h)!=index['bytes']
            or sum(n for h in hist.values() for s,n in h)!=index['requests']):
        raise ValueError('Baleen native frequency totals differ')
    return index,pin


def from_profile(profile):
    index,pin=_index(profile);size=index['capacity_bytes']
    lanes=[dict(bin='container',operation=op,weight=index['source_operations'][source],
                xfersize=deepcopy(profile['operation_histograms'][op]),fileio='random',
                fileselect='random',threads=1,file_sharing=True) for op,source in (('read','GET'),('write','PUT'))]
    phase=dict(name='native_all',seconds=600,lanes=lanes)
    provenance=deepcopy(profile['provenance'])
    provenance.update(source_file_size_rule=provenance['file_size_rule'],
        file_size_rule='one container; each selected key owns ceil(full-window observed maximum endpoint/4MiB) exclusive objects',
        source_request_bytes=profile['provenance']['source_request_bytes'],aligned_request_bytes=index['bytes'],
        source_active_objects=index['active_objects'],source_inventory_objects=index['inventory_objects'],
        source_index_sha256=pin,
        adaptation='one600sRD;600 native1s joint offset/size/read-write frequency slices;max;no per-second quota;no Zipf',
        timeline='affine map fixed full Region2 nanosecond boundaries to600s;retain every record of the selected keys;sample at preIO;no original event-order replay',
        object_mapping='(key,object_index) bijects to container object; each key starts at4MiB boundary and owns whole objects',
        preparation='standard full container format; each key owns whole4MiB objects; runtime never accesses alignment padding',
        limitation='selected records already4KiB aligned; finite max-rate samples and source-rate variation still affect whole-run frequencies and object coverage')
    return dict(id='bigdata_baleen',access_engine='user',
        bins=[dict(id='container',group='bigdata',files=1,size_bytes=size)],
        profiles={'current':[deepcopy(phase)],'baseline':[deepcopy(phase)]},
        capacity_policy=dict(kind='trace_inventory',expected_bytes=size,min_bytes=size,max_bytes=size),
        user_timeline=dict(slice_us=1000000,duration_us=600000000,grain_bytes=GRAIN,threads=64,
                           seed=SEED,request_quota=None,max_transfer_bytes=index['maximum_request'],source_index_sha256=pin),
        container_mapping=dict(key_count=index['key_count'],source_file_bytes=profile['source_file_bytes'],
            alignment_padding_bytes=size-profile['source_file_bytes'],
            observed_extent_bytes=profile['observed_extent_bytes'],
            native_unique_accessed_bytes=profile['native_unique_accessed_bytes'],
            source_active_objects=index['active_objects'],
            inventory_objects=index['inventory_objects'],key_map_sha256=index['key_map_sha256'],
            mapping_added_object_count_tv=0,mapping_added_object_byte_tv=0),provenance=provenance)


def _profile():
    payload=PROFILE.read_bytes()
    pin=json.loads((PACKAGE/'source_pin.json').read_text())['model_files']['baleen']
    if _sha(payload)!=pin['sha256']:raise ValueError('Region2 source profile checksum mismatch')
    profile=json.loads(payload)
    if (profile['schema']!=1 or profile['provenance']['region']!='Region2'
            or profile['provenance']['trace_sha256']!=pin['trace_sha256']):
        raise ValueError('unexpected Region2 source identity')
    return profile


def build():
    return from_profile(_profile())


def validate_model(model):
    expected=from_profile(_profile())
    for key in ('id','access_engine','bins','profiles','capacity_policy','user_timeline','container_mapping','provenance'):
        if model.get(key)!=expected[key]:raise ValueError('Baleen User model differs from frozen native plan: '+key)


def _runtime():
    meta=json.loads((JAVA/'runtime.json').read_text())
    if meta['stock_jar_sha256']!=JAR_SHA256:raise ValueError('Baleen User requires stock Vdbench5.04.06')
    for name,digest in meta['source_sha256'].items():
        if _sha((JAVA/name).read_bytes())!=digest:raise ValueError('Baleen User source differs from built extension')
    if _sha((JAVA/EXTENSION_JAR).read_bytes())!=meta['jar_sha256']:raise ValueError('Baleen User extension checksum mismatch')
    return meta


def timeline_text(model):
    size=model['bins'][0]['size_bytes']
    lines=[f'BALEEN_TIMELINE1\t{size}\t{GRAIN}\t600000000\t600']
    lines.extend(f'{s*1000000}\t{(s+1)*1000000}\tuser_data/second_{s:03d}.bin.gz' for s in range(600))
    return '\n'.join(lines)+'\n'


def expected_assets(model):
    index,_=_index(_profile());runtime=_runtime()
    assets={'user_data/'+Path(r['file']).name:r['sha256'] for r in index['rows']}
    assets.update({'key-map.csv':index['key_map_sha256'],EXTENSION_JAR:runtime['jar_sha256'],
                   'vdbench':_sha((JAVA/'vdbench').read_bytes()),'native.timeline':_sha(timeline_text(model).encode())})
    return assets


def materialize(model,output):
    validate_model(model);output=Path(output);index,_=_index(_profile());expected=expected_assets(model)
    sources={'user_data/'+Path(r['file']).name:SOURCE/r['file'] for r in index['rows']}
    sources.update({'key-map.csv':SOURCE/'key-map.csv',EXTENSION_JAR:JAVA/EXTENSION_JAR,'vdbench':JAVA/'vdbench'})
    # Check generated and copied destinations alike before writing anything.
    for name in expected:
        target=output/name
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p!=output.parent):
            raise ValueError('Baleen asset path must not be a symlink')
    for name,source in sources.items():
        payload=source.read_bytes()
        if _sha(payload)!=expected[name]:raise ValueError('Baleen source asset checksum mismatch: '+name)
        target=output/name
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
    (output/'native.timeline').write_text(timeline_text(model));(output/'vdbench').chmod(0o755)
    return expected


def validate_assets(model,bundle,recorded):
    expected=expected_assets(model)
    if recorded!=expected:raise ValueError('Baleen User asset inventory differs from native source')
    for name,digest in expected.items():
        path=Path(bundle)/name
        if path.is_symlink() or not path.is_file() or _sha(path.read_bytes())!=digest:
            raise ValueError('Baleen User asset missing or modified: '+name)
    # The map digest pins the complete independently audited bijection. Avoid
    # allocating705,130 dictionaries again at every metadata readiness check.
    if not os.access(Path(bundle)/'vdbench',os.X_OK):raise ValueError('Baleen launcher is not executable')


def render_profile(model,data_root,phases,rate,runtime_root):
    validate_model(model)
    if rate!='max':raise ValueError('Baleen User uses iorate=max without per-second quotas')
    size=model['bins'][0]['size_bytes'];maximum=model['user_timeline']['max_transfer_bytes']
    data_file=Path(data_root)/'container'/'vdb.1_1.dir'/'vdb_f0000.file'
    lines=['* Baleen Region2 full-time key subset;native joint time/address/size/read-write frequencies',
        'data_errors=1','messagescan=no','formatsds=no',
        f'hd=default,jvms=1,vdbench={Path(runtime_root).absolute()}',
        f'sd=baleen,lun={data_file},size={size},threads=64,openflags=o_direct,offset=0,align=4096',
        f'wd=native,sd=baleen,xfersize={maximum},seekpct=100,rdpct=50,skew=100,user=(idfsbaleen.TimelineWorkload,native.timeline,seed{SEED})',
        'rd=continuous,wd=native*,iorate=max,elapsed=600,interval=1,warmup=0']
    reads=model['provenance']['source_operations']['GET'];writes=model['provenance']['source_operations']['PUT']
    stats=[dict(name='native_all',seconds=600,fwd_count=0,wd_count=1,target_iops='max',
                read_share=reads/(reads+writes),write_share=writes/(reads+writes))]
    return '\n'.join(lines)+'\n',stats


def validate_run_text(text):
    lines=[l for l in text.splitlines() if l and not l.startswith('*')]
    if len(lines)!=7 or any(sum(l.startswith(p) for l in lines)!=1 for p in ('hd=','sd=','wd=','rd=')):
        raise ValueError('Baleen User requires one host, SD, WD and RD')
    index,_=_index(_profile());maximum=index['maximum_request']
    expected=f'wd=native,sd=baleen,xfersize={maximum},seekpct=100,rdpct=50,skew=100,user=(idfsbaleen.TimelineWorkload,native.timeline,seed{SEED})'
    if (next(l for l in lines if l.startswith('wd='))!=expected
            or next(l for l in lines if l.startswith('rd='))!='rd=continuous,wd=native*,iorate=max,elapsed=600,interval=1,warmup=0'
            or 'threads=64,openflags=o_direct,offset=0,align=4096' not in next(l for l in lines if l.startswith('sd='))):
        raise ValueError('Baleen User runtime differs from native plan')
