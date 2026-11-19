"""Build an offline Region2 candidate; never creates/opens the 5TiB replay container."""
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import resource
import shutil
import struct
import time
import numpy as np

BASE = Path(__file__).resolve().parent
RESULT = Path('/home/chris/ceph-tool/results/baleen-region2-5t-20261026')
OUTPUT = RESULT / 'candidate'
GRAIN = 4194304
HEADER = struct.Struct('>iiqiiqqiqq')
RECORD = struct.Struct('>qiIq')
KEY_DTYPE = np.dtype([(name, '<u8') for name in ('key', 'end', 'reads', 'writes', 'base', 'slot', 'hash')])
EVENT_DTYPE = np.dtype([('row', '<u8'), ('time_ns', '<u8'), ('offset', '<u8'),
                       ('size', '<u4'), ('opcode', '<u4'), ('key_index', '<u4')])

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def top20(values):
    positive = np.sort(np.asarray(values)[np.asarray(values) > 0])
    n = len(positive)
    return {'active_objects': n, 'object_touches': int(positive.sum()),
            'top20_count_share': float(positive[-((n + 4) // 5):].sum() / positive.sum()) if n else None,
            'one_touch_objects': int(np.count_nonzero(positive == 1)), 'maximum_touches': int(positive[-1]) if n else 0}

def pieces(start, size):
    end = start + size
    first = start // GRAIN
    last = (end - 1) // GRAIN
    all_ids, all_bytes, all_events = [], [], []
    for delta in range(int((last - first).max()) + 1):
        keep = first + delta <= last
        ids = (first[keep] + delta).astype(np.int64)
        spans = np.minimum(end[keep], (ids.astype(np.uint64) + 1) * GRAIN) - np.maximum(start[keep], ids.astype(np.uint64) * GRAIN)
        all_ids.append(ids)
        all_bytes.append(spans)
        all_events.append(np.flatnonzero(keep))
    return np.concatenate(all_ids), np.concatenate(all_bytes), np.concatenate(all_events)

def aggregate(start, size, reads, inventory):
    counts = np.zeros((2, inventory), dtype=np.uint64)
    byte_counts = np.zeros((2, inventory), dtype=np.uint64)
    ids, spans, events = pieces(start, size)
    for direction, selected in enumerate((reads[events], ~reads[events])):
        np.add.at(counts[direction], ids[selected], 1)
        np.add.at(byte_counts[direction], ids[selected], spans[selected])
    assert int(byte_counts.sum()) == int(size.sum())
    return counts, byte_counts

def interval_union(start, size):
    end = start + size
    order = np.lexsort((end, start))
    ordered_start, ordered_end = start[order], end[order]
    previous = np.empty(len(end), dtype=np.uint64)
    previous[0] = 0
    previous[1:] = np.maximum.accumulate(ordered_end[:-1])
    lower = np.maximum(ordered_start, previous)
    return int(np.where(ordered_end > lower, ordered_end - lower, 0).sum())

def sparse_stats(start, size):
    ids, byte_counts, event_indices = pieces(start, size)
    unique, inverse, counts = np.unique(ids, return_inverse=True, return_counts=True)
    total_bytes = np.bincount(inverse, weights=byte_counts.astype(np.float64))
    assert int(total_bytes.sum()) == int(size.sum())
    return unique, counts, total_bytes

def sparse_tv(left_ids, left_values, right_ids, right_values):
    ids = np.union1d(left_ids, right_ids)
    a = np.zeros(len(ids)); b = np.zeros(len(ids))
    a[np.searchsorted(ids, left_ids)] = left_values / left_values.sum()
    b[np.searchsorted(ids, right_ids)] = right_values / right_values.sum()
    return float(np.abs(a - b).sum() / 2)

def main():
    started = time.monotonic()
    selection = json.loads((BASE / 'selection.json').read_text())
    filtered = json.loads((BASE / 'filter.json').read_text())
    clock = json.loads((BASE / 'inventory.json').read_text())
    keys = np.fromfile(BASE / 'selected.bin', dtype=KEY_DTYPE)
    events = np.memmap(BASE / 'selected.events', dtype=EVENT_DTYPE, mode='r')
    assert len(events) == filtered['selected_rows'] and len(keys) == selection['selected_keys']
    assert np.all(events['row'][1:] > events['row'][:-1])
    assert np.all(events['time_ns'][1:] >= events['time_ns'][:-1])
    key_index = events['key_index'].astype(np.int64)
    reads = np.isin(events['opcode'], [1, 2, 5])
    for flag, field in ((reads, 'reads'), (~reads, 'writes')):
        assert np.array_equal(np.bincount(key_index[flag], minlength=len(keys)), keys[field])
    max_ends = np.zeros(len(keys), dtype=np.uint64)
    raw_size = events['size'].astype(np.uint64)
    np.maximum.at(max_ends, key_index, events['offset'] + raw_size)
    assert np.array_equal(max_ends, keys['end'])
    base = keys['base'][key_index]
    raw_start = base + events['offset']
    relative_aligned = events['offset'] // 4096 * 4096
    aligned_size = (events['offset'] + raw_size + 4095) // 4096 * 4096 - relative_aligned
    aligned_start = base + relative_aligned
    assert int(aligned_size.max()) < 2**31
    assert np.all(relative_aligned + aligned_size <= (keys['end'][key_index] + 2097151) // 2097152 * 2097152)
    assert np.all(raw_start // GRAIN == aligned_start // GRAIN)
    assert np.all((raw_start + raw_size - 1) // GRAIN == (aligned_start + aligned_size - 1) // GRAIN)
    capacity = selection['capacity_bytes']; inventory = capacity // GRAIN
    native_counts, native_bytes = aggregate(raw_start, raw_size, reads, inventory)
    aligned_counts, aligned_bytes = aggregate(aligned_start, aligned_size, reads, inventory)
    assert np.array_equal(native_counts, aligned_counts)
    combined = native_counts.sum(axis=0)
    union = interval_union(raw_start, raw_size)
    active_clipped = 0
    for key in keys:
        first = int(key['base']) // GRAIN
        n = int(key['slot']) // GRAIN
        valid = np.minimum(GRAIN, int(key['end']) - np.arange(n, dtype=np.uint64) * GRAIN)
        active_clipped += int(valid[combined[first:first + n] > 0].sum())
    assert union <= active_clipped <= selection['selected_extent_bytes'] <= capacity
    seconds = np.minimum(599, (events['time_ns'] - clock['time_min_ns']) * 600 //
                         (clock['time_max_ns'] - clock['time_min_ns'])).astype(np.int64)
    assert np.all(seconds[1:] >= seconds[:-1])
    boundaries = np.searchsorted(seconds, np.arange(601))
    native_per_second = np.diff(boundaries)
    empty = np.flatnonzero(native_per_second == 0).tolist()
    if empty:
        raise ValueError('Native empty slices require explicit compatibility resolution: ' + str(empty))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'user_data').mkdir(exist_ok=True)
    (OUTPUT / 'native-source').mkdir(exist_ok=True)
    mapping_path = OUTPUT / 'key-map.csv'
    with mapping_path.open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['block_id','rs_shard_id','base_bytes','extent_bytes','file_bytes','slot_bytes'])
        for key in keys:
            end = int(key['end'])
            writer.writerow([int(key['key']), 0, int(key['base']), end,
                             (end + 2097151) // 2097152 * 2097152, int(key['slot'])])
    with gzip.open(OUTPUT / 'native-source/selected-keys.csv.gz', 'wt', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['block_id','hash_u64','native_reads','native_writes','extent_bytes','slot_bytes'])
        for key in keys:
            writer.writerow([int(key[n]) for n in ['key','hash','reads','writes','end','slot']])
    rows = []; expected_counts = np.zeros(inventory); expected_bytes = np.zeros(inventory)
    previous = None
    for second in range(600):
        lo, hi = map(int, boundaries[second:second + 2])
        count = hi - lo
        table = Counter(zip(aligned_start[lo:hi].tolist(), aligned_size[lo:hi].tolist(),
                            np.where(reads[lo:hi], 1, 2).tolist()))
        byte_count = sum(size * n for (off, size, op), n in table.items())
        read_count = int(reads[lo:hi].sum())
        payload = bytearray(HEADER.pack(0x42465231, 1, capacity, GRAIN, second, count, byte_count,
                                       len(table), read_count, count - read_count))
        for (off, size, op), n in sorted(table.items()):
            payload.extend(RECORD.pack(off, size, op, n))
        compressed = gzip.compress(payload, compresslevel=9, mtime=0)
        path = OUTPUT / f'user_data/second_{second:03d}.bin.gz'
        path.write_bytes(compressed)
        ids, touches, byte_values = sparse_stats(raw_start[lo:hi], raw_size[lo:hi])
        expected_counts[ids] += touches / count
        expected_bytes[ids] += byte_values / count
        native_heat = top20(touches)
        adjacent = None if previous is None else sparse_tv(previous[0], previous[1], ids, touches)
        previous = (ids, touches)
        rows.append({'second':second,'start_us':second*1000000,'end_us':(second+1)*1000000,
                     'file':f'user_data/second_{second:03d}.bin.gz','sha256':sha(path),
                     'requests':count,'reads':read_count,'writes':count-read_count,
                     'bytes':byte_count,'native_bytes':int(raw_size[lo:hi].sum()),'entries':len(table),
                     'source_full_requests':filtered['full_requests_per_slice'][second],
                     'source_full_reads':filtered['full_reads_per_slice'][second],
                     'active_objects':len(ids),'top20_count_share':native_heat['top20_count_share'],
                     'adjacent_count_tv':adjacent})
        if second % 100 == 99:
            print(f'compiled {second + 1}/600 frequency tables', flush=True)
    np.savez_compressed(RESULT / 'object-histograms.npz', reads=native_counts[0], writes=native_counts[1],
                        native_read_bytes=native_bytes[0], native_write_bytes=native_bytes[1],
                        aligned_read_bytes=aligned_bytes[0], aligned_write_bytes=aligned_bytes[1],
                        equal_iops_count_weights=expected_counts, equal_iops_byte_weights=expected_bytes)
    normalized_count = expected_counts / expected_counts.sum()
    normalized_bytes = expected_bytes / expected_bytes.sum()
    target_count = combined / combined.sum()
    target_bytes = native_bytes.sum(axis=0) / native_bytes.sum()
    fixed_hot = np.argsort(combined)[-((np.count_nonzero(combined)+4)//5):]
    alignment_tv = float(np.abs(aligned_bytes.sum(axis=0) / aligned_bytes.sum() - target_bytes).sum() / 2)
    stats = {'schema':1,'source':'Baleen24 Region2 official full.trace, fixed whole-key subset',
             'selection':selection,'source_clock':clock,'filtered_trace':filtered,
             'capacity_bytes':capacity,'inventory_objects':inventory,'keys':len(keys),'requests':len(events),
             'native_unique_accessed_bytes':union,'observed_max_endpoint_sum_bytes':selection['selected_extent_bytes'],
             'padding_bytes':capacity-selection['selected_extent_bytes'],
             'unaccessed_holes_bytes':selection['selected_extent_bytes']-union,
             'active_object_address_bytes_clipped':active_clipped,
             'native_request_bytes':int(raw_size.sum()),'aligned_request_bytes':int(aligned_size.sum()),
             'maximum_aligned_request':int(aligned_size.max()),'cover_alignment_changed_requests':int(np.count_nonzero(
                 (raw_start != aligned_start) | (raw_size != aligned_size))),
             'read':top20(native_counts[0]),'write':top20(native_counts[1]),'combined':top20(combined),
             'mapping_count_tv':0,'mapping_byte_tv_before_direct_io_alignment':0,
             'direct_io_cover_alignment_combined_byte_tv':alignment_tv,
             'empty_slices':empty,'duration_us':600000000,'slice_us':1000000,
             'source_boundary_policy':'fixed full trace boundaries; never selected min/max',
             'rs_shard_id_policy':'CSV compatibility constant zero; not an original Region2 identity field',
             'uniform_iops_expectation':{
                 'definition':'equal completed requests in every replay second; native within-second frequencies, infinite-cycle expectation',
                 'combined_count_tv':float(np.abs(normalized_count-target_count).sum()/2),
                 'combined_byte_tv':float(np.abs(normalized_bytes-target_bytes).sum()/2),
                 'fixed_native_hot20_count_share':float(normalized_count[fixed_hot].sum()),
                 'retop20_inventory_count_share':float(np.sort(normalized_count)[-((inventory+4)//5):].sum()),
                 'read_request_share':float(np.mean([r['reads']/r['requests'] for r in rows]))},
             'requests_per_slice_min':int(native_per_second.min()),'requests_per_slice_max':int(native_per_second.max()),
             'source_record_count_per_slice_cv':float(native_per_second.std()/native_per_second.mean()),
             'table_entries':sum(r['entries'] for r in rows),
             'runtime_array_lower_bound_bytes':sum(13*r['entries']+8*r['requests'] for r in rows),
             'rows':rows,'elapsed_s':time.monotonic()-started,
             'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    (RESULT / 'statistics.json').write_text(json.dumps(stats,indent=2)+'\n')
    with (RESULT / 'seconds.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
    shutil.copyfile(BASE/'selected.trace.gz', OUTPUT/'native-source/selected.trace.gz')
    shutil.copyfile(BASE/'selection.json', OUTPUT/'native-source/selection.json')
    native_jar=Path('/home/chris/idfs-workloads/idfs_workload/baleen_user_java')
    for name in ['baleen-user.jar','vdbench']:
        shutil.copyfile(native_jar/name,OUTPUT/name)
    (OUTPUT/'vdbench').chmod(0o755)
    timeline=[f'BALEEN_TIMELINE1\t{capacity}\t{GRAIN}\t600000000\t600']
    timeline.extend(f'{s*1000000}\t{(s+1)*1000000}\tuser_data/second_{s:03d}.bin.gz' for s in range(600))
    (OUTPUT/'native.timeline').write_text('\n'.join(timeline)+'\n')
    data_file='/mnt/idfs/workloads_5t/baleen_region2/container/vdb.1_1.dir/vdb_f0000.file'
    vdb=['* OFFLINE CANDIDATE: Region2 full-time whole-key subset; replay data not prepared',
         'data_errors=1','messagescan=no','formatsds=no',f'hd=default,jvms=1,vdbench={OUTPUT}',
         f'sd=baleen,lun={data_file},size={capacity},threads=64,openflags=o_direct,offset=0,align=4096',
         f'wd=native,sd=baleen,xfersize={stats["maximum_aligned_request"]},seekpct=100,rdpct=50,skew=100,user=(idfsbaleen.TimelineWorkload,native.timeline,seed20261026)',
         'rd=continuous,wd=native*,iorate=max,elapsed=600,interval=1,warmup=0']
    (OUTPUT/'run_candidate.vdb').write_text('\n'.join(vdb)+'\n')
    index={'schema':1,'grain_bytes':GRAIN,'duration_us':600000000,'slice_us':1000000,'capacity_bytes':capacity,
           'source':'Baleen Region2 whole-key/full-time offline candidate','native_trace':True,
           'source_trace_sha256':'df6cc476579301fa9148092903713242cc44975536c98a372ab0957df1014f06',
           'source_time_range_unix_ns':[str(clock['time_min_ns']),str(clock['time_max_ns'])],
           'key_count':len(keys),'inventory_objects':inventory,'active_objects':stats['combined']['active_objects'],
           'source_operations':{'GET':filtered['reads'],'PUT':filtered['writes']},'requests':len(events),
           'bytes':int(aligned_size.sum()),'maximum_request':stats['maximum_aligned_request'],
           'key_map_sha256':sha(mapping_path),'mapping_added_object_count_tv':0,
           'mapping_added_object_byte_tv':0,'direct_io_cover_alignment_combined_byte_tv':alignment_tv,'rows':rows}
    (OUTPUT/'index.json').write_text(json.dumps(index,indent=2)+'\n')
    manifest={str(p.relative_to(OUTPUT)):{'bytes':p.stat().st_size,'sha256':sha(p)}
              for p in sorted(OUTPUT.rglob('*')) if p.is_file()}
    (OUTPUT/'manifest.json').write_text(json.dumps({'offline_candidate':True,'replay_data_created':False,
        'capacity_bytes':capacity,'stock_vdbench_version':'5.04.06','user_jar_unchanged':True,
        'files':manifest},indent=2)+'\n')
    print(json.dumps({k:v for k,v in stats.items() if k not in ['rows','selection','source_clock','filtered_trace']},indent=2))

if __name__ == '__main__':
    main()
