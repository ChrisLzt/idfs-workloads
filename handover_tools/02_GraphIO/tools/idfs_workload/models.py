"""Build the packaged workload from its frozen inputs."""

CASES = {'graph': 'graph_zipf'}
DEFAULT_TARGET_BYTES = 400 * 2**30


def build(case, target_bytes=None):
    if case not in CASES:
        raise ValueError('unsupported workload: ' + case)
    if target_bytes is not None and (
            type(target_bytes) is not int or target_bytes != DEFAULT_TARGET_BYTES):
        raise ValueError('the archived inventory has a fixed capacity')
    from .graph_zipf import build as build_case
    return build_case()
