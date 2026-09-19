"""Smoke test: the src-layout package installs and imports cleanly."""


def test_package_imports():
    import galactic_unicorn

    assert galactic_unicorn.__version__
