"""User module for the workload packaged in this delivery."""


def implementation(model):
    if model.get('id') != 'hpc_wrf':
        raise ValueError('unsupported workload identity in this archive')
    from . import wrf_user
    return wrf_user


def extension_jar(model):
    implementation(model)
    return 'wrf-user.jar'
