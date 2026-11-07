"""Read-only common4MiB layout checks; NFS may not export private xattrs."""
import errno
import json
import os
from pathlib import Path

GRAIN=4194304


def assert_layout(payload):
    try:
        text=payload.decode() if isinstance(payload,bytes) else payload
        values=json.loads(text) if text.lstrip().startswith('{') else dict(x.split('=',1) for x in text.split())
        actual={key:int(values[key]) for key in ('stripe_unit','stripe_count','object_size')}
    except (ValueError,KeyError,TypeError,AttributeError) as error:
        raise ValueError('cannot verify actual Ceph/IDFS file layout') from error
    if actual != dict(stripe_unit=GRAIN,stripe_count=1,object_size=GRAIN):
        raise ValueError('workload requires actual4MiB objects,4MiB stripe unit and stripe_count=1')
    return actual


def probe_layout(path, *, directory=False, stop=None):
    path=Path(path)
    while True:
        kind='dir' if directory else 'file'
        for prefix in ('ceph','idfs'):
            for suffix in ('.json',''):
                try:
                    payload=os.getxattr(path,f'{prefix}.{kind}.layout{suffix}')
                except OSError as error:
                    if error.errno in (errno.ENODATA,errno.ENOTSUP,errno.EOPNOTSUPP):continue
                    raise
                return dict(path=str(path),layout=assert_layout(payload))
        if not directory or path==Path(stop or '/') or path==path.parent:return None
        path=path.parent


def require_layout(path, *, directory=False, stop=None, fstype=None):
    result=probe_layout(path,directory=directory,stop=stop)
    if result is not None:return dict(result,verification='xattr')
    if fstype in ('nfs','nfs4'):
        return dict(path=str(path),layout=None,verification='not exposed by NFS;must be confirmed when preparing data')
    raise ValueError('cannot verify actual4MiB Ceph/IDFS layout for '+str(path))
