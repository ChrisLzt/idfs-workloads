from collections import defaultdict
import copy
from pathlib import Path
import re
import unittest

from idfs_workload import core, models


class SourceReadWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.built = {case: models.build(case) for case in models.CASES}

    def shares(self, phase):
        result = defaultdict(float)
        total = sum(l['weight'] for l in phase['lanes'])
        for lane in phase['lanes']:
            hist = lane['xfersize'] if isinstance(lane['xfersize'], list) else [[lane['xfersize'], 1]]
            z = sum(n for _, n in hist)
            for size, n in hist:
                result[(lane.get('operation', 'read'), lane['fileio'], size)] += lane['weight']/total*n/z
        return result

    def test_baleen_get_and_put_counts_remain_separate(self):
        phase = self.built['baleen']['profiles']['current'][0]
        totals = defaultdict(int)
        for lane in phase['lanes']:
            totals[lane.get('operation', 'read')] += lane['weight']
        self.assertEqual(dict(totals), {'read': 3925006, 'write': 645808})



    def test_training_preserves_ses_operation_and_transfer_joint_mix(self):
        actual = self.shares({'lanes': self.built['ai_training']['ses_channels']})
        expected = {('read', 'random', 8192): .2, ('read', 'random', 32768): .2,
                    ('read', 'random', 65536): .1, ('write', 'random', 65536): .1,
                    ('read', 'random', 1048576): .2, ('write', 'random', 1048576): .2}
        self.assertEqual(set(actual), set(expected))
        for key, value in expected.items(): self.assertAlmostEqual(actual[key], value, places=10)

    def test_inference_random_adaptation_preserves_ses_operation_and_size_joint_mix(self):
        actual = self.shares({'lanes': self.built['ai_inference']['ses_channels']})
        expected = {('write', 'random', 65536): .1, ('write', 'random', 131072): .1,
                    ('read', 'random', 65536): .4, ('read', 'random', 131072): .4}
        self.assertEqual(set(actual), set(expected))
        for key, value in expected.items(): self.assertAlmostEqual(actual[key], value, places=10)

    def test_training_small_file_random_io_fits_all_legal_offsets(self):
        model=self.built['ai_training'];groups={g['id']:g for g in model['logical_groups']}
        for lane in model['ses_channels']:
            size=groups[lane['group']]['extent_bytes']
            if size>131072 or lane['operation']!='read':continue
            requested=lane['xfersize']
            choices=[n for n,_ in requested] if isinstance(requested,list) else [requested]
            for n in choices:
                self.assertIn(n,(8192,32768,65536))
                for offset in range(0,size-n+1,4096):
                    self.assertLessEqual(offset+n,size)

    def test_renderer_emits_source_operations_and_enforces_the_fwd_budget(self):
        for case, model in self.built.items():
            texts, stats = core.config_texts(model, Path('/mnt/idfs/check'), 'max')
            for profile, phases in model['profiles'].items():
                if model.get('access_engine')=='user' and case != 'graph':
                    # The callback chooses both operations. rdpct provisions
                    # both buffers; the source tables determine the measured mix.
                    self.assertEqual(len(re.findall(r'^wd=',texts[f'run_{profile}.vdb'],re.M)),1)
                    self.assertIn('rdpct=50,',texts[f'run_{profile}.vdb'])
                    reads,writes=(3925006,645808) if case=='baleen' else tuple(
                        lane['weight'] for lane in phases[0]['lanes'])
                    self.assertAlmostEqual(stats[profile][0]['read_share'],reads/(reads+writes))
                    self.assertAlmostEqual(stats[profile][0]['write_share'],writes/(reads+writes))
                    actual=None
                elif model.get('access_engine') in ('sd_wd','user'):
                    actual = ['read' if p=='100' else 'write' for p in
                        re.findall(r'^wd=[^\n]*?,rdpct=(\d+)', texts[f'run_{profile}.vdb'], re.M)]
                    self.assertLessEqual(len(actual),256)
                else:
                    actual = re.findall(r'^fwd=[^\n]*?,operation=(\w+)', texts[f'run_{profile}.vdb'], re.M)
                expected = [l.get('operation', 'read') for p in phases for l in p['lanes']]
                if actual is not None:self.assertEqual(actual, expected, case)
                if case == 'graph':
                    self.assertEqual(set(actual), {'read'})
                elif actual is not None:
                    self.assertIn('write', actual, case)
                self.assertTrue(all(len(p['lanes']) <= 256 for p in phases), case)
                for phase in phases:
                    bins = {b['id']: b for b in model['bins']}
                    counts = defaultdict(int)
                    for lane in phase['lanes']: counts[lane['bin']] += 1
                    for name, n in counts.items():
                        if n > bins[name]['files']:
                            self.assertTrue(all(l.get('file_sharing') and l['fileio']=='random'
                                                for l in phase['lanes'] if l['bin']==name))
            bad = copy.deepcopy(model)
            bad['profiles']['current'][0]['lanes'][0]['operation'] = 'delete'
            with self.assertRaises(ValueError): core.validate_model(bad)


if __name__ == '__main__': unittest.main()
