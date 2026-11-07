"""User module for the workload packaged in this delivery."""


def implementation(model):
    if model.get('id') != 'graph_zipf':
        raise ValueError('unsupported workload identity in this archive')
    from . import graph_zipf
    return graph_zipf


def extension_jar(model):
    implementation(model)
    return 'graph-zipf-user.jar'
