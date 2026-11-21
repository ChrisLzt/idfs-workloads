import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExpandedWorkloads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('idfs_workload') is None:
            return
        from idfs_workload import models
        cls.models = models
        cls.built = {case: models.build(case) for case in models.CASES}

    def require_models(self):
        self.assertIsNotNone(importlib.util.find_spec('idfs_workload'),
                             'workload generator is not implemented')





    def test_offline_render_and_validation_do_not_create_dataset(self):
        self.require_models()
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);data=base/'not-created';out=base/'config'
            command=[sys.executable,'-m','idfs_workload']
            result=subprocess.run(command+['render','--data-root',str(data),'--config-root',str(out)],
                                  cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([case['id'] for case in json.loads(result.stdout)['cases']],
                             ['bigdata_fast','graph_zipf','hpc_wrf','ai_training_ses','ai_inference_ses'])
            self.assertFalse(data.exists())
            result=subprocess.run(command+['render','--case','baleen','--data-root',str(data),'--config-root',str(out)],
                                  cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            bundles = sorted(out.glob('*/rendered'))
            self.assertEqual(len(bundles), 6)
            for bundle in bundles:
                with self.subTest(bundle=bundle):
                    for name in ('model.json', 'manifest.json', 'runtime.json', 'prepare_data.vdb', 'vdbench'):
                        self.assertTrue((bundle/name).is_file(), name)
                    prepare = (bundle/'prepare_data.vdb').read_text()
                    manifest = json.loads((bundle/'manifest.json').read_text())
                    self.assertEqual(set(manifest['profiles']), {'current'})
                    self.assertEqual(set(manifest['config_sha256']), {'prepare_data.vdb', 'run_current.vdb'})
                    self.assertFalse((bundle/'run_baseline.vdb').exists())
                    plan = manifest['preparation']
                    self.assertIn('user=(idfscommon.ContainerPrepare,', prepare)
                    self.assertIn('seekpct=eof,rdpct=0', prepare)
                    self.assertIn('formatsds=no', prepare)
                    self.assertIn(f'lun={plan["file"]},', prepare)
                    self.assertIn('elapsed=99999998,', prepare)
                    if manifest['id'] == 'bigdata_fast':
                        self.assertEqual(plan['transfer_bytes'], 400 * 1024)
                        self.assertEqual(plan['payload_offset'], 128 * 1024)
                        self.assertEqual(plan['stride_bytes'], 4 * 2**20)
                        self.assertEqual(plan['written_bytes'], 400 * 2**30)
                    else:
                        self.assertEqual(plan['written_bytes'], manifest['capacity_bytes'])
                        self.assertEqual(plan['payload_offset'], 0)
                        self.assertEqual(plan['stride_bytes'], 4 * 2**20)
                        self.assertEqual(plan['transfer_bytes'], 4 * 2**20)
            result=subprocess.run(command+['validate','--config-root',str(out)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            help_text=subprocess.check_output(command+['--help'],cwd=ROOT,text=True)
            for obsolete in ('--profile', '--confirm-nfs-layout', '--prepare-chunk-mib'):
                self.assertNotIn(obsolete,help_text)
            for p in out.glob('*/rendered/run_current.vdb'):
                text=p.read_text()
                if p.parent.parent.name == 'graph_zipf':
                    self.assertIn('rdpct=100,', text)
                    self.assertNotIn('rdpct=0,', text)
                elif p.parent.parent.name == 'bigdata_fast':
                    self.assertIn('rdpct=90,', text)
                else:
                    self.assertTrue('operation=write' in text or 'rdpct=0,' in text
                                    or (any(name in text for name in ('idfsbaleen.TimelineWorkload','idfswrf.TimelineWorkload','idfsai.TimelineWorkload')) and 'rdpct=50,' in text))
                self.assertNotIn('stopafter=',text)
                self.assertNotIn('format=yes',text)
            for action in ('prepare','run'):
                result=subprocess.run(command+[action,'--config-root',str(out)],cwd=ROOT,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('--execute',result.stderr)
            self.assertFalse(data.exists())

    def test_all_cases_route_each_client_to_the_one_prepared_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data, config = base/'no-data', base/'config'
            clients = ['localhost', 'runner@127.0.0.2', *(f'127.0.0.{i}' for i in range(3, 9))]
            command = [sys.executable, '-m', 'idfs_workload', 'render',
                       '--data-root', str(data), '--config-root', str(config),
                       '--clients', ','.join(clients), '--threads', '5']
            for case in ('all', 'baleen'):
                result = subprocess.run([*command, '--case', case], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            bundles = sorted(config.glob('*/rendered'))
            self.assertEqual(len(bundles), 6)
            for bundle in bundles:
                with self.subTest(bundle=bundle):
                    manifest = json.loads((bundle/'manifest.json').read_text())
                    test = (bundle/'run_current.vdb').read_text().splitlines()
                    prepare = (bundle/'prepare_data.vdb').read_text().splitlines()
                    file = manifest['preparation']['file']
                    self.assertEqual(manifest['runtime']['clients'], clients)
                    self.assertEqual(manifest['runtime']['threads'], 5)
                    self.assertEqual(manifest['sd_count'], len(clients))
                    self.assertEqual(manifest['file_count'], 1)
                    default = next(line for line in test if line.startswith('hd=default,'))
                    self.assertIn(f',shell=ssh,user={manifest["runtime"]["ssh_user"]},', default)
                    self.assertEqual([line for line in test if line.startswith('hd=h')],
                                     ['hd=h000,system=localhost', 'hd=h001,system=127.0.0.2,user=runner',
                                      *(f'hd=h{i-1:03d},system=127.0.0.{i}' for i in range(3, 9))])
                    devices = [line for line in test if line.startswith('sd=')]
                    self.assertEqual(len(devices), len(clients))
                    for i, line in enumerate(devices):
                        self.assertIn(f',host=h{i:03d},lun={file},', line)
                        self.assertIn(',threads=5,', line)
                    self.assertEqual([l for l in prepare if l.startswith('hd=') and 'system=' in l],
                                     ['hd=local,system=localhost'])
                    self.assertEqual(sum(l.startswith('sd=') for l in prepare), 1)
                    self.assertEqual(sum(l.startswith('rd=') for l in test), 1)
            self.assertFalse(data.exists())

    def test_regular_filesystem_is_rejected_before_dataset_creation(self):
        self.require_models()
        from idfs_workload import core,lifecycle
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);data=base/'not-created';out=base/'config'
            core.render(self.built['graph'],data_root=data,output=out,rate='max')
            with self.assertRaisesRegex(ValueError,'mount'):
                lifecycle.preflight(out)
            self.assertFalse(data.exists())


if __name__=='__main__':unittest.main()
