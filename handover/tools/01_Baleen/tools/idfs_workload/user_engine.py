"""User module for the workload packaged in this delivery."""


def implementation(model):
    if model.get('id') != 'bigdata_baleen':
        raise ValueError('unsupported workload identity in this archive')
    from . import baleen_user
    return baleen_user


def extension_jar(model):
    implementation(model)
    return 'baleen-user.jar'
