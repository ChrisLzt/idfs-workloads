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
        self.assertEqual(dict(totals), {'read': 247264, 'write': 41275})

    def test_graphchi_load_and_full_update_writeback_share_each_phase(self):
        for phase in self.built['graphchi']['profiles']['current']:
            shares = self.shares(phase)
            self.assertEqual(set(shares), {('read', 'sequential', 4194304), ('write', 'sequential', 4194304)})
            self.assertAlmostEqual(shares[('read', 'sequential', 4194304)], .5)
            self.assertAlmostEqual(shares[('write', 'sequential', 4194304)], .5)

    def test_wrf_reads_inputs_and_boundaries_but_writes_outputs(self):
        model = self.built['wrf']
        groups = {b['id']: b['group'] for b in model['bins']}
        expected = [(1, 0), (0, 1), (1/9, 8/9), (0, 1), (1/9, 8/9), (1/9, 8/9), (0, 1)]
        for phase, (read, write) in zip(model['profiles']['current'], expected):
            shares = self.shares(phase)
            self.assertAlmostEqual(shares.get(('read', 'sequential', 4194304), 0), read)
            self.assertAlmostEqual(shares.get(('write', 'sequential', 4194304), 0), write)
            for lane in phase['lanes']:
                group = groups[lane['bin']]
                want = 'read' if group == 'input' or group.startswith('boundary_') else 'write'
                self.assertEqual(lane.get('operation', 'read'), want, group)

    def test_training_preserves_ses_operation_and_transfer_joint_mix(self):
        actual = self.shares(self.built['ai_training']['profiles']['current'][0])
        expected = {('read', 'sequential', 8192): .2, ('read', 'sequential', 32768): .2,
                    ('read', 'sequential', 65536): .1, ('write', 'sequential', 65536): .1,
                    ('read', 'sequential', 1048576): .2, ('write', 'sequential', 1048576): .2}
        self.assertEqual(set(actual), set(expected))
        for key, value in expected.items(): self.assertAlmostEqual(actual[key], value, places=10)

    def test_inference_preserves_ses_operation_and_access_joint_mix(self):
        actual = self.shares(self.built['ai_inference']['profiles']['current'][0])
        expected = {('read', 'sequential', 65536): .2, ('read', 'sequential', 131072): .2,
                    ('write', 'sequential', 65536): .1, ('write', 'sequential', 131072): .1,
                    ('read', 'random', 65536): .2, ('read', 'random', 131072): .2}
        self.assertEqual(set(actual), set(expected))
        for key, value in expected.items(): self.assertAlmostEqual(actual[key], value, places=10)

    def test_training_small_file_sequential_io_does_not_create_non_template_requests(self):
        model=self.built['ai_training'];bins={b['id']:b for b in model['bins']}
        for lane in model['profiles']['current'][0]['lanes']:
            size=bins[lane['bin']]['size_bytes']
            if size>131072 or lane['operation']!='read':continue
            requested=lane['xfersize']
            choices=[n for n,_ in requested] if isinstance(requested,list) else [requested]
            # Stock sequential I/O truncates a sampled request at EOF. Enumerate
            # reachable offsets; the source fixed-size FWDs only emit 8/32/64K.
            pending=[0];seen={0}
            while pending:
                offset=pending.pop()
                for n in choices:
                    actual=min(n,size-offset)
                    self.assertIn(actual,(8192,32768,65536))
                    following=(offset+actual)%size
                    if following not in seen:seen.add(following);pending.append(following)

    def test_renderer_emits_source_operations_and_enforces_the_fwd_budget(self):
        for case, model in self.built.items():
            texts, _ = core.config_texts(model, Path('/mnt/idfs/check'), 'max')
            for profile, phases in model['profiles'].items():
                actual = re.findall(r'^fwd=[^\n]*?,operation=(\w+)', texts[f'run_{profile}.vdb'], re.M)
                expected = [l.get('operation', 'read') for p in phases for l in p['lanes']]
                self.assertEqual(actual, expected, case)
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
