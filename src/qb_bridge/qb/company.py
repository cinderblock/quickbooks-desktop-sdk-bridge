"""Which company file a session is talking to.

Dependency-free so it can be tested without QuickBooks or pywin32.
"""

from __future__ import annotations

import ntpath


def same_company_file(actual: str, expected: str) -> bool:
    """True if two company file paths name the same file.

    Compared as Windows paths: case and slash direction don't matter. A file
    reached two ways (a share and a local path, a short and a long name) does
    not compare equal — refusing a session we cannot identify is the safe way
    to be wrong.
    """
    if not actual or not expected:
        return False
    return _normalize(actual) == _normalize(expected)


def _normalize(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path.strip().strip('"')))
