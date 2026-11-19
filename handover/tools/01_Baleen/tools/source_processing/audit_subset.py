"""Independent full-source subsequence check and historical-parser cross-check."""
import csv
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import tarfile
import time

BASE = Path(__file__).resolve().parent
RESULT = Path('/home/chris/ceph-tool/results/baleen-region2-5t-20261026')
CANDIDATE = RESULT / 'candidate'
ARCHIVE = Path('/home/chris/codex_tmp/baleen-graph-5t-20261023/baleen/storage_all_Region2.tar.gz')
started = time.monotonic()
with (CANDIDATE / 'key-map.csv').open(newline='') as f:
    selected = {int(row['block_id']) for row in csv.DictReader(f)}
reference = json.loads((RESULT / 'statistics.json').read_text())
assert len(selected) == reference['keys']
rows = matched = 0
expected_hash = hashlib.sha256()
observed_hash = hashlib.sha256()

def data_rows(stream):
    for line in stream:
        if line.strip() and not line.startswith(b'#'):
            yield line

with gzip.open(CANDIDATE / 'native-source/selected.trace.gz', 'rb') as subset:
    remaining = data_rows(subset)
    with tarfile.open(ARCHIVE, 'r|gz') as archive:
        for member in archive:
            if not member.name.endswith('/Region2/full.trace'):
                continue
            for raw in data_rows(archive.extractfile(member)):
                rows += 1
                if int(raw.split(None, 1)[0]) not in selected:
                    continue
                actual = next(remaining)
                assert actual == raw, ('changed original data fields or sequence', rows)
                expected_hash.update(raw)
                observed_hash.update(actual)
                matched += 1
            break
    assert next(remaining, None) is None, 'extra sampled records'
assert rows == 47141113 and matched == reference['requests']
assert expected_hash.hexdigest() == observed_hash.hexdigest()

module_path = Path('/home/chris/codex_tmp/baleen-graph-5t-20261023/baleen/analyze_official.py')
spec = importlib.util.spec_from_file_location('historical_baleen_reader', module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ROOT = BASE / 'root'
with gzip.open(CANDIDATE / 'native-source/selected.trace.gz', 'rb') as stream:
    independent = module.analyze(stream,
        '# block_id io_offset io_size op_time op_name pipeline user_namespace user_name',
        'independent-selected', 'verified full-trace whole-key subsequence')
fields = {
    'raw_rows':'requests', 'keys':'keys',
    'observed_max_endpoint_sum_bytes':'observed_max_endpoint_sum_bytes',
    'unique_accessed_byte_union_bytes':'native_unique_accessed_bytes',
    'per_key_4mib_container_bytes':'capacity_bytes',
    'per_key_4mib_container_objects':'inventory_objects',
    'request_bytes_with_repeats':'native_request_bytes',
    'active_object_address_bytes_clipped_at_key_endpoint':'active_object_address_bytes_clipped',
}
for old, new in fields.items():
    assert independent[old] == reference[new], (old, independent[old], reference[new])
for direction in ('read','write','combined'):
    for name in ('active_objects','object_touches','top20_count_share','one_touch_objects'):
        assert independent[direction][name] == reference[direction][name], (direction, name)
result = {
    'source_rows_checked':rows, 'selected_rows_checked':matched,
    'selected_keys':len(selected),
    'every_selected_raw_record_byte_for_byte_equal_to_source':True,
    'original_data_record_order_retained':True,
    'data_rows_sha256':expected_hash.hexdigest(),
    'independent_native_statistics_match':True,
    'historical_parser':str(module_path),
    'elapsed_s':time.monotonic()-started,
    'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
}
(RESULT/'SOURCE_SUBSET_AUDIT.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
