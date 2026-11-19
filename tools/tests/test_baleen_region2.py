"""Region2 integration audits real access metadata,never creates its5TiB data."""
from collections import Counter
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from idfs_workload import baleen_user,core,lifecycle,models
from idfs_workload.baleen_user_source import HEADER,RECORD

CAPACITY=5497553944576
GRAIN=4194304


class Region2Tests(unittest.TestCase):
    def test_source_selection_and_clock_are_the_audited_full_time_candidate(self):
        model=models.build('baleen');p=model['provenance']
        self.assertEqual(p['region'],'Region2')
        self.assertEqual(p['trace_sha256'],'df6cc476579301fa9148092903713242cc44975536c98a372ab0957df1014f06')
        self.assertEqual(p['selected_trace_gzip_sha256'],'1438a05ed09f7e432c4db230597eecc4c0953a002010fb95564daddf1f0ea81b')
        self.assertEqual(p['source_time_range_unix_ns'],['1568350198725452000','1568694405318661000'])
        self.assertEqual(p['selection']['seed'],20261026)
        self.assertEqual(p['selection']['selected_keys'],705130)
        self.assertEqual(p['selection']['source_keys'],8190394)
        self.assertTrue(p['selection']['no_access_count_or_hotness_used_for_priority'])
        self.assertEqual(model['user_timeline']['seed'],1048596)
        self.assertEqual(p['key_identity'],'block_id; rs_shard_id=0 is a CSV compatibility placeholder only')

    def test_pinned_tables_and_streamed_key_map_preserve_the_offline_candidate(self):
        source=baleen_user.SOURCE
        self.assertTrue((source/'index.json').is_file())
        self.assertEqual(hashlib.sha256((source/'index.json').read_bytes()).hexdigest(),
                         '4f02b7493898ea977e82351b3e5cca2d7edba0dde18f1668e6a0c407447188cd')
        index=json.loads((source/'index.json').read_text())
        self.assertEqual(index['source_time_range_unix_ns'],['1568350198725452000','1568694405318661000'])
        self.assertEqual(len(index['rows']),600)
        mapping=source/'key-map.csv'
        self.assertEqual(hashlib.sha256(mapping.read_bytes()).hexdigest(),
                         'f189f02ce563810503ed0b1978acaaf9b5b557864a30c985bfade19465257c6b')
        base=count=0;previous=-1
        with mapping.open() as stream:
            for fields in csv.DictReader(stream):
                row={k:int(v) for k,v in fields.items()}
                self.assertGreater(row['block_id'],previous)
                self.assertEqual(row['rs_shard_id'],0)
                self.assertEqual(row['base_bytes'],base)
                self.assertEqual(row['slot_bytes'],(row['extent_bytes']+GRAIN-1)//GRAIN*GRAIN)
                self.assertLessEqual(row['extent_bytes'],row['file_bytes'])
                self.assertLessEqual(row['file_bytes'],row['slot_bytes'])
                previous=row['block_id'];base+=row['slot_bytes'];count+=1
        self.assertEqual((count,base),(705130,CAPACITY))
        total=Counter();hist={'read':Counter(),'write':Counter()};largest_offset=0
        for second,row in enumerate(index['rows']):
            self.assertEqual(row['file'],f'user_data/second_{second:03d}.bin.gz')
            payload=(source/row['file']).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(),row['sha256'])
            raw=gzip.decompress(payload)
            magic,version,capacity,grain,number,requests,byte_count,entries,reads,writes=HEADER.unpack_from(raw)
            self.assertEqual((magic,version,capacity,grain,number),(0x42465231,1,CAPACITY,GRAIN,second))
            self.assertEqual((requests,byte_count,reads,writes),(row['requests'],row['bytes'],row['reads'],row['writes']))
            self.assertEqual(len(raw),HEADER.size+entries*RECORD.size)
            counts=Counter();observed_bytes=0
            for offset,size,op,n in RECORD.iter_unpack(raw[HEADER.size:]):
                if (offset<0 or offset%4096 or size<=0 or size%4096 or offset+size>CAPACITY or op not in (1,2) or n<=0):
                    self.fail('invalid native Region2 frequency entry')
                counts[op]+=n;observed_bytes+=size*n;largest_offset=max(largest_offset,offset)
                hist['read' if op==1 else 'write'][size]+=n
            self.assertEqual((counts[1],counts[2],observed_bytes),(reads,writes,byte_count))
            total.update(counts)
        self.assertEqual(total,{1:3925006,2:645808})
        self.assertGreater(largest_offset,2**32)
        profile=baleen_user._profile()
        self.assertEqual(profile['operation_histograms'],{op:[list(p) for p in sorted(h.items())] for op,h in hist.items()})

    def test_preparation_and_measurement_share_capacity_and_reject_region7_ready_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=root/'absent';bundle=root/'bundle'
            manifest=core.render(models.build('baleen'),data_root=data,output=bundle,rate='max')
            self.assertFalse(data.exists())
            self.assertEqual(manifest['capacity_bytes'],CAPACITY)
            self.assertNotEqual(manifest['layout_sha256'],'de07c344b99749e052db22f31f2eb6e6cdaa354c30d46ef6635fb87bc7a5a035')
            prep=(bundle/'prepare_data.vdb').read_text()
            self.assertIn(f'files=1,size={CAPACITY}',prep)
            self.assertIn('format=(restart,only)',prep)
            for name in ('current','baseline'):
                text=(bundle/f'run_{name}.vdb').read_text()
                self.assertIn(f'lun={data}/container/vdb.1_1.dir/vdb_f0000.file,size={CAPACITY}',text)
                self.assertEqual(sum(l.startswith('rd=') for l in text.splitlines()),1)
                self.assertIn('iorate=max,elapsed=600,interval=1,warmup=0',text)
                self.assertNotIn('format=(',text)
                self.assertNotIn('stopafter',text)
            data.mkdir()
            (data/lifecycle.READY).write_text(json.dumps(dict(state='ready',capacity_bytes=423008141312,
                layout_sha256='de07c344b99749e052db22f31f2eb6e6cdaa354c30d46ef6635fb87bc7a5a035')))
            with patch.object(lifecycle,'_storage_target',return_value=(data,{'fstype':'nfs4'})):
                with self.assertRaisesRegex(ValueError,'different layout'):lifecycle.check_ready(bundle)

    def test_user_sdk_applies_full_native_cycles_at_64bit_addresses_without_storage_io(self):
        stock=Path(os.environ.get('VDBENCH_HOME','/home/chris/PDSL/vdbench50406'))/'vdbench.jar'
        if not stock.is_file() or not shutil.which('javac') or not shutil.which('java'):
            self.skipTest('stock5.04.06/JDK unavailable')
        harness=Path(__file__).parent/'java/Vdb/Region2FrequencyTests.java'
        self.assertTrue(harness.is_file(),'missing real User SDK Region2 regression')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle=root/'bundle';classes=root/'classes';classes.mkdir()
            core.render(models.build('baleen'),data_root=root/'not-created',output=bundle,rate='max')
            cp=str(stock)+os.pathsep+str(bundle/'baleen-user.jar')
            result=subprocess.run(['javac','--release','8','-cp',cp,'-d',str(classes),str(harness)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run(['java','-XX:-UsePerfData','-Xmx2g','-Djava.io.tmpdir='+str(root),
                '-cp',str(classes)+os.pathsep+cp,'Vdb.Region2FrequencyTests',str(bundle/'native.timeline')],
                capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('REGION2_USER_PASS requests=4570814 reads=3925006 writes=645808',result.stdout)
            self.assertIn('no_physical_io=true',result.stdout)
            self.assertFalse((root/'not-created').exists())


if __name__=='__main__':unittest.main()
