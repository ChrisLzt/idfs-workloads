"""Audited 5.04.06 -s parser only, exact jar, fresh output, generated grammar."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from .core import json_text, safe_path, validate_bundle

JAR_SHA256 = '8a87321b1b3cd6b60a5251a149359b628dd95a52b93efd7a990703478cfbf4d5'


def check(bundles, *, jar, output):
    jar = Path(jar).absolute()
    if hashlib.sha256(jar.read_bytes()).hexdigest() != JAR_SHA256:
        raise ValueError('parser-only mode requires the audited Vdbench 5.04.06 jar')
    java = jar.parent/'jre'/'bin'/'java'
    if not java.is_file():
        java = Path(os.environ.get('JAVA_HOME','/usr'))/'bin'/'java'
    output = safe_path(output)
    if output.exists():
        raise ValueError('parser output directory must not exist')
    configs = []
    for bundle in bundles:
        manifest = validate_bundle(bundle)
        data = Path(manifest['data_root'])
        bundle = safe_path(bundle)
        if output == data or data in output.parents or output in data.parents:
            raise ValueError('parser output must be outside all data directories')
        if output == bundle or bundle in output.parents or output in bundle.parents:
            raise ValueError('parser output must be separate from configuration bundle')
        for name in sorted(manifest['config_sha256']):
            config = bundle/name
            # Byte-for-byte model regeneration above excludes includes, hooks and auxreport.
            from .user_engine import extension_jar
            extension=extension_jar(manifest) if manifest.get('access_engine')=='user' else None
            configs.append((manifest['id'],config,manifest['config_sha256'][name],extension,bundle))
    env = os.environ.copy()
    for key in ('JDK_JAVA_OPTIONS','JAVA_TOOL_OPTIONS','_JAVA_OPTIONS'):
        env.pop(key,None)
    output.mkdir(parents=True,exist_ok=False)
    report={'jar':str(jar),'jar_sha256':JAR_SHA256,'mode':'-s parser simulation; no workload execution','results':[]}
    for model_id,config,expected,extension,bundle in configs:
        # Copy already-verified bytes into isolated working directory; avoid input TOCTOU.
        payload=config.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=expected:
            raise ValueError('config changed during parser validation')
        dest=output/(model_id+'__'+config.stem)
        dest.mkdir()
        with tempfile.TemporaryDirectory(prefix='single-v2-parser-') as tmp:
            local=Path(tmp)/'generated.vdb'; local.write_bytes(payload)
            classpath=os.pathsep.join((str(jar),str(bundle/extension))) if extension else str(jar)
            command=[str(java),'-XX:-UsePerfData',f'-Djava.io.tmpdir={tmp}','-Xms64m','-Xmx512m','-cp',classpath,'Vdb.Vdbmain','-s','-f',str(local),'-o',str(dest/'reports')]
            result=subprocess.run(command,cwd=bundle if extension else tmp,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
        (dest/'console.log').write_text(result.stdout)
        success=result.returncode==0 and 'Vdbench simulation completed successfully.' in result.stdout
        report['results'].append({'config':str(config),'config_sha256':expected,'command':command,'exit_status':result.returncode,'passed':success})
        (output/'summary.json').write_text(json_text(report))
        if not success:
            raise ValueError(f'Vdbench parser rejected {config}; see {dest}/console.log')
    report['passed']=len(configs)
    (output/'summary.json').write_text(json_text(report))
    return report
