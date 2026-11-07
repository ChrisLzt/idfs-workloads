"""Read-only data/model verification for the archived seed1048596 workloads."""
from pathlib import Path
from collections import defaultdict
from bisect import bisect_right
import argparse,csv,gzip,hashlib,json,math,re,struct

G=4194304
SEED=1048596
CONTRACT={
 'bigdata_baleen':dict(capacity=5497553944576,keys=705130,reads=3925006,writes=645808,active=1308572),
 'graph_zipf':dict(capacity=4398046511104,files=1,read=1.0),
 'hpc_wrf':dict(capacity=3417015582720,files=42),
 'ai_training_ses':dict(capacity=6214285000704,files=382965,ranges=382965,payload=4722360451072,read=.7,
     categories={65536:230934,131072:113740,1048576:19151,2097152:7656,8388608:3828,134217728:3828,1073741824:3828},
     mix={('read',8192):.2,('read',32768):.2,('read',65536):.1,('write',65536):.1,('read',1048576):.2,('write',1048576):.2}),
 'ai_inference_ses':dict(capacity=4947802324992,files=4007,ranges=16028,payload=4947802324992,read=.8,
     categories={536870912:1202,1073741824:1603,2147483648:1202},
     mix={('read',65536):.4,('read',131072):.4,('write',65536):.1,('write',131072):.1}),
}
HEADER=struct.Struct('>iiqiiqqiqq')
RECORD=struct.Struct('>qiIq')

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def inside(root,relative):
    p=(root/relative).resolve()
    if root!=p and root not in p.parents:raise ValueError('archive path escapes its root')
    return p

def verify(root,check_index=True):
    root=Path(root).resolve();checks=[];metrics={}
    def add(key,label,expected,actual,ok):
        checks.append(dict(id=key,item=label,expected=expected,actual=actual,status='PASS' if ok else 'FAIL'))
    try:
        base=root/'datasets/model';model=json.loads((base/'model.json').read_text());manifest=json.loads((base/'manifest.json').read_text())
        case=model['id'];spec=CONTRACT[case];capacity=manifest['capacity_bytes']
        seed=model['user_zipf']['mapping_seed'] if case=='graph_zipf' else model['user_timeline']['seed']
        configs=[(base/f'run_{p}.vdb').read_text() for p in ('current','baseline')]
        seed_ok=seed==SEED and all(f'seed{SEED}' in x for x in configs)
        add('seed','访问生成种子','1048596',str(seed),seed_ok)
        objects=capacity//G
        add('capacity','载体逻辑容量与对象库存',f'{spec["capacity"]:,} B；{spec["capacity"]//G:,}个对象位置',
            f'{capacity:,} B；{objects:,}个对象位置',capacity==spec['capacity'] and capacity%G==0 and manifest['file_count']==1)
        runtime_ok=all(sum(s.startswith('rd=') for s in x.splitlines())==1 and 'iorate=max,elapsed=600' in x and 'openflags=o_direct' in x for x in configs)
        add('runtime','运行窗口、速率和访问方式','单个600秒运行；max；O_DIRECT','配置已逐项核对',runtime_ok)
        hash_bad=[]
        for name,h in manifest['config_sha256'].items():
            if sha(inside(base,name))!=h:hash_bad.append(name)
        for name,h in manifest['asset_sha256'].items():
            if sha(inside(base,name))!=h:hash_bad.append(name)
        if sha(base/'model.json')!=manifest['model_sha256']:hash_bad.append('model.json')
        add('bundle_hashes','模型、配置与运行资产摘要','摘要不匹配0项',f'摘要不匹配{len(hash_bad)}项',not hash_bad)

        if case=='bigdata_baleen':
            bases=[];ends=[];ids=[];previous=0;last_id=-1;invalid=0
            with (base/'key-map.csv').open() as f:
                reader=csv.DictReader(f)
                assert reader.fieldnames==['block_id','rs_shard_id','base_bytes','extent_bytes','file_bytes','slot_bytes']
                for r in reader:
                    k,b,e,n,s=(int(r[v]) for v in ('block_id','base_bytes','extent_bytes','file_bytes','slot_bytes'))
                    if k<=last_id or int(r['rs_shard_id'])!=0 or b!=previous or b%G or not 0<e<=n<=s or n%4096 or s!=(n+G-1)//G*G:invalid+=1
                    bases.append(b);ends.append(b+n);ids.append(k);previous=b+s;last_id=k
            add('key_map','键与独占对象区间映射',f'{spec["keys"]:,}个键；末端{capacity:,} B；异常0项',
                f'{len(ids):,}个键；末端{previous:,} B；异常{invalid}项',len(ids)==spec['keys'] and previous==capacity and invalid==0)
            with gzip.open(root/'datasets/source/selected-keys.csv.gz','rt') as f:
                reader=csv.DictReader(f);chosen=[];chosen_reads=chosen_writes=0
                for r in reader:
                    chosen.append(int(r['block_id']));chosen_reads+=int(r['native_reads']);chosen_writes+=int(r['native_writes'])
            add('source_keys','来源键清单与计数','键集合一致；读取3,925,006次、写入645,808次',
                f'{len(chosen):,}个键；读取{chosen_reads:,}次、写入{chosen_writes:,}次',
                chosen==ids and chosen_reads==spec['reads'] and chosen_writes==spec['writes'])
            index=json.loads((root/'tools/idfs_workload/sources/baleen_region2_user/index.json').read_text())
            rows=(base/'native.timeline').read_text().splitlines();head=rows[0].split('\t')
            timeline_ok=head==['BALEEN_TIMELINE1',str(capacity),str(G),'600000000','600'] and len(rows)==601
            reads=writes=total_bytes=entries=bad=0;seen=bytearray(objects)
            for second,line in enumerate(rows[1:]):
                lo,hi,name=line.split('\t');timeline_ok=timeline_ok and (int(lo),int(hi))==(second*1000000,(second+1)*1000000)
                payload=gzip.decompress(inside(base,name).read_bytes());h=HEADER.unpack_from(payload)
                magic,version,c,g,sec,n,nb,nr,rr,ww=h
                assert (magic,version,c,g,sec)==(0x42465231,1,capacity,G,second)
                assert len(payload)==HEADER.size+nr*RECORD.size
                tr=tw=tb=0
                for off,size,op,count in RECORD.iter_unpack(payload[HEADER.size:]):
                    position=bisect_right(bases,off)-1
                    valid=position>=0 and count>0 and size>0 and off%4096==0 and size%4096==0 and off+size<=ends[position] and op in (1,2)
                    if not valid:bad+=1;continue
                    if op==1:tr+=count
                    else:tw+=count
                    tb+=size*count
                    first,last=off//G,(off+size-1)//G
                    seen[first:last+1]=b'\1'*(last-first+1)
                assert (tr,tw,tb,tr+tw)==(rr,ww,nb,n)
                ref=index['rows'][second];assert (tr,tw,tb,nr)==(ref['reads'],ref['writes'],ref['bytes'],ref['entries'])
                reads+=tr;writes+=tw;total_bytes+=tb;entries+=nr
            add('native_counts','600片原生频率与请求计数','600片；读取3,925,006、写入645,808；越界0项',
                f'{len(rows)-1}片；读取{reads:,}、写入{writes:,}；越界{bad}项',timeline_ok and reads==spec['reads'] and writes==spec['writes'] and bad==0)
            active=sum(seen)
            add('native_active','源窗口实际涉及的模型对象位置',f'{spec["active"]:,}个',f'{active:,}个',active==spec['active'])
            metrics.update(keys=len(ids),source_requests=reads+writes,source_reads=reads,source_writes=writes,
                           source_bytes=total_bytes,frequency_entries=entries,source_active_objects=active)
        elif case=='graph_zipf':
            g=model['user_zipf']
            ok=g['object_count']==objects and g['object_bytes']==G and g['transfer_bytes']==32 and g['alpha']==.99 and g['request_quota'] is None
            add('graph_model','全局对象热度与读取参数','1,048,576个对象；32 B读取；Zipf(0.99)',
                f'{g["object_count"]:,}个对象；{g["transfer_bytes"]} B读取；指数{g["alpha"]}',ok)
            metrics.update(physical_files=1,read_probability=1,transfer_bytes=32,alpha=.99)
        else:
            map_path=base/'file-map.csv';invalid=0;previous=0;rows=0;valid_bytes=0
            if case=='hpc_wrf':
                expected=model['logical_files'];templates=0
                with map_path.open() as f:
                    reader=csv.DictReader(f)
                    assert reader.fieldnames==['id','member','role','source_name','template_bytes','extent_bytes','base_bytes','slot_bytes','rank_seed','size_basis']
                    for r in reader:
                        e=expected[rows]
                        for k in reader.fieldnames:
                            if str(e[k])!=r[k]:invalid+=1
                        b,s,n=(int(r[k]) for k in ('base_bytes','slot_bytes','extent_bytes'))
                        if b!=previous or b%G or s%G or not 0<n<=s:invalid+=1
                        previous=b+s;valid_bytes+=n;templates+=int(r['template_bytes']);rows+=1
                add('file_map','逻辑文件与对象映射','42行；末端等于载体容量；异常0项',f'{rows}行；末端{previous:,} B；异常{invalid}项',rows==42 and previous==capacity and invalid==0)
                phases=model['business_phases'];want=[('startup',0,60),('history_00h',60,150),('checkpoint_05h',150,510),('history_06h',510,600)]
                actual=[(p['name'],p['start_s'],p['end_s']) for p in phases]
                add('phases','业务阶段与时间覆盖','0–60、60–150、150–510、510–600秒',
                    '；'.join(f'{p[1]}–{p[2]}秒' for p in actual),actual==want)
                add('wrf_zipf','活动文件对象分布','各文件Zipf(0.99)；1秒规则检查；配额关闭',
                    f'指数{model["user_timeline"]["alpha"]}；片长{model["user_timeline"]["slice_us"]}微秒',
                    model['user_timeline']['alpha']==.99 and model['user_timeline']['slice_us']==1000000 and model['user_timeline']['request_quota'] is None)
                probs=[]
                for p in phases:
                    total=math.fsum(x['weight'] for x in p['channels']);read=math.fsum(x['weight'] for x in p['channels'] if x['operation']=='read')
                    probs.append(dict(phase=p['name'],read_probability=read/total,write_probability=1-read/total))
                metrics.update(logical_files=rows,valid_extent_bytes=valid_bytes,template_bytes=templates,phase_probabilities=probs)
            else:
                groups=model['logical_groups'];logical=sum(g['logical_files'] for g in groups)
                category_counts={g['logical_size_bytes']:g['logical_files'] for g in groups}
                add('ai_categories','文件大小类别及数量',
                    '；'.join(f'{n:,} B：{v:,}个' for n,v in sorted(spec['categories'].items())),
                    '；'.join(f'{n:,} B：{v:,}个' for n,v in sorted(category_counts.items())),category_counts==spec['categories'])
                with map_path.open() as f:
                    reader=csv.DictReader(f);assert reader.fieldnames==['group','logical_file','fragment','base_bytes','extent_bytes','slot_bytes']
                    for g in groups:
                        for number in range(g['logical_files']):
                            for fragment in range(g['fragments_per_logical_file']):
                                r=next(reader);b=g['base_bytes']+(number*g['fragments_per_logical_file']+fragment)*g['slot_bytes']
                                target=[g['id'],str(number),str(fragment),str(b),str(g['extent_bytes']),str(g['slot_bytes'])]
                                if list(r.values())!=target or b!=previous:invalid+=1
                                previous=b+g['slot_bytes'];valid_bytes+=g['extent_bytes'];rows+=1
                    if next(reader,None) is not None:invalid+=1
                add('file_map','逻辑文件、地址区间及容量',f'{spec["files"]:,}个逻辑文件；{spec["ranges"]:,}行；异常0项',
                    f'{logical:,}个逻辑文件；{rows:,}行；异常{invalid}项',logical==spec['files'] and rows==spec['ranges'] and previous==capacity and valid_bytes==spec['payload'] and invalid==0)
                sums=defaultdict(float)
                for c in model['ses_channels']:
                    sums[(c['operation'],c['xfersize'])]+=c['weight']
                read=math.fsum(v for (op,n),v in sums.items() if op=='read');total=math.fsum(sums.values())
                all_random=all(c['fileio']=='random' for c in model['ses_channels'])
                add('ai_request_mix','请求概率与随机读写',f'读{spec["read"]:.0%}、写{1-spec["read"]:.0%}；概率和1；纯随机',
                    f'读{read:.12%}、写{1-read:.12%}；概率和{total:.12f}',all_random and abs(read-spec['read'])<1e-12 and abs(total-1)<1e-12)
                mix_ok=set(sums)==set(spec['mix']) and all(abs(sums[k]-v)<1e-12 for k,v in spec['mix'].items())
                mix_text=lambda values:'；'.join(f'{op} {n//1024}KiB：{v:.4%}' for (op,n),v in values.items())
                add('ai_transfer_mix','读写与请求大小联合概率',mix_text(spec['mix']),mix_text(sums),mix_ok)
                t=model['user_timeline'];ranks=all(g['object_rank_seed']==SEED^g['logical_size_bytes'] for g in groups)
                add('ai_zipf','对象热度与独立选择','类别内Zipf(0.99)；每次选择1次I/O；配额关闭',
                    f'指数{t["alpha"]}；每次选择{t["ios_per_object_selection"]}次I/O',ranks and t['alpha']==.99 and t['ios_per_object_selection']==1 and t['request_quota'] is None and t['slice_us']==1000000 and t['duration_us']==600000000)
                metrics.update(logical_files=logical,mapping_rows=rows,valid_payload_bytes=valid_bytes,
                               read_probability=read,request_probability_sum=total,
                               joint_request_probabilities=[dict(operation=k[0],request_bytes=k[1],probability=v) for k,v in sums.items()])

        wrong=[];checked=0
        for line in (root/'verification/files.sha256').read_text().splitlines():
            digest,relative=line.split('  ',1);p=inside(root,relative);checked+=1
            if sha(p)!=digest:wrong.append(relative)
        add('file_integrity','数据与工具完整性','清单文件全部一致；差异0项',f'已核对{checked:,}个文件；差异{len(wrong)}项',not wrong)
        if check_index:
            index_path=root/'metadata/delivery_files.csv';bad=[];count=0
            with index_path.open(encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    p=inside(root,row['relative_path']);count+=1
                    if p.stat().st_size!=int(row['bytes']) or sha(p)!=row['sha256']:bad.append(row['relative_path'])
            add('delivery_index','三类资料清单完整性','登记文件全部一致；差异0项',f'已核对{count:,}个文件；差异{len(bad)}项',not bad)
    except (OSError,ValueError,KeyError,AssertionError,IndexError,StopIteration,struct.error) as error:
        add('input_error','资料读取与格式','输入完整、字段与约定一致',str(error) or type(error).__name__,False)
    return dict(status='PASS' if checks and all(x['status']=='PASS' for x in checks) else 'FAIL',
                access_seed=SEED,storage_workload_executed=False,checks=checks,metrics=metrics)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args()
    report=verify(a.root);print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
