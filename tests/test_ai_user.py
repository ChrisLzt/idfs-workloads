"""AI physical-container mapping and stock User contracts; never create test data."""
from collections import defaultdict
import copy
import csv
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from idfs_workload import core, models

ROOT = Path(__file__).resolve().parents[1]
GRAIN = 4194304


class AIUserTests(unittest.TestCase):
    def test_all_ai_request_channels_use_random_offsets(self):
        for case, count in [('ai_training', 18), ('ai_inference', 12)]:
            model = models.build(case)
            self.assertEqual({c['fileio'] for c in model['ses_channels']}, {'random'})
            self.assertEqual(len(model['ses_channels']), count)
            self.assertEqual(model['user_timeline']['offset_selection'], 'iid_uniform_4k_aligned')

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('idfs_workload.ai_user'),
                             'AI physical-container User implementation is missing')
        from idfs_workload import ai_user
        return ai_user

    def test_preserves_valid_file_inventory_and_exclusive_object_count(self):
        self.module()
        expected = {
            'ai_training': (4722360451072, 6214285000704, 382965, 1481601,
                            {65536: 230934, 131072: 113740, 1048576: 19151,
                             2097152: 7656, 8388608: 3828, 134217728: 3828, 1073741824: 3828}),
            'ai_inference': (4947802324992, 4947802324992, 4007, 1179648,
                             {536870912: 1202, 1073741824: 1603, 2147483648: 1202})}
        for case, (payload, capacity, count, objects, sizes) in expected.items():
            model = models.build(case)
            self.assertEqual(model['access_engine'], 'user')
            self.assertEqual(core.validate_model(model), capacity)
            self.assertEqual(model['valid_payload_bytes'], payload)
            self.assertEqual(model['container_padding_bytes'], capacity - payload)
            self.assertEqual(sum(g['logical_files'] for g in model['logical_groups']), count)
            self.assertEqual({g['logical_size_bytes']: g['logical_files']
                              for g in model['logical_groups']}, sizes)
            self.assertEqual(model['bins'], [dict(id='container', group='ses', files=1,
                                                  size_bytes=capacity)])
            self.assertEqual(capacity // GRAIN, objects)
            self.assertEqual(model['user_timeline']['slice_us'], 1000000)
            self.assertIsNone(model['user_timeline']['request_quota'])

    def test_address_mapping_is_reversible_and_never_merges_logical_file_objects(self):
        module = self.module()
        for case, rows in [('ai_training', 382965), ('ai_inference', 16028)]:
            model = models.build(case)
            mapping = list(csv.DictReader(io.StringIO(module.file_map_text(model))))
            self.assertEqual(len(mapping), rows)
            previous_end = 0
            for row in mapping:
                base, length, slot = (int(row[k]) for k in ('base_bytes', 'extent_bytes', 'slot_bytes'))
                identity = (row['group'], int(row['logical_file']), int(row['fragment']))
                self.assertEqual(base, previous_end)
                self.assertEqual(base % GRAIN, 0)
                self.assertEqual(module.reverse_address(model, base), (*identity, 0))
                self.assertEqual(module.reverse_address(model, base + length - 1), (*identity, length - 1))
                if slot > length:
                    with self.assertRaisesRegex(ValueError, 'padding'):
                        module.reverse_address(model, base + length)
                previous_end = base + slot
            self.assertEqual(previous_end, model['bins'][0]['size_bytes'])
            with self.assertRaises(ValueError): module.reverse_address(model, -1)
            with self.assertRaises(ValueError): module.reverse_address(model, previous_end)

    def test_user_channels_preserve_the_ses_joint_request_distribution(self):
        self.module()
        expected = {
            'ai_training': {('read', 'random', 8192): .2,
                            ('read', 'random', 32768): .2,
                            ('read', 'random', 65536): .1,
                            ('write', 'random', 65536): .1,
                            ('read', 'random', 1048576): .2,
                            ('write', 'random', 1048576): .2},
            'ai_inference': {('read', 'random', 65536): .4,
                             ('read', 'random', 131072): .4,
                             ('write', 'random', 65536): .1,
                             ('write', 'random', 131072): .1}}
        for case, want in expected.items():
            model = models.build(case); actual = defaultdict(float)
            groups = {g['id']: g for g in model['logical_groups']}
            for c in model['ses_channels']:
                actual[c['operation'], c['fileio'], c['xfersize']] += c['weight']
                self.assertEqual(groups[c['group']]['extent_bytes'] % c['xfersize'], 0)
            self.assertEqual(set(actual), set(want))
            for key, mass in want.items(): self.assertAlmostEqual(actual[key], mass, places=10)

    def test_model_and_timeline_require_per_io_iid_selection(self):
        module = self.module()
        for case in ('ai_training', 'ai_inference'):
            model = models.build(case)
            self.assertEqual(model['user_timeline']['object_selection'], 'iid_inverse_cdf')
            self.assertEqual(model['user_timeline']['ios_per_object_selection'], 1)
            header = module.timeline_text(model).splitlines()[0].split('\t')
            self.assertEqual((header[0], header[8], header[9]), ('AI_SES_OBJECT3', '1', 'iid'))

    def test_size_class_request_shares_survive_the_container_mapping(self):
        self.module()
        # SES class shares use size-weighted file inventories inside each pool.
        # These literals come from the templates, independent of the generator.
        expected = {
            'ai_training': {65536: .6 * 67 * 64 / 8512,
                            131072: .6 * 33 * 128 / 8512,
                            1048576: .4 * 50 / 11690,
                            2097152: .4 * 40 / 11690,
                            8388608: .4 * 80 / 11690,
                            134217728: .4 * 1280 / 11690,
                            1073741824: .4 * 10240 / 11690},
            'ai_inference': {536870912: .15 / 1.15,
                             1073741824: .4 / 1.15,
                             2147483648: .6 / 1.15}}
        for case, want in expected.items():
            masses = defaultdict(float)
            for c in models.build(case)['ses_channels']:
                masses[int(c['group'].removeprefix('size_'))] += c['weight']
            self.assertEqual(set(masses), set(want))
            for size, mass in want.items(): self.assertAlmostEqual(masses[size], mass, places=10)

    def test_render_uses_one_ordinary_sd_and_preserves_the_uniform_control(self):
        module = self.module()
        from idfs_workload.__main__ import _validate
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for case, read in [('ai_training', .7), ('ai_inference', .8)]:
                model = models.build(case); bundle = root/case
                manifest = core.render(model, data_root=root/'absent'/case, output=bundle, rate='max')
                self.assertEqual((manifest['file_count'], manifest['max_run_fwd'], manifest['total_run_wd']), (1, 0, 1))
                self.assertAlmostEqual(manifest['profiles']['current'][0]['read_share'], read)
                self.assertEqual(_validate(bundle)['capacity_bytes'], model['bins'][0]['size_bytes'])
                current = (bundle/'run_current.vdb').read_text()
                baseline = (bundle/'run_baseline.vdb').read_text()
                self.assertEqual(sum(l.startswith('rd=') for l in current.splitlines()), 1)
                self.assertEqual(sum(l.startswith('sd=') for l in current.splitlines()), 1)
                self.assertIn('iorate=max,elapsed=600,interval=1,warmup=0', current)
                self.assertIn('user=(idfsai.TimelineWorkload,ai.timeline,current,', current)
                self.assertIn('user=(idfsai.TimelineWorkload,ai.timeline,baseline,', baseline)
                self.assertNotIn('concatenatesds', current)
                self.assertEqual(set(manifest['asset_sha256']),
                                 {'ai-user.jar', 'ai.timeline', 'file-map.csv', 'vdbench'})
                with zipfile.ZipFile(bundle/'ai-user.jar') as jar:
                    self.assertTrue(jar.namelist())
                    for name in jar.namelist():
                        self.assertTrue(name.startswith('idfsai/'))
                        self.assertEqual(int.from_bytes(jar.read(name)[6:8], 'big'), 52)
                (bundle/'file-map.csv').write_text('corrupted')
                with self.assertRaisesRegex(ValueError, 'asset'): core.validate_bundle(bundle)
            self.assertFalse((root/'absent').exists())

    def test_rejects_changed_quota_layout_mix_and_unapproved_target_capacity(self):
        self.module()
        model = models.build('ai_training')
        for mutate in (lambda m: m['user_timeline'].update(request_quota=100),
                       lambda m: m['user_timeline'].update(alpha=.5),
                       lambda m: m['user_timeline'].update(ios_per_object_selection=4),
                       lambda m: m['user_timeline'].update(object_selection='weyl_inverse_cdf'),
                       lambda m: m['logical_groups'][0].update(object_rank_seed=1),
                       lambda m: m['logical_groups'][1].update(base_bytes=0),
                       lambda m: m['logical_groups'][0].update(logical_files=1),
                       lambda m: m['ses_channels'][0].update(weight=.5)):
            bad = copy.deepcopy(model); mutate(bad)
            with self.assertRaises(ValueError): core.validate_model(bad)
        with self.assertRaises(ValueError):
            core.config_texts(model, Path('/mnt/idfs/absent'), 100)

    def test_symlinked_assets_are_rejected_before_any_asset_is_written(self):
        self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); bundle = root/'bundle'; bundle.mkdir()
            outside = root/'unchanged'; outside.write_text('original')
            (bundle/'ai.timeline').symlink_to(outside)
            with self.assertRaises(ValueError):
                core.render(models.build('ai_training'), data_root=root/'absent', output=bundle, rate='max')
            self.assertEqual(outside.read_text(), 'original')
            self.assertEqual({p.name for p in bundle.iterdir()}, {'ai.timeline'})

    def test_sampler_and_stock_adapter_without_opening_a_data_file(self):
        module = self.module()
        stock = Path(os.environ.get('VDBENCH_HOME', '/home/chris/PDSL/vdbench50406'))
        jar = stock/'vdbench.jar'
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); classes = root/'classes'; classes.mkdir()
            timelines = []
            for case in ('ai_training', 'ai_inference'):
                path = root/(case+'.timeline')
                path.write_text(module.timeline_text(models.build(case)))
                timelines.append(str(path))
            sources = sorted((module.JAVA/'idfsai').glob('*.java'))
            sources += [ROOT/'tests/java/idfsai/AITests.java', ROOT/'tests/java/Vdb/AIAdapterTests.java']
            compiled = subprocess.run(['javac', '-J-XX:-UsePerfData', '-J-Djava.io.tmpdir='+d,
                                       '--release', '8', '-cp', str(jar), '-d', str(classes),
                                       *map(str, sources)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout+compiled.stderr)
            for main in ('idfsai.AITests', 'Vdb.AIAdapterTests'):
                result = subprocess.run([str(stock/'jre/bin/java'), '-XX:-UsePerfData',
                                         '-Djava.io.tmpdir='+d, '-cp', str(jar)+os.pathsep+str(classes),
                                         main, *timelines], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertIn('no_physical_io=true', result.stdout)

    def test_stock_user_loads_tib_inventory_and_rejects_more_than_six_tib(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); classes = root/'classes'; classes.mkdir()
            accepted = root/'accepted.timeline'; boundary = root/'boundary.timeline'; rejected = root/'rejected.timeline'
            accepted_model = module.build('ai_inference', 9*2**39)
            accepted.write_text(module.timeline_text(accepted_model))
            for path, counts, capacity in [
                (boundary, {536870912:1602, 1073741824:2137, 2147483648:1603}, 6*2**40),
                (rejected, {536870912:2404, 1073741824:3206, 2147483648:2404}, 9*2**40)]:
                fixture = copy.deepcopy(accepted_model); base = 0
                for group in fixture['logical_groups']:
                    group['logical_files'] = counts[group['logical_size_bytes']]
                    group['base_bytes'] = base
                    base += group['logical_files']*group['fragments_per_logical_file']*group['slot_bytes']
                self.assertEqual(base, capacity)
                fixture['bins'][0]['size_bytes'] = capacity
                path.write_text(module.timeline_text(fixture))
            jar = module.JAVA/'ai-user.jar'
            compiled = subprocess.run(['javac', '-J-XX:-UsePerfData', '-J-Djava.io.tmpdir='+d,
                '--release', '8', '-cp', str(jar), '-d', str(classes),
                str(ROOT/'tests/java/idfsai/AICapacityTests.java')], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout+compiled.stderr)
            result = subprocess.run(['java', '-XX:-UsePerfData', '-Xmx512m', '-Djava.io.tmpdir='+d,
                '-cp', str(jar)+os.pathsep+str(classes), 'idfsai.AICapacityTests',
                str(accepted), str(boundary), str(rejected)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('no_physical_io=true', result.stdout)

    def test_generator_rejects_container_exceeding_user_capacity(self):
        module = self.module()
        for case, target in [('ai_inference', 7*2**40), ('ai_training', 5*2**40)]:
            with self.assertRaisesRegex(ValueError, '6TiB'):
                module.build(case, target)


if __name__ == '__main__': unittest.main()
