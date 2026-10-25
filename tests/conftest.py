import os

import pytest

from cloverboard.logger.api import _shutdown


@pytest.fixture(autouse=True)
def reset_run_context():
    _shutdown()
    yield
    _shutdown()


@pytest.fixture
def directory_link():
    def create_link(link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
    return create_link
