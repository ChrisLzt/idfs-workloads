"""Prepare/test separation; only small local fixtures and replaced storage boundaries."""
import errno
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
CASES=['bigdata_baleen','bigdata_fast','graph_zipf','hpc_wrf','ai_training_ses','ai_inference_ses']


class PreparationTests(unittest.TestCase):
    def preparer(self):
        self.assertIsNotNone(importlib.util.find_spec('idfs_workload.graph_prepare'),
                             'separate graph preparation is missing')
        from idfs_workload import graph_prepare
        return graph_prepare

    def test_each_workload_has_independent_prepare_check_and_test_entries(self):
        for case in CASES:
            for entry in ['prepare_data.sh','check_data.sh','run_test.sh']:
                p=ROOT.parent/'workloads'/'hot_cold'/case/entry
                self.assertTrue(p.is_file(),str(p))
                self.assertTrue(os.access(p,os.X_OK),str(p))
                r=subprocess.run([str(p),'--help'],capture_output=True,text=True)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_preparation_and_test_actions_require_execute_for_all_cases(self):
        for case in ['fast','baleen','graph','wrf','ai_training','ai_inference']:
            for action in ['prepare','run']:
                r=subprocess.run([str(ROOT/'workloads.sh'),action,'--case',case],capture_output=True,text=True)
                self.assertNotEqual(r.returncode,0)
                self.assertIn('--execute',r.stderr)

    def test_write_pass_checks_exact_completion_and_no_io_errors_before_sync(self):
        from idfs_workload import user_prepare
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root/'file'
            file.touch()
            bundle = root/'bundle'
            bundle.mkdir()
            plan = user_prepare.plan(file, 8192, transfer=4096)
            fingerprint = '0'*64
            config = bundle/'prepare_data.vdb'
            config.write_text(user_prepare.config_text(plan, bundle, fingerprint))
            manifest = dict(preparation=plan, user_jar='fixture.jar', asset_sha256={'fixture.jar':fingerprint})

            def finish(args, check, pass_fds):
                report = Path(args[-1])
                report.mkdir()
                (report/'summary.html').write_text(summary)
                (report/'errorlog.html').write_text(errors)

            with file.open('r+b') as data:
                # Size metadata only; subprocess and fsync stand in for the storage boundary.
                data.truncate(8192)
                for index, (summary, errors, good) in enumerate([
                        ('00:00:00.000 localhost-0: IDFS_PREPARE_COMPLETE 0 2 8192\n', '', True),
                        ('00:00:00.000 localhost-0: IDFS_PREPARE_COMPLETE 0 1 4096\n', '', False),
                        ('00:00:00.000 localhost-0: IDFS_PREPARE_COMPLETE 0 2 8192\n',
                         '00:00:00.000 op: write lun: file lba: 0 xfer: 4096 errno: Input/output error\n', False),
                ]):
                    output = root/f'report-{index}'
                    output.mkdir()
                    with patch.object(user_prepare, 'execution', return_value={'args':['-o', str(output/'vdbench')]}), \
                         patch.object(user_prepare.subprocess, 'run', side_effect=finish), \
                         patch.object(user_prepare.os, 'fsync') as sync:
                        if good:
                            written = user_prepare.write_pass(bundle, manifest, 'stock', output, data.fileno())
                            self.assertEqual(written, 8192)
                            sync.assert_called_once_with(data.fileno())
                        else:
                            with self.assertRaisesRegex(ValueError, 'full write pass'):
                                user_prepare.write_pass(bundle, manifest, 'stock', output, data.fileno())
                            sync.assert_not_called()

    def test_graph_preparation_without_authorization_does_not_create_data(self):
        prep=self.preparer()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(ValueError,'--execute'):
                prep.prepare(p/'absent-bundle',output=p/'result')
            self.assertFalse((p/'result').exists())

    def test_missing_graph_preflight_checks_capacity_but_creates_nothing(self):
        prep=self.preparer()
        from idfs_workload import core,graph_zipf,lifecycle
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);data=p/'absent';bundle=p/'bundle'
            core.render(graph_zipf.build(),data_root=data,output=bundle,rate='max')
            mount={'target':str(p),'fstype':'nfs4','source':'fixture-nfs:/data'}
            fs=SimpleNamespace(f_bavail=6*2**40//4096,f_frsize=4096)
            with patch.object(lifecycle,'mount_info',return_value=mount),\
                 patch.object(prep.os,'statvfs',return_value=fs),\
                 patch('idfs_workload.file_layout.os.getxattr',side_effect=OSError(errno.ENOTSUP,'no private attrs')):
                report=prep.preflight(bundle)
            self.assertEqual(report['state'],'missing-file')
            self.assertGreaterEqual(report['required_free_bytes'],4398046511104)
            self.assertFalse(data.exists())

    def test_graph_incomplete_preparation_blocks_readiness(self):
        prep=self.preparer()
        from idfs_workload import core,graph_zipf
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);data=p/'input';data.mkdir();bundle=p/'bundle'
            core.render(graph_zipf.build(),data_root=data,output=bundle,rate='max')
            intent,_=prep.markers(data/'test.graph.edge.props');intent.write_text('{}\n')
            with self.assertRaisesRegex(ValueError,'preparation'):
                graph_zipf.check_ready(bundle)

    def test_graph_preparation_fsyncs_before_ready_and_preserves_interrupted_files(self):
        prep=self.preparer()
        from idfs_workload import core,graph_zipf,lifecycle,user_prepare
        for fail in [False,True]:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d);data=p/'data';bundle=p/'bundle';file=data/'test.graph.edge.props'
                core.render(graph_zipf.build(),data_root=data,output=bundle,rate='max')
                report=dict(state='missing-file',mount={'target':str(p),'fstype':'nfs4'},capacity_bytes=4398046511104)
                intent,ready=prep.markers(file);events=[];real_fsync=os.fsync
                def write_boundary(config, manifest, vdbench, output, fd):
                    self.assertEqual(config, bundle)
                    self.assertEqual(manifest['capacity_bytes'], 4398046511104)
                    self.assertEqual(manifest['preparation']['transfer_bytes'], 4*2**20)
                    self.assertTrue(intent.exists());os.pwrite(fd,b'small-storage-boundary-fixture',0)
                    events.append('write')
                    if fail:raise OSError('injected storage interruption')
                    os.fsync(fd)
                    return manifest['capacity_bytes']
                def sync(fd):
                    name=Path(os.readlink(f'/proc/self/fd/{fd}')).name
                    events.append('fsync:'+name)
                    return real_fsync(fd)
                def checked(manifest):
                    self.assertEqual(events,['fsync:'+intent.name,'fsync:'+data.name,
                                             'write','fsync:'+file.name])
                    self.assertTrue(intent.exists());self.assertFalse(ready.exists())
                    return dict(state='ready',capacity_bytes=4398046511104), file.stat()
                with patch.object(prep,'preflight',return_value=report),\
                     patch.object(prep,'require_layout',return_value={'layout':None}),\
                     patch.object(user_prepare,'write_pass',side_effect=write_boundary),\
                     patch.object(prep.os,'ftruncate'),\
                     patch('idfs_workload.user_runtime.execution'),\
                     patch.object(lifecycle,'_executable',return_value=p/'fixture-vdbench'),\
                     patch.object(prep.os,'fsync',side_effect=sync),\
                     patch.object(lifecycle,'_inspect_container',side_effect=checked):
                    if fail:
                        with self.assertRaises(OSError):prep.prepare(bundle,output=p/'result',execute=True)
                    else:
                        result=prep.prepare(bundle,output=p/'result',execute=True)
                        self.assertEqual(result['state'],'ready')
                self.assertEqual(file.read_bytes(),b'small-storage-boundary-fixture')
                self.assertEqual(intent.exists(),fail)
                self.assertEqual(ready.exists(),not fail)
                if not fail:
                    self.assertEqual(events,['fsync:'+intent.name,'fsync:'+data.name,
                                             'write','fsync:'+file.name,'fsync:'+ready.name+'.tmp',
                                             'fsync:'+data.name,'fsync:'+data.name])
                if fail:
                    with self.assertRaisesRegex(ValueError,'preparation'):graph_zipf.check_ready(bundle)

    def test_graph_reuses_existing_ready_input_without_overwriting_it(self):
        prep=self.preparer()
        from idfs_workload import core,graph_zipf
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);data=p/'data';data.mkdir();file=data/'test.graph.edge.props';file.write_bytes(b'original-fixture')
            bundle=p/'bundle';core.render(graph_zipf.build(),data_root=data,output=bundle,rate='max')
            with patch.object(prep,'preflight',return_value={'state':'ready'}),\
                 patch.object(prep,'write_new_file',side_effect=AssertionError('existing data must not be overwritten')):
                result=prep.prepare(bundle,output=p/'result',execute=True)
            self.assertTrue(result['reused_existing_data'])
            self.assertEqual(file.read_bytes(),b'original-fixture')
            self.assertFalse((p/'result').exists())

    def test_all_workloads_report_the_same_readiness_fields_without_nfs_prompt(self):
        from idfs_workload import lifecycle, user_prepare
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mount = {'target':str(base), 'fstype':'nfs4', 'source':'fixture-nfs:/data'}
            fields = None
            with patch.object(lifecycle,'mount_info',return_value=mount), \
                 patch('idfs_workload.file_layout.os.getxattr',side_effect=OSError(errno.ENOTSUP,'no private attrs')):
                for case in CASES:
                    folder = base/case
                    folder.mkdir()
                    data = folder/'container'
                    data.write_bytes(b'f'*4096)
                    manifest = dict(id=case, data_root=str(folder), capacity_bytes=4096, file_count=1,
                                    layout_sha256='fixture', preparation=user_prepare.plan(data,4096,transfer=4096))
                    if case == 'graph_zipf':
                        manifest['input_policy'] = {'mode':'existing_file'}
                    recorded = lifecycle.record_container_ready(manifest)
                    ready = lifecycle.container_ready(manifest)
                    self.assertEqual(ready, recorded)
                    self.assertEqual(ready['layout_verification'], 'unavailable')
                    self.assertEqual(ready['file_count'],1)
                    fields = set(ready) if fields is None else fields
                    self.assertEqual(set(ready), fields)
                    intent, _ = lifecycle.container_markers(manifest)
                    intent.touch()
                    with self.assertRaisesRegex(ValueError, 'preparation'):
                        lifecycle.container_ready(manifest)


if __name__=='__main__':unittest.main()
