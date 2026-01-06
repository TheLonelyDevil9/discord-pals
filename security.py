"""
Discord Pals - Security Utilities
Path validation, authentication, and CSRF protection for the dashboard.
"""

import os
import re
import secrets
from pathlib import Path
from functools import wraps
from flask import request, Response, session, jsonify, redirect, url_for
from typing import Optional


# =============================================================================
# PATH TRAVERSAL PROTECTION
# =============================================================================

def safe_filename(name: str, allowed_ext: Optional[str] = None) -> str:
    """
    Sanitize a filename to prevent path traversal attacks.

    Args:
        name: The filename to sanitize
        allowed_ext: Optional extension to enforce (e.g., '.md', '.json')

    Returns:
        Sanitized filename

    Raises:
        ValueError: If the filename is invalid or empty after sanitization
    """
    if not name:
        raise ValueError("Filename cannot be empty")

    # Remove path separators, null bytes, and other dangerous characters
    # Allow alphanumeric, dash, underscore, dot, and space
    safe = re.sub(r'[/\\:\x00<>"|?*]', '', name)

    # Remove leading dots and spaces (prevents hidden files and .. traversal)
    safe = safe.lstrip('. ')

    # Remove trailing dots and spaces (Windows issue)
    safe = safe.rstrip('. ')

    if not safe:
        raise ValueError("Invalid filename after sanitization")

    # Enforce extension if specified
    if allowed_ext:
        if not allowed_ext.startswith('.'):
            allowed_ext = f'.{allowed_ext}'
        if not safe.lower().endswith(allowed_ext.lower()):
            # Remove any existing extension and add the correct one
            safe = os.path.splitext(safe)[0] + allowed_ext

    return safe


def safe_path(base_dir: Path, filename: str, allowed_ext: Optional[str] = None) -> Path:
    """
    Create a safe path that's guaranteed to be within the base directory.

    Args:
        base_dir: The base directory that the path must stay within
        filename: The filename (will be sanitized)
        allowed_ext: Optional extension to enforce

    Returns:
        Safe Path object within base_dir

    Raises:
        ValueError: If path traversal is detected or filename is invalid
    """
    safe_name = safe_filename(filename, allowed_ext)

    # Resolve both paths to absolute
    base_resolved = base_dir.resolve()
    full_path = (base_dir / safe_name).resolve()

    # Ensure the path is within the base directory
    try:
        full_path.relative_to(base_resolved)
    except ValueError:
        raise ValueError("Path traversal detected")

    return full_path


def validate_zip_entry(entry_name: str, allowed_files: set) -> Optional[str]:
    """
    Validate a ZIP entry name for safe extraction.

    Args:
        entry_name: The name of the ZIP entry
        allowed_files: Set of allowed filenames

    Returns:
        The safe filename if valid, None if should be skipped
    """
    # Get just the basename (no directory components)
    basename = os.path.basename(entry_name)

    # Skip if basename doesn't match entry (has path components)
    if basename != entry_name:
        return None

    # Skip if not in whitelist
    if basename not in allowed_files:
        return None

    return basename


# =============================================================================
# SECRET KEY MANAGEMENT
# =============================================================================

def get_or_create_secret_key(data_dir: Path) -> str:
    """
    Get or create a persistent secret key for Flask sessions.

    Args:
        data_dir: Directory to store the secret key file

    Returns:
        The secret key string
    """
    key_file = data_dir / '.secret_key'

    try:
        if key_file.exists():
            key = key_file.read_text().strip()
            if len(key) >= 32:  # Ensure key is long enough
                return key
    except (IOError, OSError):
        pass

    # Generate new key
    key = secrets.token_hex(32)

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key)
        # Try to set restrictive permissions (Unix-like systems)
        try:
            os.chmod(key_file, 0o600)
        except (OSError, AttributeError):
            pass  # Windows or permission error
    except (IOError, OSError):
        pass  # Use the key even if we can't persist it

    return key


# =============================================================================
# AUTHENTICATION
# =============================================================================

def check_dashboard_auth(username: str, password: str) -> bool:
    """
    Verify dashboard credentials against environment variables.

    If DASHBOARD_PASS is not set, authentication is disabled (local-only mode).

    Args:
        username: Provided username
        password: Provided password

    Returns:
        True if authenticated, False otherwise
    """
    expected_pass = os.getenv('DASHBOARD_PASS')

    # No password set = authentication disabled (local-only mode)
    if not expected_pass:
        return True

    expected_user = os.getenv('DASHBOARD_USER', 'admin')

    # Use constant-time comparison to prevent timing attacks
    user_match = secrets.compare_digest(username or '', expected_user)
    pass_match = secrets.compare_digest(password or '', expected_pass)

    return user_match and pass_match


def requires_auth(f):
    """
    Decorator to require HTTP Basic Auth on a route.

    Authentication is only enforced if DASHBOARD_PASS environment variable is set.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        username = auth.username if auth else ''
        password = auth.password if auth else ''

        if not check_dashboard_auth(username, password):
            return Response(
                'Authentication required.\n'
                'Set DASHBOARD_USER and DASHBOARD_PASS environment variables to configure.',
                401,
                {'WWW-Authenticate': 'Basic realm="Discord Pals Dashboard"'}
            )
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# CSRF PROTECTION
# =============================================================================

def generate_csrf_token() -> str:
    """
    Generate or retrieve a CSRF token for the current session.

    Returns:
        The CSRF token string
    """
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def validate_csrf_token(token: str) -> bool:
    """
    Validate a CSRF token against the session.

    Args:
        token: The token to validate

    Returns:
        True if valid, False otherwise
    """
    session_token = session.get('csrf_token')
    if not session_token or not token:
        return False
    return secrets.compare_digest(token, session_token)


def requires_csrf(f):
    """
    Decorator to require valid CSRF token on POST/PUT/DELETE requests.

    Token can be provided via:
    - Form field: csrf_token
    - Header: X-CSRF-Token
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE'):
            token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
            if not validate_csrf_token(token):
                return jsonify({"error": "Invalid or missing CSRF token"}), 403
        return f(*args, **kwargs)
    return decorated


def csrf_exempt(f):
    """
    Decorator to mark a route as exempt from CSRF protection.
    Use sparingly and only for API endpoints with other authentication.
    """
    f._csrf_exempt = True
    return f


# =============================================================================
# SESSION-BASED LOGIN
# =============================================================================

def is_auth_enabled() -> bool:
    """Check if authentication is enabled (DASHBOARD_PASS is set)."""
    return bool(os.getenv('DASHBOARD_PASS'))


def is_logged_in() -> bool:
    """Check if the current session is logged in."""
    if not is_auth_enabled():
        return True  # No auth required
    return session.get('logged_in', False)


def login_user():
    """Mark the current session as logged in."""
    session['logged_in'] = True
    session.permanent = True  # Use permanent session


def logout_user():
    """Log out the current session."""
    session.pop('logged_in', None)


def requires_login(f):
    """
    Decorator to require login on a route.

    If DASHBOARD_PASS is not set, authentication is disabled.
    Otherwise, redirects to login page if not logged in.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_auth_enabled():
            return f(*args, **kwargs)  # No auth required

        if not is_logged_in():
            return redirect(url_for('login', next=request.path))

        return f(*args, **kwargs)
    return decorated
