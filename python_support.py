"""Supported Python versions and the notice for installs that run an older one."""

import sys

# Keep this module importable on Python 3.10. The notice exists for installs that
# still run 3.10, so syntax that needs 3.11 here would stop them before it shows.
SUPPORTED_PYTHON_VERSIONS = ("3.11", "3.12", "3.13", "3.14")
_OLDEST_SUPPORTED = (3, 11)


def unsupported_python_notice(version_info=None):
    """Return the notice for an interpreter older than 3.11, or None for a supported one."""
    major, minor = tuple((version_info or sys.version_info)[:2])
    if (major, minor) >= _OLDEST_SUPPORTED:
        return None
    supported = ", ".join(SUPPORTED_PYTHON_VERSIONS[:-1]) + " and " + SUPPORTED_PYTHON_VERSIONS[-1]
    return (
        f"Python {major}.{minor} is no longer supported. Discord Pals supports Python {supported}. "
        "This version still starts, but a later update may not. Rebuild the virtual environment "
        "with a supported Python; see the upgrade notes in CHANGELOG.md."
    )


def warn_if_unsupported(warn, version_info=None) -> None:
    """Pass the notice to ``warn`` when the interpreter is older than the oldest supported version."""
    notice = unsupported_python_notice(version_info)
    if notice:
        warn(notice)
