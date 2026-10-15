"""Put ``src/tests`` on ``sys.path`` so the calibration tests import ``sim.*`` as the sim tests do.

Without it ``pytest src/tests/jev_calibration`` fails to import ``sim.spend`` unless another
directory's conftest happened to run first.
"""

import sys
from pathlib import Path

_TESTS_ROOT = str(Path(__file__).resolve().parent.parent)
if _TESTS_ROOT not in sys.path:
    sys.path.insert(0, _TESTS_ROOT)
