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
CASES=['bigdata_baleen','graph_zipf','hpc_wrf','ai_training_ses','ai_inference_ses']


class PreparationTests(unittest.TestCase):
    def preparer(self):
        self.assertIsNotNone(importlib.util.find_spec('idfs_workload.graph_prepare'),
                             'separate graph preparation is missing')
        from idfs_workload import graph_prepare
        return graph_prepare

    def test_each_workload_has_independent_prepare_check_and_test_entries(self):
        for case in CASES:
            for entry in ['prepare_data.sh','check_data.sh','run_test.sh']:
                p=ROOT/case/entry
                self.assertTrue(p.is_file(),str(p))
                self.assertTrue(os.access(p,os.X_OK),str(p))
                r=subprocess.run([str(p),'--help'],capture_output=True,text=True)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_preparation_and_test_actions_require_execute_for_all_cases(self):
        for case in ['baleen','graph','wrf','ai_training','ai_inference']:
            for action in ['prepare','run']:
                r=subprocess.run([str(ROOT/'workloads.sh'),action,'--case',case],capture_output=True,text=True)
                self.assertNotEqual(r.returncode,0)
                self.assertIn('--execute',r.stderr)

    def test_graph_writer_handles_short_writes_and_final_fragment_without_holes(self):
        prep=self.preparer()
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'tiny-offline-fixture';fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            real_write=os.pwrite;progress=[]
            def short_write(handle,buffer,offset):
                return real_write(handle,buffer[:max(1,len(buffer)//2)],offset)
            try:
                with patch.object(prep.os,'urandom',side_effect=lambda n:b'Q'*n),\
                     patch.object(prep.os,'pwrite',side_effect=short_write):
                    written=prep.write_all(fd,4097,1024,progress.append)
            finally:os.close(fd)
            self.assertEqual(written,4097)
            self.assertEqual(file.read_bytes(),b'Q'*4097)
            self.assertEqual(progress[-1],4097)

    def test_graph_writer_rejects_zero_progress_instead_of_hanging(self):
        prep=self.preparer()
        with patch.object(prep.os,'urandom',return_value=b'a'*32),\
             patch.object(prep.os,'pwrite',return_value=0):
            with self.assertRaises(OSError):prep.write_all(42,32,32,lambda n:None)

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
        from idfs_workload import core,graph_zipf
        for fail in [False,True]:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d);data=p/'data';bundle=p/'bundle';file=data/'test.graph.edge.props'
                core.render(graph_zipf.build(),data_root=data,output=bundle,rate='max')
                report=dict(state='missing-file',mount={'target':str(p),'fstype':'nfs4'},capacity_bytes=4398046511104)
                intent,ready=prep.markers(file);events=[];real_fsync=os.fsync
                def write_boundary(fd,total,chunk,progress):
                    self.assertEqual(total,4398046511104)
                    self.assertEqual(chunk,16*2**20)
                    self.assertTrue(intent.exists());os.pwrite(fd,b'small-storage-boundary-fixture',0)
                    events.append('write')
                    if fail:raise OSError('injected storage interruption')
                    return total
                def sync(fd):
                    name=Path(os.readlink(f'/proc/self/fd/{fd}')).name
                    events.append('fsync:'+name)
                    return real_fsync(fd)
                def checked(bundle,allow_preparing=False):
                    self.assertTrue(allow_preparing)
                    self.assertEqual(events,['fsync:'+intent.name,'fsync:'+data.name,
                                             'write','fsync:'+file.name])
                    self.assertTrue(intent.exists());self.assertFalse(ready.exists())
                    return dict(state='existing-file-ready',capacity_bytes=4398046511104)
                with patch.object(prep,'preflight',return_value=report),\
                     patch.object(prep,'require_layout',return_value={'layout':None}),\
                     patch.object(prep,'write_all',side_effect=write_boundary),\
                     patch.object(prep.os,'fsync',side_effect=sync),\
                     patch.object(graph_zipf,'check_ready',side_effect=checked):
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
                                             'write','fsync:'+file.name,'fsync:'+ready.name,
                                             'fsync:'+data.name,'fsync:'+data.name])
                if fail:
                    with self.assertRaisesRegex(ValueError,'preparation'):graph_zipf.check_ready(bundle)

    def test_graph_reuses_existing_ready_input_without_overwriting_it(self):
        prep=self.preparer()
        from idfs_workload import core,graph_zipf
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);data=p/'data';data.mkdir();file=data/'test.graph.edge.props';file.write_bytes(b'original-fixture')
            bundle=p/'bundle';core.render(graph_zipf.build(),data_root=data,output=bundle,rate='max')
            with patch.object(prep,'preflight',return_value={'state':'existing-file-ready'}),\
                 patch.object(prep,'write_all',side_effect=AssertionError('existing data must not be overwritten')):
                result=prep.prepare(bundle,output=p/'result',execute=True)
            self.assertTrue(result['reused_existing_data'])
            self.assertEqual(file.read_bytes(),b'original-fixture')
            self.assertFalse((p/'result').exists())

    def test_nfs_inventory_check_reports_unverified_layout_instead_of_requiring_private_xattrs(self):
        self.preparer()
        from idfs_workload import lifecycle
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);root=p/'data';anchor=root/'container';files=anchor/'vdb.1_1.dir'
            files.mkdir(parents=True);(files/'vdb_f0000.file').write_bytes(b'f'*4096)
            manifest=dict(data_root=str(root),capacity_bytes=4096,file_count=1,access_engine='user',
                          bins=[dict(id='container',anchor=str(anchor),files=1,size_bytes=4096)])
            mount={'target':str(p),'fstype':'nfs4','source':'fixture-nfs:/data'}
            with patch.object(lifecycle,'mount_info',return_value=mount),\
                 patch('idfs_workload.file_layout.os.getxattr',side_effect=OSError(errno.ENOTSUP,'no private attrs')):
                report=lifecycle.verify_inventory(manifest)
            self.assertEqual(report['file_count'],1)
            self.assertIn('not exposed by NFS',report['layout_verification'])


if __name__=='__main__':unittest.main()
