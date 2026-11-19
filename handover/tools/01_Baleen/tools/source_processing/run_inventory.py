import json
import os
from pathlib import Path
import resource
import subprocess
import time

base = Path(__file__).resolve().parent
archive = Path('/home/chris/codex_tmp/baleen-graph-5t-20261023/baleen/storage_all_Region2.tar.gz')
env = dict(os.environ, TMPDIR=str(base / 'root'))
start = time.monotonic()
with (base / 'inventory-progress.log').open('w') as progress, (base / 'inventory.json').open('w') as summary:
    tar = subprocess.Popen(['nice', '-n', '10', 'tar', '-xOzf', str(archive), '201910/Region2/full.trace'],
                           stdout=subprocess.PIPE, stderr=progress, env=env)
    scan = subprocess.Popen(['nice', '-n', '10', str(base / 'scan_region2'), 'inventory', str(base / 'inventory.bin')],
                            stdin=tar.stdout, stdout=summary, stderr=progress, env=env)
    tar.stdout.close()
    scan_status = scan.wait()
    tar_status = tar.wait()
    assert scan_status == tar_status == 0, (scan_status, tar_status)
d = json.loads((base / 'inventory.json').read_text())
reference = json.loads(Path('/home/chris/ceph-tool/results/baleen-graph-5t-20261023/evidence/baleen/Region2-official-full-statistics.json').read_text())
assert d['rows'] == reference['raw_rows']
assert d['keys'] == reference['keys']
assert d['extent_bytes'] == reference['observed_max_endpoint_sum_bytes']
assert d['inventory_objects'] == reference['per_key_4mib_container_objects']
assert d['reads'] == sum(v for k, v in reference['weighted_operation_codes'].items() if int(k) in {1, 2, 5})
assert d['reads'] + d['writes'] == d['rows']
d['elapsed_s'] = time.monotonic() - start
d['child_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
d['matches_verified_full_trace'] = True
(base / 'inventory.json').write_text(json.dumps(d, indent=2) + '\n')
print(json.dumps(d, indent=2))
