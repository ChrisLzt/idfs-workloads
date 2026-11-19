"""Offline checks for the FAST candidate. They never create or open workload files."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from idfs_workload import fast_container as fast, fast_prepare
from idfs_workload.models import CASES

STOCK = Path(os.environ.get('VDBENCH_HOME', '/home/chris/PDSL/vdbench50406'))
ROOT = Path(__file__).resolve().parents[1]


class FastContainerTests(unittest.TestCase):
    def test_capacity_and_shared_sd_config(self):
        self.assertEqual(len(CASES), 5)
        self.assertNotIn('fast', CASES)
        self.assertEqual(fast.SHARDS * fast.SHARD_BYTES, 4 * 1024**4)
        self.assertEqual(fast.OBJECTS * fast.PAYLOAD_BYTES, 400 * 1024**3)
        self.assertEqual(fast.OBJECTS, 1_048_576)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = fast.specification(root/'no-data', STOCK, ['localhost'] + [f'127.0.0.{i}' for i in range(2, 9)], 64)
            config = fast.config_text(spec, root/'bundle', '0' * 64)
            self.assertEqual(config.count('\nsd='), 8)
            self.assertEqual(config.count('\nwd='), 1)
            self.assertIn('host=(h000,h001,h002,h003,h004,h005,h006,h007)', config)
            self.assertIn('xfersize=128k,seekpct=100,rdpct=90', config)
            self.assertTrue(config.endswith('rd=continuous,wd=fast,iorate=max,elapsed=600,interval=1,warmup=0\n'))
            self.assertFalse((root/'no-data').exists())

    def test_payload_write_retries_short_writes_at_correct_offset(self):
        slot = fast.SLOTS_PER_SHARD - 1
        offset = slot * fast.OBJECT_BYTES + fast.TRANSFER_BYTES
        with patch.object(fast_prepare.os, 'pwrite', side_effect=[17, fast.PAYLOAD_BYTES-17]) as write:
            fast_prepare.write_slot(7, fast.SHARDS - 1, slot)
        self.assertEqual([call.args[2] for call in write.call_args_list], [offset, offset+17])
        self.assertEqual([len(call.args[1]) for call in write.call_args_list], [fast.PAYLOAD_BYTES, fast.PAYLOAD_BYTES-17])
        self.assertLess(offset + fast.PAYLOAD_BYTES, fast.SHARD_BYTES - 4096)

    def test_interrupted_preparation_does_not_mark_incomplete_shard_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'data'
            spec = fast.specification(root, STOCK, ['localhost'], 4)
            mount = dict(target=temp, fstype='ceph')
            with patch.multiple(fast_prepare, SHARDS=1, SHARD_BYTES=8192, SLOTS_PER_SHARD=2, CHECKPOINT_SLOTS=1), \
                 patch.object(fast_prepare, '_storage_target', return_value=(root, mount)), \
                 patch.object(fast_prepare, 'check_layout', return_value='xattr'), \
                 patch.object(fast_prepare, 'write_slot', side_effect=[None, OSError('interrupted')]) as write:
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    fast_prepare.prepare(spec, False)
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    fast_prepare.check(spec, False)
                self.assertEqual(fast_prepare.load_state(root, spec)['parts'][0]['slots'], 1)
                write.reset_mock(side_effect=True)
                self.assertEqual(fast_prepare.prepare(spec, False)['state'], 'ready')
                self.assertEqual([call.args[2] for call in write.call_args_list], [1])

    @unittest.skipUnless((STOCK/'vdbench.jar').is_file(), 'requires the stock Vdbench jar')
    def test_real_parser_and_native_java_routing_without_storage_io(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root/'runtime'
            for count in (1, 8):
                spec = fast.specification(root/'no-data', STOCK, ['localhost'] + [f'127.0.0.{i}' for i in range(2, count+1)], 64)
                fast.render(spec, bundle)
                fast.load_bundle(bundle)
                opts = fast.launch_options(bundle, root/f'syntax-{count}', simulate=True)
                run = subprocess.run(**opts, capture_output=True, text=True, timeout=30, check=True)
                self.assertIn('Vdbench simulation completed successfully', run.stdout)
                self.assertFalse((root/'no-data').exists())
            extension = bundle/fast.EXTENSION
            jar_path = STOCK/'vdbench.jar'
            classpath = os.pathsep.join(map(str, (jar_path, extension)))
            subprocess.run(['javac', '-J-XX:-UsePerfData', '--release', '8', '-cp', classpath, '-d', str(root),
                            str(ROOT/'tests/java/Vdb/FastRoutingProbe.java')], check=True)
            java = STOCK/'jre/bin/java'
            if not java.exists():
                java = Path('/usr/bin/java')
            run = subprocess.run([str(java), '-XX:-UsePerfData', '-cp', classpath + os.pathsep + str(root),
                                  'Vdb.FastRoutingProbe'], capture_output=True, text=True, check=True, timeout=30)
            self.assertIn('FAST route and sampling passed', run.stdout)
