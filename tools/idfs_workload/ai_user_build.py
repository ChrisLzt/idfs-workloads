"""Build the User extension used by both SES workloads."""
from .user_build import build as build_extension, main


def build(jar, output=None):
    return build_extension('ai', jar, output)


if __name__ == '__main__':
    main('ai')
