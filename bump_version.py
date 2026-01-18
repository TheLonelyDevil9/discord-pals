#!/usr/bin/env python3
"""
Discord Pals - Version Bump Script

Usage:
    python bump_version.py          # Bump patch version (1.0.0 -> 1.0.1)
    python bump_version.py minor    # Bump minor version (1.0.0 -> 1.1.0)
    python bump_version.py major    # Bump major version (1.0.0 -> 2.0.0)
    python bump_version.py 1.2.3    # Set specific version
"""

import re
import sys
import os


def get_version_file_path():
    """Get path to version.py relative to this script."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'version.py')


def read_version():
    """Read current version from version.py."""
    version_file = get_version_file_path()
    with open(version_file, 'r') as f:
        content = f.read()

    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if match:
        return match.group(1)
    raise ValueError("Could not find __version__ in version.py")


def write_version(version):
    """Write new version to version.py."""
    version_file = get_version_file_path()
    content = f'''"""
Discord Pals - Version Information
"""

__version__ = "{version}"
VERSION = __version__
'''
    with open(version_file, 'w') as f:
        f.write(content)


def parse_version(version_str):
    """Parse version string into tuple of ints."""
    parts = version_str.split('.')
    if len(parts) != 3:
        raise ValueError(f"Invalid version format: {version_str} (expected X.Y.Z)")
    return tuple(int(p) for p in parts)


def format_version(version_tuple):
    """Format version tuple as string."""
    return '.'.join(str(p) for p in version_tuple)


def bump_version(current, bump_type):
    """Bump version based on type."""
    major, minor, patch = parse_version(current)

    if bump_type == 'major':
        return format_version((major + 1, 0, 0))
    elif bump_type == 'minor':
        return format_version((major, minor + 1, 0))
    elif bump_type == 'patch':
        return format_version((major, minor, patch + 1))
    else:
        # Assume it's a specific version
        parse_version(bump_type)  # Validate format
        return bump_type


def main():
    bump_type = sys.argv[1] if len(sys.argv) > 1 else 'patch'

    try:
        current = read_version()
        new_version = bump_version(current, bump_type)
        write_version(new_version)
        print(f"Version bumped: {current} -> {new_version}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
