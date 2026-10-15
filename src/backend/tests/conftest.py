"""Plugin registration for the backend tests.

pytest 9 ties a ``conftest.py`` fixture to the Directory node that was collected first. When
file arguments under ``tests/api/`` are interleaved with a file directly under ``tests/`` (for
example ``tests/api/test_slots.py tests/test_api_auth.py tests/api/test_auth.py``), ``tests/``
is collected twice, the second ``tests/api`` node never sees the fixtures and the run fails with
"fixture 'env' not found". Fixtures from a plugin module are visible everywhere, so the REST
API fixtures live in ``tests/api/support.py`` and are registered here, which keeps the result
independent of the order of the arguments.
"""

pytest_plugins = ["tests.api.support"]
