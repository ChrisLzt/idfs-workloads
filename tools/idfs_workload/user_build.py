"""Build the external User JARs against the same pinned Vdbench and Java API."""
import argparse
from pathlib import Path
import subprocess
import tempfile
import zipfile

from .core import json_text
from .user_runtime import JAR_SHA256, sha256

PACKAGE = Path(__file__).parent
COMMON = PACKAGE/'common_user_java/idfscommon'
BUILDS = {
    'fast': ('fast_user_java', 'fast-user.jar', 'idfsfast'),
    'baleen': ('baleen_user_java', 'baleen-user.jar', 'idfsbaleen'),
    'graph': ('graph_zipf_java', 'graph-zipf-user.jar', 'idfsgraphzipf'),
    'wrf': ('wrf_user_java', 'wrf-user.jar', 'idfswrf'),
    'ai': ('ai_user_java', 'ai-user.jar', 'idfsai'),
}


def source_files(key):
    directory, _, namespace = BUILDS[key]
    sources = sorted((PACKAGE/directory/namespace).glob('*.java'))
    if not sources:
        raise ValueError(f'missing Java sources for {key}')
    if key == 'fast':
        sources.append(PACKAGE/'graph_zipf_java/idfsgraphzipf/ZipfDistribution.java')
    return [*sorted(COMMON.glob('*.java')), *sources]


def source_hashes(key):
    return {source.relative_to(PACKAGE).as_posix(): sha256(source) for source in source_files(key)}


def build(key, stock_jar, output=None):
    directory, name, _ = BUILDS[key]
    stock_jar = Path(stock_jar).absolute()
    output = Path(output) if output is not None else PACKAGE/directory/name
    if sha256(stock_jar) != JAR_SHA256:
        raise ValueError('build requires the unmodified stock Vdbench 5.04.06 JAR')
    if output.resolve() == stock_jar.resolve():
        raise ValueError('extension output must be separate from the stock JAR')
    sources = source_files(key)
    hashes = source_hashes(key)
    namespaces = {p.parent.name for p in sources}
    with tempfile.TemporaryDirectory(prefix=f'{key}-user-build-') as temp:
        classes = Path(temp)/'classes'
        classes.mkdir()
        subprocess.run(['javac', '-J-XX:-UsePerfData', f'-J-Djava.io.tmpdir={temp}',
                        '--release', '8', '-cp', str(stock_jar), '-d', str(classes),
                        *map(str, sources)], check=True)
        extension = Path(temp)/name
        with zipfile.ZipFile(extension, 'w') as archive:
            for path in sorted(classes.rglob('*.class')):
                relative = path.relative_to(classes)
                if relative.parts[0] not in namespaces:
                    raise ValueError('a User JAR must not override any stock classes')
                bytecode = path.read_bytes()
                if int.from_bytes(bytecode[6:8], 'big') != 52:
                    raise ValueError('a User JAR must target Java 8')
                entry = zipfile.ZipInfo(relative.as_posix())
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, bytecode)
        if hashes != source_hashes(key):
            raise ValueError('User sources changed during compilation')
        metadata = dict(stock_jar_sha256=JAR_SHA256, java_class_version=52,
                        jar_sha256=sha256(extension), source_sha256=hashes)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(extension.read_bytes())
        (output.parent/'runtime.json').write_text(json_text(metadata))
    return metadata


def main(key):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json_text(build(key, args.jar, args.output)), end='')
