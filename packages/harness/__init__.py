"""Retired legacy harness namespace.

The old agent/orchestration harness has been removed. ``packages.harness.plugins``
is kept temporarily as an import-compatibility bridge to the canonical
``packages.plugins.extensions`` registry; it contains no harness implementation.
New code must not be added under this package.
"""
