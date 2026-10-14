"""Business-structured workloads: native Region7 Baleen and scaled role Zipf."""
from bisect import bisect_left
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import heapq
import json
import math
from pathlib import Path

CASES = {'baleen':'bigdata_baleen', 'graphchi':'graph_graphchi_psw',
         'wrf':'hpc_wrf_continuous', 'ai_training':'ai_training_ses',
         'ai_inference':'ai_inference_ses'}
TARGET_BYTES = 400 * 2**30
ALPHA = .99
MAX_FWD = 256
MAX_BINS = 255
SOURCE_DIR = Path(__file__).with_name('sources')


def load_source(case):
    if case=='baleen':
        from .baleen import build as native_baleen
        return native_baleen()
    payload = (SOURCE_DIR/f'{case}.json').read_bytes()
    pin = json.loads(Path(__file__).with_name('source_pin.json').read_text())
    if hashlib.sha256(payload).hexdigest() != pin['model_files'][case]['sha256']:
        raise ValueError('frozen SINGLE source model hash mismatch: '+case)
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
    if case=='graphchi':
        return [('read',mode,.5,hist),('write',mode,.5,hist)]
    if case=='wrf':
        operation='read' if group=='input' or group.startswith('boundary_') else 'write'
        return [(operation,mode,1.,hist)]
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


def _zipf(source, case, target):
    inference=case=='ai_inference';inventory={}
    for b in source['bins']:
        size=b.get('logical_size_bytes',b['size_bytes'])
        count=1 if inference else b['files']
        group=inventory.setdefault(b['group'],{'count':0,'size':size})
        if group['size']!=size:raise ValueError('mixed file sizes inside a business group')
        group['count']+=count
    names=sorted(inventory)
    ns=_counts([inventory[g]['count'] for g in names],[inventory[g]['size'] for g in names],target)
    for group,count in zip(names,ns):inventory[group]['count']=count
    phases=_phases(source,case);importance=defaultdict(float)
    min_files={group:1 if inference else max(len(p['groups'].get(group,{'channels':{}})['channels'])
                                           for p in phases) for group in names}
    tv_limit=.12 if case=='ai_training' else .08 if case=='graphchi' else .06
    # Reserve attention for low-request classes as well as dominant roles;
    # their conditional heat must not be lost under a small global I/O share.
    for phase in phases:
        for group,data in phase['groups'].items():importance[group]=max(importance[group],data['weight'])
    cdfs={};ideal={}
    for group,data in inventory.items():
        values=[r**-ALPHA for r in range(1,data['count']+1)]
        total=math.fsum(values);values=[v/total for v in values];prefix=[0.]
        for value in values:prefix.append(prefix[-1]+value)
        cdfs[group]=(prefix,[-v for v in values]);ideal[group]=values
    def error(group,a,b):
        prefix,negative=cdfs[group];mean=(prefix[b]-prefix[a])/(b-a)
        pivot=bisect_left(negative,-mean,a,b)
        return max(0.,prefix[pivot]-prefix[a]-(pivot-a)*mean)
    parts=set();queue=[]
    def add(group,a,b):
        parts.add((group,a,b))
        minimum=min_files[group]
        if b-a<2*minimum:return
        current=error(group,a,b)
        gain,cut=max((current-error(group,a,k)-error(group,k,b),k)
                     for k in range(a+minimum,b-minimum+1))
        floor=.075 if case=='ai_training' else .05
        if gain>1e-15:heapq.heappush(queue,(-gain*max(importance[group], floor),group,a,b,cut))
    for group in names:add(group,0,inventory[group]['count'])
    active=[sum(len(g['channels']) for g in phase['groups'].values()) for phase in phases]
    group_error={group:error(group,0,inventory[group]['count']) for group in names}
    while len(parts)<MAX_BINS and queue:
        # Fixed-size training channels consume different numbers of FWDs.
        # Meet each class's conditional error bound before spending the rest
        # on the global weighted error; a tiny class must not lose its budget.
        candidates=[]
        if case=='ai_training':
            for index,item in enumerate(queue):
                _,g,_,_,_=item
                if group_error[g]<=tv_limit:continue
                if all(active[i]+len(p['groups'][g]['channels'])<=MAX_FWD
                       for i,p in enumerate(phases) if g in p['groups']):
                    candidates.append((group_error[g],-item[0],index))
        if candidates:
            index=max(candidates)[2];item=queue[index]
            queue[index]=queue[-1];queue.pop();heapq.heapify(queue)
            _,group,a,b,cut=item
        else:
            _,group,a,b,cut=heapq.heappop(queue)
        touched=[i for i,p in enumerate(phases) if group in p['groups']]
        if any(active[i]+len(phases[i]['groups'][group]['channels'])>MAX_FWD for i in touched):continue
        for i in touched:active[i]+=len(phases[i]['groups'][group]['channels'])
        group_error[group]+=error(group,a,cut)+error(group,cut,b)-error(group,a,b)
        parts.remove((group,a,b));add(group,a,cut);add(group,cut,b)
    bins=[];masses={};errors=defaultdict(float)
    for i,(group,a,b) in enumerate(sorted(parts)):
        name=f'zipf_{i:03d}';n=b-a;logical_size=inventory[group]['size']
        item={'id':name,'group':group,'files':n,'size_bytes':logical_size,
              'rank_start':a+1,'rank_end':b}
        if inference:
            item.update(logical_files=n,logical_size_bytes=logical_size,
                        files=4*n,size_bytes=logical_size//4,fragments_per_logical_file=4,
                        fragment_mapping='logical local index j maps to physical file indices 4*j through 4*j+3; four equal contiguous ranges')
        bins.append(item);prefix,_=cdfs[group];masses[name]=prefix[b]-prefix[a]
        errors[group]+=error(group,a,b)
    profiles={};audits=[]
    for profile in ('baseline','current'):
        output=[];start=0
        for phase in phases:
            lanes=[]
            for b in bins:
                if b['group'] not in phase['groups']:continue
                data=phase['groups'][b['group']]
                count=b.get('logical_files',b['files'])
                mass=masses[b['id']] if profile=='current' else count/inventory[b['group']]['count']
                for (operation,mode,_),channel in sorted(data['channels'].items()):
                    hist=sorted(channel['hist'].items());xfer=hist[0][0] if len(hist)==1 else [list(p) for p in hist]
                    lanes.append({'bin':b['id'],'operation':operation,'weight':data['weight']*mass*channel['share'],
                                  'xfersize':xfer,'fileio':mode,'fileselect':'random','threads':1})
            output.append({'name':phase['name'],'seconds':phase['seconds'],'start_s':start,
                           'end_s':start+phase['seconds'],'rate_multiplier':1,'lanes':lanes})
            if profile=='current':
                tv=math.fsum(data['weight']*errors[g] for g,data in phase['groups'].items())
                if tv>tv_limit:raise ValueError('Zipf bin approximation exceeds limit: '+phase['name'])
                audits.append({'name':phase['name'],'tv_distance':tv,'fwd_count':len(lanes),
                               'group_weights':{g:d['weight'] for g,d in phase['groups'].items()}})
            start+=phase['seconds']
        profiles[profile]=output
    if max(errors.values())>tv_limit:raise ValueError('conditional group Zipf error exceeds limit: '+str(dict(errors)))
    return {'id':source['id'],'bins':bins,'profiles':profiles,
            'phase_zipf':{'alpha':ALPHA,'scope':'fixed ranks within original business group or logical size class',
                          'phases':audits,'group_tv_distances':dict(errors),'max_fwd_per_rd':MAX_FWD,
                          'max_bins':MAX_BINS,'tv_limit':tv_limit,'min_files_per_group':min_files},
            'fragmentation':{'fragments_per_logical_file':4,
                             'logical_files':sum(b['logical_files'] for b in bins),
                             'limitation':'multiple logical files may share a bucket; uniform physical-file selection approximates their Zipf ranks'} if inference else None}


def build(case, target_bytes=TARGET_BYTES):
    if isinstance(target_bytes,bool) or not isinstance(target_bytes,int) or target_bytes<=0:
        raise ValueError('target_bytes must be a positive integer')
    if case=='baleen':
        if target_bytes!=TARGET_BYTES:
            raise ValueError('Baleen keeps the trace-native capacity; custom expansion targets are unsupported')
        return load_source(case)
    source=load_source(case);original=_capacity(source)
    if target_bytes<=original:raise ValueError('expansion target must exceed source capacity')
    model=_zipf(source,case,target_bytes)
    model['id']=CASES[case]
    pin=json.loads(Path(__file__).with_name('source_pin.json').read_text())
    model['provenance']=deepcopy(source['provenance'])
    model['provenance']['source_write_to_read']=False
    model['provenance']['operation_mapping']='preserve source reads and writes; overwrite prepared synthetic files'
    if case=='graphchi':
        model['provenance']['write_mapping']='Full-update PSW template: active-edge load is read, writeback is write, equal request shares on the same file inventory; concurrent statistical channels, not call-order replay.'
    elif case=='wrf':
        model['provenance']['write_mapping']='input/boundary are read; history/restart are write at their output events'
    else:
        model['provenance']['adaptation']='SES 1.2.0 template-based storage workload with original read/write shares and added conditional Zipf; not standard SES certification'
    model['provenance']['idfs_expansion']={'source_commit':pin['commit'],
        'source_model_sha256':pin['model_files'][case]['sha256'],'original_bytes':original,
        'target_bytes':target_bytes,'actual_bytes':_capacity(model),
        'method':'increase logical file counts; keep logical size classes, timing, business shares and source read/write types; rebuild conditional Zipf bins',
        'baleen_method':None,
        'runtime':'unmodified Vdbench 5.04.07; integer transfer percentages; no SES framework',
        'coverage':'600s timed access, not complete scans; generated probabilities do not guarantee measured object heat or classifier metrics'}
    if not .98*target_bytes<=_capacity(model)<=1.02*target_bytes:
        raise ValueError('file-count rounding puts capacity more than 2% from target')
    return model
