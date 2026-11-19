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
clock = json.loads((base / 'inventory.json').read_text())
with (base / 'filter-progress.log').open('w') as progress, (base / 'filter.json').open('w') as summary:
    tar = subprocess.Popen(['nice', '-n', '10', 'tar', '-xOzf', str(archive), '201910/Region2/full.trace'],
                           stdout=subprocess.PIPE, stderr=progress, env=env)
    scan = subprocess.Popen(['nice', '-n', '10', str(base / 'scan_region2'), 'filter', str(base / 'selected.bin'), str(base / 'selected'),
                             str(clock['time_min_ns']), str(clock['time_max_ns'])],
                            stdin=tar.stdout, stdout=summary, stderr=progress, env=env)
    tar.stdout.close()
    scan_status = scan.wait()
    tar_status = tar.wait()
    assert scan_status == tar_status == 0, (scan_status, tar_status)
d = json.loads((base / 'filter.json').read_text())
selection = json.loads((base / 'selection.json').read_text())
assert d['source_rows'] == 47141113
assert d['reads'] == selection['expected_selected_reads']
assert d['writes'] == selection['expected_selected_writes']
assert d['selected_rows'] == d['reads'] + d['writes']
assert sum(d['full_requests_per_slice']) == clock['rows']
assert sum(d['full_reads_per_slice']) == clock['reads']
assert (base / 'selected.events').stat().st_size == d['selected_rows'] * 36
d['elapsed_s'] = time.monotonic() - start
d['child_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
(base / 'filter.json').write_text(json.dumps(d, indent=2) + '\n')
print(json.dumps(d, indent=2))
