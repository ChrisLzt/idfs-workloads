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
            self.assertFalse(data.exists())
            result=subprocess.run(command+['validate','--config-root',str(out)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            for p in out.glob('*/rendered/run_current.vdb'):
                text=p.read_text()
                if p.parent.parent.name == 'graph_zipf':
                    self.assertIn('rdpct=100,', text)
                    self.assertNotIn('rdpct=0,', text)
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
