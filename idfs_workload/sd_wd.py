"""Stock SD/WD rendering on the same regular files prepared by FSD/FWD."""
from pathlib import Path
from .core import percentages


def emitted_histogram(lane):
    hist=lane['xfersize']
    pairs=hist if isinstance(hist,list) else [[hist,1]]
    return [(size,pct) for (size,_),pct in zip(pairs,percentages([n for _,n in pairs]))]


def validate_lane(lane, file_size):
    fields=[lane.get(k) for k in ('offset_bytes','range_end_bytes','align_bytes')]
    if any(isinstance(v,bool) or not isinstance(v,int) or v<0 or v%4096 for v in fields):
        raise ValueError('SD/WD offset/range/alignment must be nonnegative 4KiB multiples')
    low,high,align=fields
    maximum=max(size for size,pct in emitted_histogram(lane))
    if high>file_size or high%maximum or high-low<2*maximum:
        raise ValueError('SD/WD range must fit file, survive max-xfer rounding, and span at least two maximum transfers')
    if align and (high-low-maximum)//align<1:
        raise ValueError('SD/WD range has no valid aligned starting slot')


def render_profile(model, data_root, phases, rate):
    lines=['* Stock Vdbench SD/WD on prepared regular files; native spatial-frequency approximation',
           'data_errors=1','messagescan=no','formatsds=no','hd=default,jvms=1']
    bins={b['id']:b for b in model['bins']}
    sds=[];wds=[];rds=[];reports=[]
    for i,phase in enumerate(phases):
        prefix=f'p{i:03d}_'
        skews=percentages([l['weight'] for l in phase['lanes']])
        for j,(lane,skew) in enumerate(zip(phase['lanes'],skews)):
            name=f'{prefix}{j:04d}';b=bins[lane['bin']]
            path=Path(data_root)/lane['bin']/'vdb.1_1.dir/vdb_f0000.file'
            align=f",align={lane['align_bytes']}" if lane['align_bytes'] else ''
            sds.append(f"sd=s_{name},lun={path},size={b['size_bytes']},threads=1,openflags=o_direct,offset={lane['offset_bytes']}{align}")
            hist=emitted_histogram(lane)
            xfer='('+','.join(f'{size},{pct}' for size,pct in hist)+')'
            rdpct=100 if lane['operation']=='read' else 0
            wds.append(f"wd={name},sd=s_{name},range=(0,{lane['range_end_bytes']}),xfersize={xfer},seekpct=100,rdpct={rdpct},skew={skew}")
        target='max' if rate=='max' else format(rate*phase.get('rate_multiplier',1),'.12g')
        rds.append(f"rd={prefix}{phase['name']},wd={prefix}*,iorate={target},elapsed={phase['seconds']},interval=1,warmup=0")
        total=sum(l['weight'] for l in phase['lanes'])
        read=sum(l['weight'] for l in phase['lanes'] if l['operation']=='read')/total
        reports.append(dict(name=phase['name'],seconds=phase['seconds'],fwd_count=0,
            wd_count=len(phase['lanes']),target_iops=target,read_share=read,write_share=1-read))
    return '\n'.join(lines+sds+wds+rds)+'\n',reports


def validate_run_text(text, *, max_channels=256):
    # Exact regeneration in core.validate_bundle is the primary integrity gate.
    from .validate_vdbench import _named_lines, _parameter, ConfigValidationError
    from decimal import Decimal
    sds=_named_lines(text,'sd=');wds=_named_lines(text,'wd=');rds=_named_lines(text,'rd=')
    if type(max_channels) is not int or max_channels not in (256,512):
        raise ConfigValidationError('SD/WD global channel budget must be 256 or 512')
    if not sds or not wds or not rds or len(wds)>max_channels or len(sds)>max_channels:
        raise ConfigValidationError(f'SD/WD inventory must have 1..{max_channels} channels')
    if 'formatsds=no' not in text.splitlines() or 'fwd=' in text or 'fsd=' in text:
        raise ConfigValidationError('SD/WD run must disable automatic preparation and contain no FWD/FSD')
    selected=set();used_sds=set();elapsed=0
    for line in rds.values():
        selector=_parameter(line,'wd')
        if not selector.endswith('*') or selector.count('*')!=1:
            raise ConfigValidationError('SD/WD RD must use one prefix wildcard')
        names=[n for n in wds if n.startswith(selector[:-1])]
        if not names or len(names)>256:
            raise ConfigValidationError('SD/WD phase must select 1..256 channels')
        if sum(Decimal(_parameter(wds[n],'skew')) for n in names)!=100:
            raise ConfigValidationError('SD/WD skew total must be 100')
        selected.update(names)
        seconds=int(_parameter(line,'elapsed'))
        if seconds<=0:raise ConfigValidationError('SD/WD phase duration must be positive')
        elapsed+=seconds
    for line in wds.values():
        sd=_parameter(line,'sd')
        if sd not in sds:raise ConfigValidationError('undefined SD')
        used_sds.add(sd)
    if selected!=set(wds) or used_sds!=set(sds) or elapsed!=600:
        raise ConfigValidationError('unused SD/WD or duration not 600 seconds')
