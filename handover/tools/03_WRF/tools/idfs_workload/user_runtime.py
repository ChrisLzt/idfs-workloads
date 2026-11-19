"""Launch independent User modules with the pinned stock Vdbench runtime."""
import hashlib
import os
from pathlib import Path

JAR_SHA256 = '8a87321b1b3cd6b60a5251a149359b628dd95a52b93efd7a990703478cfbf4d5'


def execution(bundle, vdbench, config, output):
    bundle = Path(bundle).absolute()
    vdbench = Path(vdbench).absolute()
    if hashlib.sha256((vdbench.parent/'vdbench.jar').read_bytes()).hexdigest() != JAR_SHA256:
        raise ValueError('User workloads require the unmodified Vdbench5.04.06 JAR')
    env = os.environ.copy()
    for key in ('JDK_JAVA_OPTIONS', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS'):
        env.pop(key, None)
    env['VDBENCH_HOME'] = str(vdbench.parent)
    return dict(args=[str(bundle/'vdbench'), '-f', str(config), '-o', str(output)],
                cwd=bundle, env=env)
