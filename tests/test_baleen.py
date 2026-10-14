from collections import Counter
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from idfs_workload import models


class Region7BaleenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=models.build('baleen')

    def native_module(self):
        self.assertIsNotNone(importlib.util.find_spec('idfs_workload.baleen'),
                             'Region7 trace compiler is not implemented')
        from idfs_workload import baleen
        return baleen

    def test_region7_inventory_is_one_file_per_trace_key(self):
        m=self.model
        self.assertEqual(sum(b['files'] for b in m['bins']),57311)
        self.assertEqual(sum(b['files']*b['size_bytes'] for b in m['bins']),387983605760)
        self.assertEqual(m['provenance']['region'],'Region7')
        self.assertEqual(m['static_native']['source_operations'],288539)
        self.assertEqual(m['provenance']['source_operations'],{'GET':247264,'PUT':41275})
        self.assertNotIn('idfs_expansion',m['provenance'])
        self.assertNotIn('replication_mapping',m['bins'][0])

    def test_parser_uses_shard_identity_multiplicity_and_aligned_transfers(self):
        module=self.native_module()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.trace'
            path.write_text('# block_id io_offset io_size op_time op_name user_namespace user_name rs_shard_id op_count host_name\n'
                            '7 1 4096 100 2 0 0 3 4 20\n'
                            '7 4096 4096 101 4 0 0 3 2 99\n'
                            '7 0 8192 102 2 0 0 4 1 20\n')
            profile=module.parse_trace(path,trace_url='fixture')
            self.assertEqual(profile['key_columns'][:2],['block_id','rs_shard_id'])
            self.assertEqual(profile['keys'],[
                [7,3,8192,6,4,2,[[4096,2],[8192,4]],[[8192,4]],[[4096,2]]],
                [7,4,8192,1,1,0,[[8192,1]],[[8192,1]],[]]])
            self.assertEqual(profile['provenance']['source_operations'],{'GET':5,'PUT':2})
            self.assertEqual(profile['inventory_bytes'],4194304)
            model=module.from_profile(profile,max_bins=10)
            self.assertEqual(model['static_native']['native_tv'],0)

    def test_profile_membership_counts_and_histograms_are_conserved(self):
        module=self.native_module();p=module.load_profile();m=self.model
        keys=p['keys'];membership=[];lookup={b['id']:b for b in m['bins']}
        expected_hist=Counter();actual_hist=Counter();total=sum(k[3] for k in keys)
        error=0
        for key in keys:
            for size,count in key[6]:expected_hist[size]+=count
        lanes=m['profiles']['current'][0]['lanes']
        for b in m['bins']:
            indices=b['source_key_indices'];membership.extend(indices)
            self.assertEqual(b['files'],len(indices))
            weight=sum(l['weight'] for l in lanes if l['bin']==b['id'])
            self.assertEqual(weight,sum(keys[i][3] for i in indices))
            per_file=weight/total/b['files']
            error+=sum(abs(keys[i][3]/total-per_file) for i in indices)/2
            for i in indices:
                self.assertEqual(b['size_bytes'],((keys[i][2]+2**21-1)//2**21)*2**21)
        for lane in lanes:
            indices=lookup[lane['bin']]['source_key_indices']
            count_col=4 if lane['operation']=='read' else 5
            hist_col=7 if lane['operation']=='read' else 8
            self.assertEqual(lane['weight'],sum(keys[i][count_col] for i in indices))
            expected=Counter()
            for i in indices:
                for size,count in keys[i][hist_col]:expected[size]+=count
            self.assertEqual(Counter(dict(lane['xfersize'])),expected)
            for size,count in lane['xfersize']:actual_hist[size]+=count
        self.assertEqual(sorted(membership),list(range(57311)))
        self.assertEqual(actual_hist,expected_hist)
        self.assertLess(error,.04)
        self.assertAlmostEqual(m['static_native']['native_tv'],error,places=10)

    def test_trace_native_capacity_policy_and_timed_access_design(self):
        m=self.model
        self.assertIn('capacity_policy',m)
        self.assertEqual(m['capacity_policy']['kind'],'trace_inventory')
        self.assertEqual(m['capacity_policy']['expected_bytes'],387983605760)
        self.assertEqual(m['profiles']['baseline'],m['profiles']['current'])
        phase=m['profiles']['current'][0]
        self.assertEqual((phase['name'],phase['seconds']),('native_all',600))
        self.assertLessEqual(len(phase['lanes']),256)
        self.assertTrue(all(l['fileio']=='random' and l['fileselect']=='random' and 'stopafter' not in l for l in phase['lanes']))


if __name__=='__main__':unittest.main()
