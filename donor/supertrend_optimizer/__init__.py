"""
SuperTrend Engine — active donor package (Mode C runtime root).

This file makes ``donor/supertrend_optimizer/`` a regular package (PEP 328)
rather than a namespace package. It is the SINGLE source of truth for the
``supertrend_optimizer`` top-level name across both pipelines:

    * WF Grid pipeline (``wf_grid/``) imports engine modules from here.
    * SuperTrend Tester pipeline (``donor TESTER/``) ALSO resolves
      ``supertrend_optimizer.*`` to this package once ``donor/`` precedes
      ``donor TESTER/`` in ``sys.path`` (see ``donor TESTER/tests/conftest.py``).

Why a regular package and not a namespace package?
    Plan v0.5.2 §15 #9 / WP-T3 step 0a requires that ``supertrend_optimizer``
    be a regular package so the smoke assertion
    ``supertrend_optimizer.__file__ == donor/supertrend_optimizer/__init__.py``
    can be enforced. Namespace packages have ``__file__ = None`` which would
    silently regress to whichever directory wins by accident.

Authorized inside WP-T2 as a narrow B-2 unblocker (steps 0a-0c only).
WP-T3 step 0d (hard-delete duplicates from ``donor TESTER/``) is NOT executed
here.
"""

__version__ = "2.0.0"
