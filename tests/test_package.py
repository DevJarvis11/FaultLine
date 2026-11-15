import faultline
from faultline import cli


def test_package_imports() -> None:
    assert faultline.__doc__
    assert callable(cli.main)
