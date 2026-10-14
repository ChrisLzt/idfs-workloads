"""Region7 native-frequency snapshot from real trace keys; no file replication."""
from bisect import bisect_left
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import heapq
import json
import math
from pathlib import Path

PROFILE=Path(__file__).with_name('sources')/'baleen_region7.json'
FILE_ALIGNMENT=2**21
IO_ALIGNMENT=4096
TRACE_URL='https://ftp.pdl.cmu.edu/pub/datasets/Baleen24/storage/20230325/Region7/full_0_0.1.trace'
TRACE_SHA256='019ba3162716d6d3623df7bfd69be206ae00aff054d521933d102eee23a486a2'


def parse_trace(path, *, trace_url=TRACE_URL):
    """Extract whole-key extent, GET/PUT multiplicity and aligned I/O histograms."""
    path=Path(path);columns=None;keys={};rows=ignored=zero_size=0
    get=put=raw_bytes=aligned_bytes=0;t0=t1=None;sha=hashlib.sha256()
    with path.open('rb') as stream:
        for raw in stream:
            sha.update(raw)
            if not raw.strip():continue
            if raw.startswith(b'#'):
                header=raw.decode().lstrip('# ').split()
                if header and header[0]=='block_id':
                    if columns is not None and header!=columns:raise ValueError('inconsistent trace header')
                    columns=header
                continue
            if columns is None:raise ValueError('missing Region7 trace header')
            required={'block_id','rs_shard_id','io_offset','io_size','op_time','op_name','op_count'}
            if not required.issubset(columns):raise ValueError('trace must include Region7 shard identity and op_count')
            fields=raw.split()
            if len(fields)!=len(columns):raise ValueError('trace field count differs from header')
            entry=dict(zip(columns,fields));rows+=1
            op=int(entry['op_name'])
            if op not in (1,2,3,4,5,6):ignored+=1;continue
            off,length,n=(int(entry[k]) for k in ('io_offset','io_size','op_count'))
            when=float(entry['op_time'])
            if off<0 or length<0 or n<=0 or not math.isfinite(when):raise ValueError('invalid trace request')
            if length==0:zero_size+=1;continue
            identity=(int(entry['block_id']),int(entry['rs_shard_id']))
            key=keys.setdefault(identity,{'extent':0,'ops':0,'GET':0,'PUT':0,'hist':Counter(),
                                         'get_hist':Counter(),'put_hist':Counter()})
            aligned=((off+length+IO_ALIGNMENT-1)//IO_ALIGNMENT-off//IO_ALIGNMENT)*IO_ALIGNMENT
            key['extent']=max(key['extent'],off+length);key['ops']+=n;key['hist'][aligned]+=n
            if op in (1,2,5):key['GET']+=n;get+=n;key['get_hist'][aligned]+=n
            else:key['PUT']+=n;put+=n;key['put_hist'][aligned]+=n
            raw_bytes+=length*n;aligned_bytes+=aligned*n
            t0=when if t0 is None else min(t0,when);t1=when if t1 is None else max(t1,when)
    if not keys:raise ValueError('trace contains no usable data requests')
    packed=[[block,shard,data['extent'],data['ops'],data['GET'],data['PUT'],
             [list(pair) for pair in sorted(data['hist'].items())],
             [list(pair) for pair in sorted(data['get_hist'].items())],
             [list(pair) for pair in sorted(data['put_hist'].items())]] for (block,shard),data in sorted(keys.items())]
    inventory=sum(((k[2]+FILE_ALIGNMENT-1)//FILE_ALIGNMENT)*FILE_ALIGNMENT for k in packed)
    return {'schema':2,'key_columns':['block_id','rs_shard_id','extent_bytes','operations','GET','PUT','aligned_xfer_histogram',
                                    'get_aligned_xfer_histogram','put_aligned_xfer_histogram'],
            'keys':packed,'inventory_bytes':inventory,
            'provenance':{'region':'Region7','trace_url':trace_url,'trace_sha256':sha.hexdigest(),
                          'source_size_bytes':path.stat().st_size,'source_rows':rows,
                          'ignored_opcode_rows':ignored,'zero_size_rows':zero_size,
                          'key_identity':'(block_id, rs_shard_id)','source_operations':{'GET':get,'PUT':put},
                          'source_time_range_unix':[t0,t1],'source_request_bytes':raw_bytes,
                          'aligned_request_bytes':aligned_bytes,
                          'operation_mapping':'GET becomes read; PUT becomes write; retain op_count multiplicity',
                          'file_size_rule':'one file per source key; ceil(max(io_offset+io_size)/2MiB)*2MiB',
                          'transfer_alignment':'cover each source I/O byte range with 4KiB-aligned span'}}


def load_profile():
    payload=PROFILE.read_bytes()
    pin=json.loads(Path(__file__).with_name('source_pin.json').read_text())['model_files']['baleen']
    if hashlib.sha256(payload).hexdigest()!=pin['sha256']:raise ValueError('Region7 profile hash mismatch')
    profile=json.loads(payload)
    if profile['schema']!=2:raise ValueError('Region7 profile must preserve operation-specific transfer histograms')
    if profile['provenance']['trace_sha256']!=TRACE_SHA256:raise ValueError('unexpected Region7 trace hash')
    return profile


def from_profile(profile, *, max_bins=255):
    """Greedy native-frequency partition within file-size strata, preserving all keys."""
    if not isinstance(max_bins,int) or isinstance(max_bins,bool) or not 1<=max_bins<=255:
        raise ValueError('max_bins must be within 1..255')
    keys=profile['keys'];total=sum(k[3] for k in keys);by_size=defaultdict(list)
    for i,key in enumerate(keys):
        size=((key[2]+FILE_ALIGNMENT-1)//FILE_ALIGNMENT)*FILE_ALIGNMENT
        # Pure GET/PUT keys need one channel; mixed keys need two. Keeping the
        # operation sets separate avoids spending two FWDs on pure-key tails.
        by_size[(size,bool(key[4]),bool(key[5]))].append(i)
    if len(by_size)>max_bins:raise ValueError('file-size classes exceed bin budget')
    arrays={}
    for size,indices in by_size.items():
        indices.sort(key=lambda i:(-keys[i][3],keys[i][0],keys[i][1]))
        values=[keys[i][3] for i in indices];prefix=[0]
        for v in values:prefix.append(prefix[-1]+v)
        arrays[size]=(indices,prefix,[-v for v in values])
    def error(size,a,b):
        _,prefix,negative=arrays[size];mean=(prefix[b]-prefix[a])/(b-a)
        pivot=bisect_left(negative,-mean,a,b)
        return max(0.,prefix[pivot]-prefix[a]-(pivot-a)*mean)
    parts=set();queue=[]
    def add(size,a,b):
        parts.add((size,a,b))
        if b-a<2:return
        current=error(size,a,b)
        gain,cut=max((current-error(size,a,k)-error(size,k,b),k) for k in range(a+1,b))
        cost=int(size[1])+int(size[2])
        if gain>1e-10:heapq.heappush(queue,(-gain/cost,size,a,b,cut))
    for size,indices in sorted(by_size.items()):add(size,0,len(indices))
    fwd_count=sum(int(size[1])+int(size[2]) for size in by_size)
    if fwd_count>256:raise ValueError('initial size/operation classes exceed FWD budget')
    while len(parts)<max_bins and queue:
        _,size,a,b,cut=heapq.heappop(queue);cost=int(size[1])+int(size[2])
        if fwd_count+cost>256:continue
        fwd_count+=cost
        parts.remove((size,a,b));add(size,a,cut);add(size,cut,b)
    bins=[];lanes=[];tv=0.;op_error=Counter()
    op_totals={'read':sum(k[4] for k in keys),'write':sum(k[5] for k in keys)}
    for index,(size,a,b) in enumerate(sorted(parts)):
        indices=arrays[size][0][a:b]
        name=f'native_{index:03d}'
        bins.append({'id':name,'group':f'size_{size[0]}','size_bytes':size[0],'files':len(indices),
                     'source_key_indices':indices})
        mixed=any(keys[i][4] for i in indices) and any(keys[i][5] for i in indices)
        for operation,count_col,hist_col in (('read',4,7),('write',5,8)):
            weight=sum(keys[i][count_col] for i in indices)
            if not weight:continue
            hist=Counter()
            for i in indices:
                for xfer,n in keys[i][hist_col]:hist[xfer]+=n
            if sum(hist.values())!=weight:raise ValueError('key histogram operation count mismatch')
            lane={'bin':name,'operation':operation,'weight':weight,'xfersize':[list(p) for p in sorted(hist.items())],
                  'fileio':'random','fileselect':'random','threads':1}
            if mixed and len(indices)==1:lane['file_sharing']=True
            lanes.append(lane)
            op_error[operation]+=sum(abs(keys[i][count_col]-weight/len(indices)) for i in indices)/2
        tv+=error(size,a,b)/total
    if tv>.04:raise ValueError(f'native per-key probability approximation exceeds 4%: {tv}')
    capacity=sum(b['files']*b['size_bytes'] for b in bins)
    if capacity!=profile['inventory_bytes']:raise ValueError('trace inventory capacity mismatch')
    phase={'name':'native_all','seconds':600,'rate_multiplier':1,'lanes':lanes}
    provenance=deepcopy(profile['provenance'])
    provenance.update(adaptation='one 600s native-frequency snapshot with separate GET/read and PUT/write channels; no Zipf or replicated keys',
                      histogram_adaptation='exact operation-specific aggregation of source-key aligned transfer histograms into each bin; rendered percentages rounded to integers',
                      timeline='temporal order and original offsets are not replayed; random aligned reads and writes on synthetic content')
    return {'id':'bigdata_baleen','bins':bins,'profiles':{'baseline':[phase],'current':[deepcopy(phase)]},
            'provenance':provenance,'capacity_policy':{'kind':'trace_inventory','expected_bytes':capacity,
                'min_bytes':300*2**30,'max_bytes':500*2**30},
            'static_native':{'source_key_count':len(keys),'source_operations':total,
                'native_tv':tv,'native_tv_limit':.04,
                'operation_file_tv':{op:op_error[op]/n if n else 0. for op,n in op_totals.items()},
                'top_key_operations':max(k[3] for k in keys),
                'top_key_share':max(k[3] for k in keys)/total,'max_fwd_per_rd':256,
                'read_only':False,'default_rate':'max','scope':'one physical file per actual trace key; uniform selection inside each frequency bin and operation channel',
                'membership':'source_key_indices maps Vdbench file index to Region7 profile key index',
                'coverage':'configured request frequency approximation; not measured file/object heat'}}


def build():
    return from_profile(load_profile())
