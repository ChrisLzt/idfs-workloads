"""IDFS workload CLI; default preview and validation do not access test data."""
import argparse
import json
import os
from pathlib import Path
import sys

from . import core,lifecycle,models

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_DATA=Path('/mnt/idfs/workloads_400g')


def _validate(bundle):
    manifest=core.validate_bundle(bundle)
    model=json.loads((bundle/'model.json').read_text())
    capacity=manifest['capacity_bytes']
    if 'capacity_policy' in model:
        policy=model['capacity_policy']
        valid_kind = (policy['kind']=='trace_inventory' or
                      (policy['kind']=='parametric_inventory' and model['id']=='hpc_wrf') or
                      (policy['kind']=='existing_file_inventory' and model['id']=='graph_zipf') or
                      (policy['kind']=='aligned_container_inventory' and
                       model['id'] in ('ai_training_ses', 'ai_inference_ses')))
        if not valid_kind or capacity!=policy['expected_bytes']:
            raise ValueError('source inventory capacity mismatch')
        if not policy['min_bytes']<=capacity<=policy['max_bytes']:
            raise ValueError('source inventory capacity outside accepted range')
    else:
        expansion=model['provenance']['idfs_expansion'];target=expansion['target_bytes']
        if not .98*target<=capacity<=1.02*target:raise ValueError('capacity outside 2% target tolerance')
    if (manifest['rate']!='max' or manifest['max_run_fwd']>256
            or manifest.get('max_run_wd',0)>256
            or manifest.get('total_run_wd',0)>core.MAX_FWD):
        raise ValueError('IDFS requires max rate, <=256 channels per phase and the workload total WD budget')
    for phases in model['profiles'].values():
        for phase in phases:
            if any('stopafter' in lane for lane in phase['lanes']):raise ValueError('stopafter is not part of this suite')
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='preview',choices=['preview','render','validate','preflight','prepare','verify','run','parser-check'])
    lookup = models.CASES
    parser.add_argument('--case',default='all',choices=['all',*lookup])
    parser.add_argument('--profile',default='current',choices=['current','baseline'])
    parser.add_argument('--config-root',type=Path,default=Path(os.environ.get('CONFIG_ROOT',ROOT)))
    parser.add_argument('--data-root',type=Path,default=Path(os.environ.get('DATA_ROOT',DEFAULT_DATA)),help='used only when rendering')
    parser.add_argument('--data-file',type=Path,help='graph render only:exact path to the already prepared shared4TiB file')
    parser.add_argument('--clients',help='graph render only:comma-separated NFS client hostnames;default localhost')
    parser.add_argument('--threads',type=int,help='graph render only:I/O threads per client;default64')
    parser.add_argument('--prepare-chunk-mib',type=int,help='graph prepare only:write chunk size;default16MiB')
    parser.add_argument('--vdbench',type=Path,default=Path(os.environ.get('VDBENCH_HOME','/home/chris/PDSL/vdbench50406'))/'vdbench')
    parser.add_argument('--results',type=Path,help='new, separate results directory')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action in ('prepare','run') and not args.execute:
            raise ValueError('prepare/run require explicit --execute and user authorization')
        cases=list(models.CASES) if args.case=='all' else [args.case]
        graph_options = args.data_file is not None or args.clients is not None or args.threads is not None
        if graph_options and (args.action!='render' or 'graph' not in cases):
            raise ValueError('--data-file/--clients/--threads apply only when rendering graph')
        if args.prepare_chunk_mib is not None and (args.action!='prepare' or 'graph' not in cases):
            raise ValueError('--prepare-chunk-mib applies only to graph preparation')
        config=core.safe_path(args.config_root)
        protected=Path(__file__).parent
        if config==protected or protected in config.parents:
            raise ValueError('configuration root must not overwrite package or frozen sources')
        bundles={case:config/lookup[case]/'rendered' for case in cases}
        if args.action=='render':
            data=core.safe_path(args.data_root)
            for case in cases:
                if case=='graph':
                    core.render(models.build(case),data_root=data/'graph',output=bundles[case],rate='max',
                                data_file=args.data_file,
                                clients=args.clients.split(',') if args.clients is not None else None,
                                threads=args.threads if args.threads is not None else 64,
                                stock_home=args.vdbench.parent)
                else:
                    core.render(models.build(case),data_root=data/lookup[case],output=bundles[case],rate='max')
        reports=[_validate(bundles[c]) for c in cases]
        for case,manifest in zip(cases,reports):
            if manifest['id']!=lookup[case]:raise ValueError('bundle identity mismatch')
        roots=[core.safe_path(m['data_root']) for m in reports]
        for i,root in enumerate(roots):
            for other in roots[i+1:]:
                if root==other or root in other.parents or other in root.parents:
                    raise ValueError('overlapping dataset roots')
        total=sum(m['capacity_bytes'] for m in reports)
        if args.action in ('preview','render','validate'):
            print(core.json_text({'action':args.action,'real_io_executed':False,
                'total_gib':total/2**30,'required_free_gib_with_5pct_headroom':sum(
                    0 if m.get('input_policy',{}).get('mode')=='existing_file'
                    else (m['capacity_bytes']*105+99)//100 for m in reports)/2**30,
                'cases':[{'id':m['id'],'capacity_gib':m['capacity_bytes']/2**30,'physical_files':m['file_count'],
                          'max_run_fwd':m['max_run_fwd'],'data_root':m['data_root'],
                          'max_run_wd':m.get('max_run_wd',0),
                          'config':str(bundles[c]/f'run_{args.profile}.vdb'),
                          'phases':m['profiles'][args.profile]} for c,m in zip(cases,reports)]}),end='')
            return 0
        if args.action=='parser-check':
            if args.results is None:raise ValueError('--results required for parser-check')
            from .parser_check import check
            print(core.json_text(check(list(bundles.values()),jar=args.vdbench.parent/'vdbench.jar',output=args.results)),end='')
            return 0
        if args.action in ('preflight','prepare'):
            checks=[lifecycle.preflight(bundles[c]) for c in cases];mounts={}
            for check in checks:
                totals=mounts.setdefault(check['mount']['target'],{'required':0,'available':check['available_bytes']})
                totals['required']+=check['required_free_bytes'];totals['available']=min(totals['available'],check['available_bytes'])
            for mount,totals in mounts.items():
                if totals['required']>totals['available']:raise ValueError(f'selected datasets do not fit on {mount}: {totals}')
            if args.action=='preflight':print(core.json_text(checks),end='');return 0
        if args.action=='verify':
            print(core.json_text([lifecycle.check_ready(bundles[c]) for c in cases]),end='');return 0
        if args.results is None:raise ValueError('--results must name a new, separate directory')
        lifecycle.validate_outputs(reports,list(bundles.values()),args.results)
        lifecycle._executable(args.vdbench)
        if args.action=='run':
            for c in cases:lifecycle.check_ready(bundles[c])
        for c in cases:
            options={'vdbench':args.vdbench,'output':args.results/lookup[c],'execute':True}
            if args.action=='run':options['profile']=args.profile
            if args.action=='prepare' and c=='graph':
                options['chunk_mib']=args.prepare_chunk_mib if args.prepare_chunk_mib is not None else 16
            print(core.json_text(getattr(lifecycle,args.action)(bundles[c],**options)),end='')
        return 0
    except (ValueError,OSError,KeyError) as error:
        print('FAIL: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
