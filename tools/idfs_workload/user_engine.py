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
    implementation(model)
    return {'bigdata_baleen':'baleen-user.jar','hpc_wrf':'wrf-user.jar',
            'graph_zipf':'graph-zipf-user.jar', 'ai_training_ses':'ai-user.jar',
            'ai_inference_ses':'ai-user.jar'}[model['id']]
