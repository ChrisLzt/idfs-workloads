"""Build the separate Java8 SES User extension against original Vdbench5.04.06."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from .ai_user import JAVA, JAR_SHA256, EXTENSION_JAR


def build(jar, output):
    jar = Path(jar).absolute(); output = Path(output)
    if hashlib.sha256(jar.read_bytes()).hexdigest() != JAR_SHA256:
        raise ValueError('SES extension build requires stock Vdbench5.04.06')
    sources = sorted((JAVA/'idfsai').glob('*.java'))
    if len(sources) != 2:
        raise ValueError('expected two SES Java sources')
    with tempfile.TemporaryDirectory(prefix='ai-user-build-') as tmp:
        classes = Path(tmp)/'classes'; classes.mkdir()
        subprocess.run(['javac', '-J-XX:-UsePerfData', '-J-Djava.io.tmpdir='+tmp,
                        '--release', '8', '-cp', str(jar), '-d', str(classes),
                        *map(str, sources)], check=True, capture_output=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(classes.rglob('*.class')):
                name = path.relative_to(classes).as_posix()
                if not name.startswith('idfsai/'):
                    raise ValueError('SES extension must not override stock classes')
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    meta = dict(stock_jar_sha256=JAR_SHA256, java_class_version=52,
                jar_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                source_sha256={p.relative_to(JAVA).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (output.parent/'runtime.json').write_text(json.dumps(meta, indent=2)+'\n')
    return meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=JAVA/EXTENSION_JAR)
    args = parser.parse_args()
    print(json.dumps(build(args.jar, args.output), indent=2))
