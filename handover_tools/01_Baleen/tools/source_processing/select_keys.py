"""One fixed, capacity-limited hash prefix; no selection based on access heat."""
import json
from pathlib import Path
import time
import numpy as np

base = Path(__file__).resolve().parent
start = time.monotonic()
seed = 20261026
grain = 4194304
target_bytes = 5 * 2**40
dtype = np.dtype([('key', '<u8'), ('end', '<u8'), ('reads', '<u8'), ('writes', '<u8')])
source = np.fromfile(base / 'inventory.bin', dtype=dtype)
assert len(source) == 8190394 and np.all(source['key'][1:] > source['key'][:-1])

def hash_keys(keys):
    x = keys ^ np.uint64(seed)
    x = x + np.uint64(0x9e3779b97f4a7c15)
    x = (x ^ (x >> 30)) * np.uint64(0xbf58476d1ce4e5b9)
    x = (x ^ (x >> 27)) * np.uint64(0x94d049bb133111eb)
    return x ^ (x >> 31)

hashes = hash_keys(source['key'])
order = np.argsort(hashes, kind='stable')
slots = (source['end'] + grain - 1) // grain
prefix = np.cumsum(slots[order], dtype=np.uint64)
count = int(np.searchsorted(prefix, target_bytes // grain, side='right'))
assert 0 < count < len(source)
indices = np.sort(order[:count])
chosen = source[indices]
selected_dtype = np.dtype([(name, '<u8') for name in ('key', 'end', 'reads', 'writes', 'base', 'slot', 'hash')])
out = np.empty(count, dtype=selected_dtype)
for field in ('key', 'end', 'reads', 'writes'):
    out[field] = chosen[field]
out['slot'] = slots[indices] * grain
out['base'][0] = 0
out['base'][1:] = np.cumsum(out['slot'][:-1], dtype=np.uint64)
out['hash'] = hashes[indices]
out.tofile(base / 'selected.bin')
capacity = int(out['slot'].sum())
next_index = int(order[count])
assert capacity <= target_bytes < capacity + int(slots[next_index]) * grain
stats = {
    'schema': 1, 'selection': 'custom deterministic whole-key capacity prefix',
    'hash': 'splitmix64(block_id XOR seed), modulo 2^64', 'seed': seed,
    'hash_order': 'unsigned 64-bit ascending; input keys unique, SplitMix64 bijective',
    'capacity_basis': 'sum ceil(full_window_max_endpoint(key)/4MiB)*4MiB',
    'target_bytes': target_bytes, 'capacity_bytes': capacity, 'shortfall_bytes': target_bytes - capacity,
    'selected_keys': count, 'source_keys': len(source),
    'selected_key_fraction': count / len(source),
    'source_inventory_fraction': capacity / (int(slots.sum()) * grain),
    'largest_selected_hash': int(hashes[order[count - 1]]),
    'first_excluded_key': int(source['key'][next_index]),
    'first_excluded_hash': int(hashes[next_index]),
    'first_excluded_slot_bytes': int(slots[next_index]) * grain,
    'expected_selected_reads': int(out['reads'].sum()),
    'expected_selected_writes': int(out['writes'].sum()),
    'selected_extent_bytes': int(out['end'].sum()),
    'retained_full_source_clock': json.loads((base / 'inventory.json').read_text()),
    'no_access_count_or_hotness_used_for_priority': True,
    'no_skipping_keys_to_pack_capacity': True,
    'elapsed_s': time.monotonic() - start,
}
(base / 'selection.json').write_text(json.dumps(stats, indent=2) + '\n')
print(json.dumps(stats, indent=2))
