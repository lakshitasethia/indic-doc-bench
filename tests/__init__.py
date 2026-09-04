"""Marks `tests` as a package.

Two suites import shared ground truth from a sibling (`from tests.test_score
import GT`). Without this file, that resolves only when the repository root
happens to be on `sys.path` -- which `python -m pytest` arranges and the bare
`pytest` console script does not. Locally the first form was always used; CI
runs the second, so collection failed there and only there.
"""
