"""Build the WRF User extension."""
from .user_build import build as build_extension, main


def build(jar, output=None):
    return build_extension('wrf', jar, output)


if __name__ == '__main__':
    main('wrf')
