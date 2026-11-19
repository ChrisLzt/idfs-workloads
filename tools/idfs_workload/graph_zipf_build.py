"""Compile the external graph extension, leaving the original Vdbench JAR untouched."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile
from .graph_zipf import JAVA, EXTENSION, JAR_SHA256


def build(jar, output=None):
    jar = Path(jar).absolute();output = Path(output or JAVA/EXTENSION)
    if hashlib.sha256(jar.read_bytes()).hexdigest() != JAR_SHA256:
        raise ValueError('build requires the audited original Vdbench5.04.06 JAR')
    sources = sorted((JAVA/'idfsgraphzipf').glob('*.java'))
    if len(sources)!=2: raise ValueError('expected two graph Zipf Java sources')
    with tempfile.TemporaryDirectory(prefix='graph-zipf-build-') as tmp:
        classes = Path(tmp)/'classes';classes.mkdir()
        r = subprocess.run(['javac','-J-XX:-UsePerfData','-J-Djava.io.tmpdir='+tmp,
                            '--release','8','-cp',str(jar),'-d',str(classes),*map(str,sources)],
                           capture_output=True,text=True)
        if r.returncode: raise ValueError('graph extension compilation failed: '+r.stderr)
        output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(classes.rglob('*.class')):
                name = path.relative_to(classes).as_posix()
                if not name.startswith('idfsgraphzipf/'): raise ValueError('extension must not override stock classes')
                info = zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                info.external_attr=0o100644<<16;archive.writestr(info,path.read_bytes())
    meta = dict(stock_jar_sha256=JAR_SHA256,java_class_version=52,
                jar_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                source_sha256={p.relative_to(JAVA).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (output.parent/'runtime.json').write_text(json.dumps(meta,sort_keys=True,indent=2)+'\n')
    return meta


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--jar',type=Path,required=True)
    p.add_argument('--output',type=Path,default=JAVA/EXTENSION);a=p.parse_args()
    print(json.dumps(build(a.jar,a.output),indent=2))
