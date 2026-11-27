"""sfzpkg - build and inspect FIZZY ORANGE SFZ instrument packages.

The package format is defined in ``docs/PROTOCOL.md``: a ZIP containing
``fizzy.json``, one or more ``.sfz`` instruments and their samples.

This package uses only the Python standard library.  ``ffmpeg`` is an optional
external tool needed only for the ``--wav`` conversion options.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["SfzPkgError", "ConversionError", "ValidationError", "__version__"]


class SfzPkgError(Exception):
    """Base class for errors that should be shown without a traceback."""


class ConversionError(SfzPkgError):
    """A patch could not be converted (unsupported or unreadable format)."""


class ValidationError(SfzPkgError):
    """A package failed validation."""
