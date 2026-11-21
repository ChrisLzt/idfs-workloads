"""Dispatch only explicitly supported stock User workloads."""


def implementation(model):
    if model['id']=='graph_zipf':
        from . import graph_zipf
        return graph_zipf
    if model['id']=='bigdata_baleen':
        from . import baleen_user
        return baleen_user
    if model['id']=='hpc_wrf':
        from . import wrf_user
        return wrf_user
    if model['id'] in ('ai_training_ses', 'ai_inference_ses'):
        from . import ai_user
        return ai_user
    raise ValueError('unsupported User workload identity')


def extension_jar(model):
    from .user_build import BUILDS
    return BUILDS[build_key(model)][1]


def build_key(model):
    return {'bigdata_fast':'fast', 'bigdata_baleen':'baleen', 'graph_zipf':'graph',
            'hpc_wrf':'wrf', 'ai_training_ses':'ai', 'ai_inference_ses':'ai'}[model['id']]
