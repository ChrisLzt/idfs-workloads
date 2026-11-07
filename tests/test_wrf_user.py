"""WRF synthetic User contracts; no dataset access or physical I/O."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from idfs_workload import core, models

ROOT = Path(__file__).resolve().parents[1]


class WRFUserTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('idfs_workload.wrf_user'),
                             'WRF four-phase User implementation is missing')
        from idfs_workload import wrf_user
        return wrf_user

    def test_formal_suite_selects_wrf_with_two_history_identities(self):
        self.module()
        self.assertEqual(models.CASES['wrf'], 'hpc_wrf')
        self.assertEqual(len(models.CASES), 5)
        self.assertNotIn('acme', models.CASES)
        model = models.build('wrf')
        phases = model['business_phases']
        self.assertEqual([(p['name'], p['start_s'], p['end_s']) for p in phases],
                         [('startup', 0, 60), ('history_00h', 60, 150),
                          ('checkpoint_05h', 150, 510), ('history_06h', 510, 600)])
        files = model['logical_files']
        self.assertEqual(len(files), 42)
        for member in range(1, 9):
            self.assertEqual({f['role'] for f in files if f['member']==member},
                             {'input', 'boundary', 'history_00h', 'checkpoint_05h', 'history_06h'})
        operations = [{c['operation'] for c in p['channels']} for p in phases]
        self.assertEqual(operations, [{'read'}, {'read', 'write'}, {'read', 'write'}, {'write'}])
        self.assertEqual(model['profiles']['current'], model['profiles']['baseline'])
        self.assertEqual([p['seconds'] for p in model['profiles']['current']], [600])
        self.assertEqual(model['user_timeline']['slice_us'], 1000000)
        self.assertEqual(model['user_timeline']['grain_bytes'], 4194304)
        self.assertIsNone(model['user_timeline']['request_quota'])
        self.assertEqual(model['user_timeline']['alpha'], .99)
        provenance = model['provenance']
        self.assertEqual(provenance['grid'], [3751, 3001, 60])
        self.assertEqual(provenance['spacing_m'], 1000)
        self.assertEqual(provenance['source_grid'], [1501, 1201, 50])
        self.assertEqual(provenance['source_spacing_m'], 2500)
        self.assertEqual(provenance['grid_refinement']['horizontal_factor'], 2.5)
        mapping = provenance['grid_refinement']['dimension_mapping']
        self.assertEqual(mapping['bottom_top'], [49, 59])
        self.assertEqual(mapping['bottom_top_stag'], [50, 60])
        self.assertFalse(provenance['native_trace'])

    def test_container_objects_are_exclusive_and_padding_is_not_an_address(self):
        module = self.module()
        model = models.build('wrf')
        cursor = 0
        for f in model['logical_files']:
            self.assertEqual(f['base_bytes'], cursor)
            self.assertEqual(f['base_bytes'] % 4194304, 0)
            self.assertEqual(f['extent_bytes'] % 4096, 0)
            self.assertLess(f['extent_bytes'] - f['template_bytes'], 4096)
            self.assertEqual(module.reverse_address(model, cursor), (f['id'], 0))
            if f['slot_bytes'] > f['extent_bytes']:
                with self.assertRaisesRegex(ValueError, 'padding'):
                    module.reverse_address(model, cursor + f['extent_bytes'])
            cursor += f['slot_bytes']
        self.assertEqual(core.validate_model(model), cursor)
        self.assertEqual(cursor, 3417015582720)
        self.assertEqual(cursor // 4194304, 814680)
        self.assertEqual(model['capacity_policy']['kind'], 'parametric_inventory')
        with self.assertRaises(ValueError): module.reverse_address(model, -4096)
        with self.assertRaises(ValueError): module.reverse_address(model, cursor)

    def test_one_km_sixty_level_inventory_preserves_fixed_tables_and_array_shapes(self):
        self.module()
        files = models.build('wrf')['logical_files']
        self.assertEqual({f['id']: f['template_bytes'] for f in files if f['member']==0},
                         {'lookup_qs': 43764288, 'lookup_freeze': 254944848})
        expected = {'input': 61921748319, 'boundary': 833778994,
                    'history_00h': 62191747159, 'checkpoint_05h': 239943516151,
                    'history_06h': 62191747159}
        for member in range(1, 9):
            self.assertEqual({f['role']: f['template_bytes'] for f in files if f['member']==member}, expected)

    def test_shared_tables_aggregate_reads_and_each_member_has_equal_phase_weight(self):
        self.module()
        model = models.build('wrf'); files = {f['id']:f for f in model['logical_files']}
        histories = []
        for member in range(1, 9):
            roles = {f['role']:f for f in files.values() if f['member']==member}
            self.assertEqual(roles['history_00h']['rank_seed'], roles['history_06h']['rank_seed'])
            self.assertNotEqual(roles['history_00h']['base_bytes'], roles['history_06h']['base_bytes'])
            histories.append(roles['history_00h']['rank_seed'])
        self.assertEqual(len(set(histories)), 8)
        for phase in model['business_phases']:
            member_weights = {i:0 for i in range(1, 9)}
            for channel in phase['channels']:
                f = files[channel['file']]
                if f['member']==0:
                    self.assertEqual(phase['name'], 'startup')
                    self.assertEqual(channel['operation'], 'read')
                    self.assertEqual(channel['weight'], f['extent_bytes']*8)
                else:
                    member_weights[f['member']] += channel['weight']
            self.assertEqual(len(set(member_weights.values())), 1)
            self.assertGreater(member_weights[1], 0)

    def test_formal_ensemble_timeline_runs_in_packaged_user_without_data_io(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); classes = root/'classes'; classes.mkdir(); path = root/'wrf.timeline'
            path.write_text(module.timeline_text(models.build('wrf')))
            jar = module.JAVA/'wrf-user.jar'
            compiled = subprocess.run(['javac', '-J-XX:-UsePerfData', '-J-Djava.io.tmpdir='+d,
                '--release', '8', '-cp', str(jar), '-d', str(classes),
                str(ROOT/'tests/java/idfswrf/WRFEnsembleTests.java')], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout+compiled.stderr)
            result = subprocess.run(['java', '-XX:-UsePerfData', '-Xmx512m', '-Djava.io.tmpdir='+d,
                '-cp', str(jar)+os.pathsep+str(classes), 'idfswrf.WRFEnsembleTests', str(path)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('no_physical_io=true', result.stdout)

    def test_render_emits_one_stock_user_rd_and_never_creates_data(self):
        self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); data = root/'absent'; bundle = root/'bundle'
            manifest = core.render(models.build('wrf'), data_root=data, output=bundle, rate='max')
            self.assertFalse(data.exists())
            self.assertEqual((manifest['max_run_fwd'], manifest['total_run_wd']), (0, 1))
            self.assertEqual(len(manifest['profiles']['current'][0]['business_phases']), 4)
            text = (bundle/'run_current.vdb').read_text()
            self.assertEqual(sum(l.startswith('rd=') for l in text.splitlines()), 1)
            self.assertIn('iorate=max,elapsed=600,interval=1,warmup=0', text)
            self.assertIn('user=(idfswrf.TimelineWorkload,wrf.timeline,', text)
            self.assertEqual(core.validate_bundle(bundle)['asset_sha256'], manifest['asset_sha256'])
            for name in ('wrf.timeline', 'file-map.csv', 'wrf-user.jar'):
                self.assertIn(name, manifest['asset_sha256'])
            (bundle/'wrf.timeline').write_text('modified')
            with self.assertRaisesRegex(ValueError, 'asset'): core.validate_bundle(bundle)
            self.assertFalse(data.exists())

    def test_model_rejects_quota_changed_hotness_layout_or_short_stage(self):
        self.module()
        model = models.build('wrf')
        for mutate in (lambda m: m['user_timeline'].update(request_quota=100),
                       lambda m: m['user_timeline'].update(alpha=.5),
                       lambda m: m['user_timeline'].update(grain_bytes=1048576),
                       lambda m: m['business_phases'][0].update(end_s=5),
                       lambda m: m['logical_files'][1].update(base_bytes=0)):
            bad = copy.deepcopy(model); mutate(bad)
            with self.assertRaises(ValueError): core.validate_model(bad)

    def test_symlinked_asset_is_rejected_before_writing_other_assets(self):
        self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); bundle = root/'bundle'; bundle.mkdir()
            external = root/'untouched'; external.write_text('original')
            (bundle/'wrf.timeline').symlink_to(external)
            with self.assertRaises(ValueError):
                core.render(models.build('wrf'), data_root=root/'absent', output=bundle, rate='max')
            self.assertEqual(external.read_text(), 'original')
            self.assertEqual({p.name for p in bundle.iterdir()}, {'wrf.timeline'})

    def test_sampling_and_stock_adapter_offline_without_physical_io(self):
        module = self.module()
        jar = Path(os.environ.get('VDBENCH_HOME', '/home/chris/PDSL/vdbench50406'))/'vdbench.jar'
        self.assertTrue(jar.is_file(), 'stock 5.04.06 JAR required for offline compatibility check')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); classes = root/'classes'; classes.mkdir()
            sources = sorted((module.JAVA/'idfswrf').glob('*.java'))
            sources += [ROOT/'tests/java/idfswrf/WRFTests.java', ROOT/'tests/java/Vdb/WRFAdapterTests.java']
            result = subprocess.run(['javac', '-J-XX:-UsePerfData', '-J-Djava.io.tmpdir='+d,
                                     '--release', '8', '-cp', str(jar), '-d', str(classes),
                                     *map(str, sources)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            for main in ('idfswrf.WRFTests', 'Vdb.WRFAdapterTests'):
                result = subprocess.run(['java', '-XX:-UsePerfData', '-Djava.io.tmpdir='+d,
                                         '-cp', str(jar)+os.pathsep+str(classes), main],
                                        capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertIn('no_physical_io=true', result.stdout)


if __name__ == '__main__': unittest.main()
