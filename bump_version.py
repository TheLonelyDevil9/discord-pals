#!/usr/bin/env python3
"""
Discord Pals - Version Bump Script

Usage:
    python bump_version.py                    # Bump patch (1.0.0 -> 1.0.1)
    python bump_version.py minor              # Bump minor (1.0.0 -> 1.1.0)
    python bump_version.py major              # Bump major (1.0.0 -> 2.0.0)
    python bump_version.py 1.2.3              # Set specific version

Options:
    --tag         Create a git tag for the new version
    --no-tag      Skip git tag creation (default)
    --message "X" Custom message for changelog entry

Examples:
    python bump_version.py minor --tag
    python bump_version.py patch --tag --message "Fixed login bug"
"""

import re
import sys
import os
import subprocess
from datetime import datetime


def get_script_dir():
    """Get directory where this script lives."""
    return os.path.dirname(os.path.abspath(__file__))


def get_version_file_path():
    """Get path to version.py relative to this script."""
    return os.path.join(get_script_dir(), 'version.py')


def get_changelog_path():
    """Get path to CHANGELOG.md."""
    return os.path.join(get_script_dir(), 'CHANGELOG.md')


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


def get_recent_commits(since_tag=None):
    """Get commit messages since last tag or recent commits."""
    try:
        if since_tag:
            cmd = ['git', 'log', f'{since_tag}..HEAD', '--pretty=format:- %s']
        else:
            cmd = ['git', 'log', '-10', '--pretty=format:- %s']

        result = subprocess.run(
            cmd,
            cwd=get_script_dir(),
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_last_tag():
    """Get the most recent git tag."""
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--abbrev=0'],
            cwd=get_script_dir(),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def update_changelog(new_version, message=None):
    """Update CHANGELOG.md with new version entry."""
    changelog_path = get_changelog_path()
    date_str = datetime.now().strftime('%Y-%m-%d')

    # Get commits since last tag for changelog
    last_tag = get_last_tag()
    commits = get_recent_commits(last_tag)

    # Build changelog entry
    entry_lines = [f"## [v{new_version}] - {date_str}", ""]

    if message:
        entry_lines.append(message)
        entry_lines.append("")

    if commits:
        entry_lines.append("### Changes")
        entry_lines.append("")
        entry_lines.append(commits)
        entry_lines.append("")

    new_entry = '\n'.join(entry_lines)

    # Read existing changelog or create new
    if os.path.exists(changelog_path):
        with open(changelog_path, 'r', encoding='utf-8') as f:
            existing = f.read()

        # Insert after header
        if '# Changelog' in existing:
            parts = existing.split('\n## ', 1)
            if len(parts) == 2:
                updated = parts[0] + '\n' + new_entry + '## ' + parts[1]
            else:
                updated = parts[0] + '\n\n' + new_entry
        else:
            updated = f"# Changelog\n\nAll notable changes to Discord Pals.\n\n{new_entry}{existing}"
    else:
        updated = f"""# Changelog

All notable changes to Discord Pals.

{new_entry}"""

    with open(changelog_path, 'w', encoding='utf-8') as f:
        f.write(updated)

    print(f"Updated CHANGELOG.md")


def create_git_tag(version):
    """Create a git tag for the version."""
    tag_name = f"v{version}"

    try:
        # Check if tag already exists
        result = subprocess.run(
            ['git', 'tag', '-l', tag_name],
            cwd=get_script_dir(),
            capture_output=True,
            text=True
        )
        if result.stdout.strip() == tag_name:
            print(f"Tag {tag_name} already exists, skipping")
            return False

        # Create tag
        result = subprocess.run(
            ['git', 'tag', '-a', tag_name, '-m', f'Release {tag_name}'],
            cwd=get_script_dir(),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"Created git tag: {tag_name}")
            print(f"  Push with: git push origin {tag_name}")
            return True
        else:
            print(f"Failed to create tag: {result.stderr}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error creating tag: {e}", file=sys.stderr)
        return False


def parse_args(args):
    """Parse command line arguments."""
    bump_type = 'patch'
    create_tag = False
    message = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--tag':
            create_tag = True
        elif arg == '--no-tag':
            create_tag = False
        elif arg == '--message' or arg == '-m':
            i += 1
            if i < len(args):
                message = args[i]
        elif arg in ('patch', 'minor', 'major') or re.match(r'^\d+\.\d+\.\d+$', arg):
            bump_type = arg
        elif not arg.startswith('-'):
            bump_type = arg
        i += 1

    return bump_type, create_tag, message


def main():
    args = sys.argv[1:]
    bump_type, create_tag, message = parse_args(args)

    try:
        current = read_version()
        new_version = bump_version(current, bump_type)

        # Update version file
        write_version(new_version)
        print(f"Version bumped: {current} -> {new_version}")

        # Update changelog
        update_changelog(new_version, message)

        # Create git tag if requested
        if create_tag:
            create_git_tag(new_version)

        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
