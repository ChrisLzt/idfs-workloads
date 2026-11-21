"""Offline checks for the FAST candidate. They never create or open workload files."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from idfs_workload import fast_container as fast, fast_prepare, lifecycle, user_prepare, user_runtime
from idfs_workload.models import DEFAULT_CASES

STOCK = Path(os.environ.get('VDBENCH_HOME', '/home/chris/PDSL/vdbench50406'))
ROOT = Path(__file__).resolve().parents[1]


class FastContainerTests(unittest.TestCase):
    def test_capacity_and_per_client_sds_share_a_file(self):
        self.assertEqual(len(DEFAULT_CASES), 5)
        self.assertEqual(DEFAULT_CASES[0], 'fast')
        self.assertNotIn('baleen', DEFAULT_CASES)
        self.assertEqual(fast.CAPACITY_BYTES, 4 * 1024**4)
        self.assertEqual(fast.OBJECTS * fast.PAYLOAD_BYTES, 400 * 1024**3)
        self.assertEqual(fast.OBJECTS, 1_048_576)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = fast.specification(root/'no-data', STOCK, ['localhost'] + [f'127.0.0.{i}' for i in range(2, 9)], 64)
            config = fast.config_text(spec, root/'bundle', '0' * 64)
            self.assertEqual(spec['files'], [{'name': 'fast.bin', 'bytes': 4 * 1024**4}])
            devices = [line for line in config.splitlines() if line.startswith('sd=')]
            self.assertEqual(len(devices), 8)
            for i, line in enumerate(devices):
                self.assertIn(f"sd=fast_{i:03d},host=h{i:03d},lun={root/'no-data/fast.bin'},"
                              f"size={4 * 1024**4},threads=64", line)
            self.assertNotIn('concatenatesds', config)
            self.assertEqual(config.count('\nwd='), 1)
            self.assertIn('wd=fast,sd=fast_*', config)
            self.assertIn('xfersize=128k,seekpct=100,rdpct=90', config)
            self.assertTrue(config.endswith('rd=continuous,wd=fast,iorate=max,elapsed=600,interval=1,warmup=0\n'))
            self.assertFalse((root/'no-data').exists())

    def test_interrupted_preparation_does_not_mark_incomplete_container_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'data'
            spec = dict(id=fast.WORKLOAD_ID, data_root=str(root), capacity_bytes=8192,
                        layout_sha256='fixture',
                        preparation=user_prepare.plan(root/fast.FILE_NAME,8192,transfer=4096))
            mount = dict(target=temp, fstype='nfs4')
            first_output, retry_output = Path(temp)/'first', Path(temp)/'retry'
            first_output.mkdir()
            retry_output.mkdir()
            options = dict(bundle=Path(temp)/'bundle', vdbench=STOCK/'vdbench')
            def write_boundary(bundle, manifest, vdbench, attempt, fd, *, first, end, lock_fd):
                if first == 1 and attempt.parent == first_output:
                    raise OSError('interrupted')
                os.pwrite(fd, b'x'*4096, first*4096)

            with patch.multiple(fast_prepare, CAPACITY_BYTES=8192, OBJECTS=2, CHECKPOINT_SLOTS=1), \
                 patch.object(fast_prepare, '_storage_target', return_value=(root, mount)), \
                 patch.object(lifecycle, '_storage_target', side_effect=lambda path: (Path(path), mount)), \
                 patch.object(fast_prepare, 'write_pass', side_effect=write_boundary) as write:
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    fast_prepare.prepare(spec, output=first_output, **options)
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    lifecycle.container_ready(spec)
                self.assertEqual(fast_prepare.load_state(root)['file']['slots'], 1)
                write.reset_mock()
                result = fast_prepare.prepare(spec, output=retry_output, **options)
                self.assertEqual((result['state'], result['file_count']), ('ready', 1))
                self.assertEqual([(call.kwargs['first'], call.kwargs['end']) for call in write.call_args_list], [(1, 2)])
                self.assertEqual(lifecycle.container_ready(spec), result)
                (root/lifecycle.READY).unlink()
                self.assertEqual(lifecycle.container_ready(spec), result)

    def test_legacy_dataset_is_rejected_without_overwriting_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'fast00.bin').touch()
            with self.assertRaisesRegex(ValueError, 'unrelated files'):
                fast_prepare.load_state(root)
            self.assertEqual([path.name for path in root.iterdir()], ['fast00.bin'])

    def test_preflight_counts_payload_headroom_without_creating_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'no-data'
            spec = fast.specification(root, STOCK, ['localhost'], 4)
            mount = dict(target=temp, fstype='ceph')
            space = SimpleNamespace(f_bavail=450*2**30//4096, f_frsize=4096)
            with patch.object(fast_prepare, '_storage_target', return_value=(root, mount)), \
                 patch.object(fast_prepare, 'require_layout', return_value={'verification':'xattr'}), \
                 patch.object(fast_prepare.os, 'statvfs', return_value=space):
                report = fast_prepare.preflight(spec)
            self.assertEqual(report['required_free_bytes'], 420*2**30)
            self.assertFalse(root.exists())

    @unittest.skipUnless((STOCK/'vdbench.jar').is_file(), 'requires the stock Vdbench jar')
    def test_real_parser_and_per_client_sd_commands_without_storage_io(self):
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
            run = subprocess.run([str(java), '-XX:-UsePerfData', '-Dfast.test.sha256='+user_runtime.sha256(extension),
                                  '-cp', classpath + os.pathsep + str(root),
                                  'Vdb.FastRoutingProbe'], capture_output=True, text=True, check=True, timeout=30)
            self.assertIn('FAST single-SD addresses and sampling passed', run.stdout)
