"""orgkit engine package.

When installed into <target>/.org/, each module in this package is a
standalone script as well as an importable module.  The package init
does nothing at runtime — it just makes `from orgkit.X import Y` work
during development and in setup.py.
"""
