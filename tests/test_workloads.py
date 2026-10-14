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
                             '400 GiB workload generator is not implemented')

    def test_capacity_expands_counts_without_changing_size_classes(self):
        self.require_models()
        for case, model in self.built.items():
            if case=='baleen':continue
            source = self.models.load_source(case)
            capacity = sum(b['files']*b['size_bytes'] for b in model['bins'])
            self.assertTrue(395*2**30 <= capacity <= 405*2**30, case)
            self.assertGreater(sum(b['files'] for b in model['bins']),
                               sum(b['files'] for b in source['bins']))
            if case=='ai_inference':
                self.assertEqual({b['logical_size_bytes'] for b in model['bins']},
                                 {b['logical_size_bytes'] for b in source['bins']})
            else:
                self.assertEqual({b['size_bytes'] for b in model['bins']},
                                 {b['size_bytes'] for b in source['bins']})

    def test_original_stages_and_group_request_shares_survive_expansion(self):
        self.require_models()
        for case, model in self.built.items():
            if case=='baleen':continue
            source = self.models.load_source(case)
            source_bins = {b['id']:b for b in source['bins']}
            new_bins = {b['id']:b for b in model['bins']}
            for before, after in zip(source['profiles']['current'], model['profiles']['current']):
                self.assertEqual((after['name'], after['seconds']), (before['name'], before['seconds']))
                def masses(phase, bins):
                    result = {}
                    norm = sum(l['weight'] for l in phase['lanes'])
                    for lane in phase['lanes']:
                        key = (bins[lane['bin']]['group'],lane['fileio'])
                        result[key] = result.get(key,0)+lane['weight']/norm
                    return result
                expected, actual = masses(before,source_bins), masses(after,new_bins)
                self.assertEqual(set(actual),set(expected))
                for key,value in expected.items():self.assertAlmostEqual(actual[key],value,places=10)
                self.assertLessEqual(len(after['lanes']),256)
                self.assertTrue(all('stopafter' not in l for l in after['lanes']))
                self.assertTrue(all(l['fileselect']=='random' for l in after['lanes']))
            self.assertEqual(sum(p['seconds'] for p in model['profiles']['current']),600)

    def test_inference_pairs_cover_every_logical_file_and_zipf_has_bounded_error(self):
        self.require_models()
        for case, model in self.built.items():
            if case=='baleen':continue
            by_group = {}
            for b in model['bins']:by_group.setdefault(b['group'],[]).append(b)
            for group,bins in by_group.items():
                count = sum(b.get('logical_files',b['files']) for b in bins)
                ideal = [r**-.99 for r in range(1,count+1)]
                z=sum(ideal);cursor=1;error=0
                for b in bins:
                    self.assertEqual(b['rank_start'],cursor)
                    n=b.get('logical_files',b['files'])
                    probability=sum(ideal[cursor-1:cursor+n-1])/z/n
                    error+=sum(abs(v/z-probability) for v in ideal[cursor-1:cursor+n-1])/2
                    self.assertEqual(b['rank_end'],cursor+n-1)
                    cursor+=n
                    if case=='ai_inference':
                        self.assertEqual(b['files'],4*n)
                        self.assertEqual(b['logical_size_bytes'],4*b['size_bytes'])
                        self.assertEqual(b['fragments_per_logical_file'],4)
                limit=.12 if case=='ai_training' else .08 if case=='graphchi' else .06
                self.assertLess(error,limit,(case,group,error))
            if case=='ai_inference':
                self.assertGreater(sum(b['logical_files'] for b in model['bins']),100)

    def test_zipf_lane_weights_match_the_partition_rank_masses(self):
        self.require_models()
        for case,model in self.built.items():
            if case=='baleen':continue
            by_group={}
            for b in model['bins']:by_group.setdefault(b['group'],[]).append(b)
            for phase in model['profiles']['current']:
                for group,bins in by_group.items():
                    active=[l for l in phase['lanes'] if l['bin'] in {b['id'] for b in bins}]
                    if not active:continue
                    group_mass=sum(l['weight'] for l in active)
                    count=sum(b.get('logical_files',b['files']) for b in bins)
                    z=math.fsum(r**-.99 for r in range(1,count+1))
                    for b in bins:
                        expected=math.fsum(r**-.99 for r in range(b['rank_start'],b['rank_end']+1))/z
                        actual=sum(l['weight'] for l in active if l['bin']==b['id'])/group_mass
                        self.assertAlmostEqual(actual,expected,places=11)

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
                self.assertIn('operation=write',text)
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
            core.render(self.built['graphchi'],data_root=data,output=out,rate='max')
            with self.assertRaisesRegex(ValueError,'mount'):
                lifecycle.preflight(out)
            self.assertFalse(data.exists())


if __name__=='__main__':unittest.main()
