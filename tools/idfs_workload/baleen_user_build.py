"""Build the Baleen User extension."""
from .user_build import build as build_extension, main


def build(jar, output=None):
    return build_extension('baleen', jar, output)


if __name__ == '__main__':
    main('baleen')
