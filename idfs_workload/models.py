"""Baleen, shared graph Zipf, NCAR-derived WRF and SES workloads."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path

CASES = {'baleen':'bigdata_baleen', 'graph':'graph_zipf',
         'wrf':'hpc_wrf', 'ai_training':'ai_training_ses',
         'ai_inference':'ai_inference_ses'}
TARGET_BYTES = 400 * 2**30
AI_TARGET_BYTES = {'ai_training': 4400 * 2**30, 'ai_inference': 9 * 2**39}
ALPHA = .99
MAX_FWD = 256
MAX_BINS = 255
SOURCE_DIR = Path(__file__).with_name('sources')


def load_source(case):
    if case=='wrf':
        from .wrf_user import build as synthetic_wrf
        return synthetic_wrf()
    if case=='baleen':
        from .baleen_user import build as native_baleen
        return native_baleen()
    if case not in AI_TARGET_BYTES:
        raise ValueError('unsupported source workload: '+case)
    payload = (SOURCE_DIR/f'{case}.json').read_bytes()
    pin = json.loads(Path(__file__).with_name('source_pin.json').read_text())
    if hashlib.sha256(payload).hexdigest() != pin['model_files'][case]['sha256']:
        raise ValueError('frozen source model hash mismatch: '+case)
    return json.loads(payload)


def _capacity(model):
    return sum(b['files']*b['size_bytes'] for b in model['bins'])


def _counts(counts, sizes, target):
    """Preserve size classes. Equal-size inventories use exact total counts."""
    original = sum(n*s for n,s in zip(counts,sizes))
    factor = Decimal(target)/original
    quotas = [Decimal(n)*factor for n in counts]
    if len(set(sizes)) == 1:
        total = int((Decimal(target)/sizes[0]).to_integral_value(rounding=ROUND_HALF_UP))
        result = [max(1,int(q)) for q in quotas]
        while sum(result) < total:
            i=max(range(len(result)),key=lambda i:quotas[i]-result[i]);result[i]+=1
        while sum(result) > total:
            i=max((i for i in range(len(result)) if result[i]>1),key=lambda i:result[i]-quotas[i]);result[i]-=1
        return result
    return [max(1,int(q.to_integral_value(rounding=ROUND_HALF_UP))) for q in quotas]


def _source_channels(case, group, lane):
    """Restore source operations before allocating the FWD/bin budget."""
    mode=lane['fileio'];hist=lane['xfersize']
    if case=='ai_training':
        if int(group.removeprefix('size_'))<=131072:
            return [('read',mode,1/3,8192),('read',mode,1/3,32768),
                    ('read',mode,1/6,65536),
                    ('write',mode,1/6,65536)]
        return [('read',mode,.5,1048576),('write',mode,.5,1048576)]
    if case=='ai_inference':
        if mode=='sequential':
            return [('read',mode,2/3,hist),('write',mode,1/3,hist)]
        return [('read',mode,1.,hist)]
    raise ValueError('unknown source I/O case: '+case)


def _phases(source, case):
    """Collapse source lanes to business group/channel shares and transfer mixes."""
    lookup={b['id']:b for b in source['bins']};output=[]
    for phase in source['profiles']['current']:
        groups={}
        for lane in phase['lanes']:
            group=lookup[lane['bin']]['group']
            data=groups.setdefault(group,{'weight':0.,'channels':{}})
            data['weight']+=lane['weight']
            for operation,mode,share,xfers in _source_channels(case,group,lane):
                # SES training uses fixed-size sequential FWDs. Merging them
                # creates short, non-template requests at small-file EOF.
                tag=xfers if case=='ai_training' else 0
                channel=data['channels'].setdefault((operation,mode,tag),{'weight':0.,'hist':defaultdict(float)})
                mass=lane['weight']*share;channel['weight']+=mass
                hist=xfers if isinstance(xfers,list) else [[xfers,1]]
                total=math.fsum(w for _,w in hist)
                for size,weight in hist:channel['hist'][size]+=mass*weight/total
        total=math.fsum(g['weight'] for g in groups.values())
        for data in groups.values():
            for channel in data['channels'].values():
                channel['share']=channel['weight']/data['weight']
                z=math.fsum(channel['hist'].values())
                channel['hist']={size:v/z for size,v in channel['hist'].items()}
            data['weight']/=total
        output.append({'name':phase['name'],'seconds':phase['seconds'],'groups':groups})
    return output


def build(case, target_bytes=None):
    if case not in CASES:
        raise ValueError('unsupported workload: '+case)
    if target_bytes is None:
        target_bytes = AI_TARGET_BYTES.get(case, TARGET_BYTES)
    if isinstance(target_bytes,bool) or not isinstance(target_bytes,int) or target_bytes<=0:
        raise ValueError('target_bytes must be a positive integer')
    if case=='graph':
        if target_bytes!=TARGET_BYTES:
            raise ValueError('graph uses a fixed existing4TiB file;custom expansion is unsupported')
        from .graph_zipf import build as existing_graph
        return existing_graph()
    if case in ('baleen','wrf'):
        if target_bytes!=TARGET_BYTES:
            raise ValueError(case+' keeps the trace-native or source-derived capacity; custom expansion targets are unsupported')
        return load_source(case)
    if case in ('ai_training', 'ai_inference'):
        from .ai_user import build as container_ai
        return container_ai(case, target_bytes)
    raise ValueError('unsupported workload: '+case)
