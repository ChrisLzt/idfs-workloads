"""User module for the workload packaged in this delivery."""


def implementation(model):
    if model.get('id') != 'ai_training_ses':
        raise ValueError('unsupported workload identity in this archive')
    from . import ai_user
    return ai_user


def extension_jar(model):
    implementation(model)
    return 'ai-user.jar'
