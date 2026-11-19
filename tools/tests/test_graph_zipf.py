"""Offline graph Zipf checks; never open benchmark data or schedule native I/O."""
import os
import importlib.util
import copy
import json
import errno
import stat
import hashlib
import shutil
import zipfile
from types import SimpleNamespace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
JAR = Path(os.environ.get('VDBENCH_HOME', '/home/chris/PDSL/vdbench50406'))/'vdbench.jar'


class GraphZipfTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('idfs_workload.graph_zipf'),
                             'the new existing-file graph workload is missing')
        from idfs_workload import graph_zipf
        return graph_zipf

    def test_default_suite_selects_the_current_five_workloads(self):
        from idfs_workload import models, core
        self.assertIn('graph', models.CASES)
        self.assertEqual(len(models.CASES), 5)
        self.assertEqual(models.CASES['graph'], 'graph_zipf')
        model = models.build('graph')
        self.assertEqual(core.validate_model(model), 4398046511104)
        self.assertEqual(model['profiles']['current'][0]['lanes'][0]['xfersize'], 32)
        self.assertEqual(model['profiles']['current'][0]['lanes'][0]['operation'], 'read')
        self.assertEqual(model['user_zipf']['alpha'], 0.99)
        self.assertEqual(model['user_zipf']['object_count'], 1048576)
        self.assertEqual(model['input_policy']['mode'], 'existing_file')
        with self.assertRaises(ValueError):
            models.build('flashgraph')

    def test_render_uses_exact_existing_file_and_never_emits_prepare(self):
        module = self.module()
        from idfs_workload import core
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); data = base/'never-created'; out = base/'bundle'
            file = data/'test.graph.edge.props'
            m = core.render(module.build(), data_root=data, data_file=file, output=out, rate='max')
            self.assertFalse(data.exists())
            self.assertEqual(m['data_file'], str(file))
            self.assertEqual(m['file_count'], 1)
            self.assertEqual(m['capacity_bytes'], 4398046511104)
            self.assertEqual(set(m['config_sha256']), {'run_current.vdb', 'run_baseline.vdb'})
            self.assertFalse((out/'prepare_data.vdb').exists())
            text = (out/'run_current.vdb').read_text()
            sd = [l for l in text.splitlines() if l.startswith('sd=')]
            self.assertEqual(len(sd), 1)
            self.assertIn('lun='+str(file)+',', sd[0])
            self.assertIn('size=4398046511104,', sd[0])
            self.assertIn('openflags=o_direct', sd[0])
            self.assertIn('xfersize=32,', text)
            self.assertIn('rdpct=100,', text)
            self.assertIn('iorate=max,elapsed=600,interval=1,warmup=0', text)
            self.assertEqual(sum(l.startswith('rd=') for l in text.splitlines()), 1)
            self.assertEqual(core.validate_bundle(out)['data_file'], str(file))

    def test_multi_client_render_shares_file_and_keeps_total_sd_budget(self):
        module = self.module()
        from idfs_workload import core
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); data = base/'absent'; out = base/'bundle'
            m = core.render(module.build(), data_root=data, output=out, rate='max',
                            clients=['reader-a', 'reader-b'], threads=16)
            sds = [l for l in (out/'run_current.vdb').read_text().splitlines() if l.startswith('sd=')]
            self.assertEqual(len(sds), 2)
            self.assertTrue(all('lun='+m['data_file']+',' in l and 'threads=16,' in l for l in sds))
            self.assertEqual(m['file_count'], 1)
            self.assertEqual(m['sd_count'], 2)
            self.assertFalse(data.exists())
            for clients in [['same', 'same'], ['bad,parameter'], [f'h{i}' for i in range(513)]]:
                with self.assertRaises(ValueError):
                    core.render(module.build(), data_root=data, output=base/'bad', rate='max', clients=clients)

    def test_model_and_config_tampering_are_rejected(self):
        module = self.module()
        from idfs_workload import core
        bad = module.build(); bad['profiles']['current'][0]['lanes'][0]['xfersize'] = 4096
        with self.assertRaises(ValueError): core.validate_model(bad)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/'bundle'; data = Path(d)/'absent'
            core.render(module.build(), data_root=data, output=out, rate='max')
            p = out/'run_current.vdb'; p.write_text(p.read_text().replace('openflags=o_direct', 'openflags=o_sync'))
            with self.assertRaises(ValueError): core.validate_bundle(out)
            self.assertFalse(data.exists())

    def test_cli_render_and_unauthorized_prepare_do_not_create_data(self):
        self.module()
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); file = base/'absent'/'test.graph.edge.props'; output = base/'configs'
            command = [str(ROOT/'workloads.sh')]
            r = subprocess.run(command+['render', '--case', 'graph', '--data-file', str(file),
                                        '--config-root', str(output)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
            self.assertEqual(json.loads(r.stdout)['required_free_gib_with_5pct_headroom'],0)
            r = subprocess.run(command+['prepare', '--case', 'graph',
                                        '--config-root', str(output), '--results', str(base/'results')],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('--execute', r.stderr)
            self.assertFalse(file.parent.exists())
            self.assertFalse((base/'results').exists())

    def test_config_validator_rejects_extra_hooks_or_formatting(self):
        module = self.module()
        texts,_ = module.config_texts(module.build(),Path('/MUST_NOT_BE_CREATED'),'max')
        text = texts['run_current.vdb']
        module.validate_run_text(text)
        for bad in [text+'startcmd=/NOT_EXECUTED\n', text.replace('formatsds=no','formatsds=yes'),
                    text.replace('size=4398046511104','size=1048576')]:
            with self.assertRaises(ValueError): module.validate_run_text(bad)

    def test_existing_nfs_input_uses_metadata_and_no_ready_marker_or_free_space_gate(self):
        module = self.module()
        from idfs_workload import core,lifecycle
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);data=base/'input';data.mkdir();file=data/'test.graph.edge.props'
            file.write_bytes(b'offline-metadata-fixture-only')
            bundle=base/'bundle';core.render(module.build(),data_root=data,output=bundle,rate='max')
            real_lstat=Path.lstat;real_read=Path.read_bytes
            def metadata(path,*args,**kwargs):
                if path==file:return SimpleNamespace(st_mode=stat.S_IFREG|0o444,st_size=4398046511104,st_blocks=8589934592)
                return real_lstat(path,*args,**kwargs)
            def no_payload(path):
                if path==file:raise AssertionError('benchmark payload must not be read')
                return real_read(path)
            mount={'target':str(data),'fstype':'nfs4','source':'offline-nfs:/graph'}
            with patch.object(Path,'lstat',metadata),patch.object(Path,'read_bytes',no_payload),\
                 patch.object(lifecycle,'mount_info',return_value=mount),\
                 patch.object(module.os,'getxattr',side_effect=OSError(errno.ENOTSUP,'unsupported')):
                result=lifecycle.check_ready(bundle)
                self.assertEqual(result['state'],'existing-file-ready')
                self.assertEqual(result['required_free_bytes'],0)
                self.assertIsNone(result['layout'])
                self.assertIn('not exposed by NFS',result['layout_verification'])
                self.assertEqual(lifecycle.preflight(bundle)['required_free_bytes'],0)
            self.assertEqual(file.read_bytes(),b'offline-metadata-fixture-only')
            self.assertFalse((data/lifecycle.READY).exists())

    def test_wrong_existing_size_allocation_and_layout_are_rejected(self):
        module = self.module()
        from idfs_workload import core,lifecycle
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);data=base/'input';data.mkdir();file=data/'test.graph.edge.props'
            file.write_bytes(b'fixture');bundle=base/'bundle'
            core.render(module.build(),data_root=data,output=bundle,rate='max')
            real_lstat=Path.lstat
            mount={'target':str(data),'fstype':'ceph','source':'offline-ceph'}
            cases=[(4398046511072,8589934592,4194304),
                   (4398046511104,0,4194304),(4398046511104,8589934592,1048576)]
            for size,blocks,object_size in cases:
                def metadata(path,*args,**kwargs):
                    if path==file:return SimpleNamespace(st_mode=stat.S_IFREG|0o444,st_size=size,st_blocks=blocks)
                    return real_lstat(path,*args,**kwargs)
                layout=json.dumps(dict(object_size=object_size,stripe_unit=4194304,stripe_count=1)).encode()
                with patch.object(Path,'lstat',metadata),patch.object(lifecycle,'mount_info',return_value=mount),\
                     patch.object(module.os,'getxattr',return_value=layout):
                    with self.assertRaises(ValueError):lifecycle.check_ready(bundle)

    def test_run_requires_execute_and_keeps_original_jar_first_without_native_io(self):
        module=self.module()
        from idfs_workload import core,lifecycle
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);bundle=base/'bundle';data=base/'absent';stock=base/'stock'
            (stock/'jre/bin').mkdir(parents=True)
            (stock/'vdbench.jar').write_bytes(JAR.read_bytes())
            entry=stock/'vdbench';entry.write_text('#!/bin/sh\nexit 73\n');entry.chmod(0o755)
            record=base/'java-arguments.json';java=stock/'jre/bin/java'
            java.write_text('#!/usr/bin/python3\nimport json,os,sys\nopen(os.environ["RECORD"],"w").write(json.dumps(sys.argv[1:]))\n')
            java.chmod(0o755)
            core.render(module.build(),data_root=data,output=bundle,rate='max',stock_home=stock)
            with self.assertRaisesRegex(ValueError,'--execute'):
                lifecycle.run(bundle,vdbench=entry,output=base/'result')
            with self.assertRaisesRegex(ValueError,'mount'):
                lifecycle.run(bundle,vdbench=entry,output=base/'result',execute=True)
            self.assertFalse(record.exists())
            with patch.dict(os.environ,{'RECORD':str(record)}),\
                 patch.object(lifecycle,'check_ready',return_value={'state':'existing-file-ready'}):
                result=lifecycle.run(bundle,profile='current',vdbench=entry,output=base/'result',execute=True)
            args=json.loads(record.read_text())
            self.assertIn(str(stock/'vdbench.jar')+os.pathsep+str(bundle/'graph-zipf-user.jar'),args)
            self.assertIn('Vdb.Vdbmain',args)
            self.assertEqual(args[-4:],['-f',str(bundle/'run_current.vdb'),'-o',str(base/'result')])
            self.assertEqual(result['id'],'graph_zipf')
            self.assertFalse(data.exists())

    def test_remote_slave_rejects_drifted_extension_metadata_home_and_directory(self):
        module=self.module()
        from idfs_workload import core
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);bundle=base/'bundle';stock=base/'stock';other=base/'other-stock'
            record=base/'java-arguments.json'
            for home in (stock,other):
                (home/'jre/bin').mkdir(parents=True)
                (home/'vdbench.jar').write_bytes(JAR.read_bytes())
                java=home/'jre/bin/java'
                java.write_text('#!/usr/bin/python3\nimport os,json,sys\nopen(os.environ["RECORD"],"w").write(json.dumps(sys.argv[1:]))\n')
                java.chmod(0o755)
            core.render(module.build(),data_root=base/'absent',output=bundle,rate='max',stock_home=stock)
            env=os.environ.copy();env.update(RECORD=str(record),VDBENCH_HOME=str(stock));env.pop('JAVA_HOME',None)
            def call(path=bundle,override=None):
                actual=env.copy()
                if override is not None:actual['VDBENCH_HOME']=str(override)
                return subprocess.run([str(path/'vdbench'),'SlaveJvm','-m','localhost'],env=actual,capture_output=True,text=True)
            r=call();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertIn('Vdb.SlaveJvm',json.loads(record.read_text()))
            for name,payload in [('graph-zipf-user.jar',b'corrupted-extension'),
                                 ('runtime.json',b'{}\n'),('stock-home',(str(other)+'\n').encode())]:
                p=bundle/name;old=p.read_bytes();p.write_bytes(payload);record.unlink(missing_ok=True)
                r=call()
                self.assertNotEqual(r.returncode,0,name+' must fail before Java starts')
                self.assertFalse(record.exists())
                p.write_bytes(old)
            r=call(override=other)
            self.assertNotEqual(r.returncode,0,'environment must not redirect the rendered installation')
            self.assertFalse(record.exists())
            relocated=base/'relocated';shutil.copytree(bundle,relocated)
            r=call(path=relocated)
            self.assertNotEqual(r.returncode,0,'relocation requires rerendering')
            self.assertFalse(record.exists())
            self.assertFalse((base/'absent').exists())

    def test_java_sampler_and_original_user_adapter_without_storage_io(self):
        if not JAR.is_file():
            self.skipTest('stock5.04.06 JAR is unavailable')
        sources = sorted((ROOT/'idfs_workload/graph_zipf_java/idfsgraphzipf').glob('*.java'))
        sources += [ROOT/'tests/java/idfsgraphzipf/ZipfTests.java',
                    ROOT/'tests/java/Vdb/GraphZipfAdapterTests.java']
        with tempfile.TemporaryDirectory() as d:
            cmd = ['javac', '-J-XX:-UsePerfData', '-J-Djava.io.tmpdir='+d, '--release', '8',
                   '-cp', str(JAR), '-d', d, *map(str, sources)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
            extension=Path(d)/'graph-test-user.jar'
            with zipfile.ZipFile(extension,'w') as archive:
                for p in Path(d).rglob('*.class'):
                    rel=p.relative_to(d).as_posix()
                    if (rel.startswith('idfsgraphzipf/ZipfDistribution')
                            or rel=='idfsgraphzipf/ZipfWorkload.class'):
                        archive.write(p,rel)
            fingerprint=hashlib.sha256(extension.read_bytes()).hexdigest()
            for main in ['idfsgraphzipf.ZipfTests', 'Vdb.GraphZipfAdapterTests']:
                r = subprocess.run(['java', '-XX:-UsePerfData', '-Djava.io.tmpdir='+d,
                                    '-Dgraph.test.sha256='+fingerprint,
                                    '-cp', os.pathsep.join([str(JAR),str(extension),d]), main],
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
                self.assertIn('no_storage_io=true', r.stdout)


if __name__ == '__main__':
    unittest.main()
