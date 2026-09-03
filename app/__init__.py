"""StratiGraph Server — the s3Dgraphy access API over HTTP (P0: read-only, local)."""

# THE VERSION, and the convention is not this service's to invent.
#
#     <major EM>.<minor EM>.<the tool's own iteration>
#
# The first two segments declare which Extended Matrix language this build
# speaks; the third is its own history. **A tool cannot be more stable than the
# language it speaks**: while s3Dgraphy is `1.6.0.devN`, so is this.
#
# Measured rather than assumed: s3Dgraphy is `1.6.0.dev17`, and EM-blender-tools
# already carries `1.6.0-dev.8` in its `blender_manifest.toml` beside an
# s3dgraphy wheel of `1.6.0.dev16` — so the convention exists and was adopted
# half-way. `0.1.0.dev0` meant this service had never been versioned at all.
#
# The dialects differ and that is not a disagreement: a Blender manifest wants
# SemVer (`1.6.0-dev.8`), Python wants PEP 440 (`1.6.0.dev1`). Same convention,
# two spellings, each one legal where it lives.
#
# THE THIRD SEGMENT IS THE COORDINATE OF A TEST REPORT. Somebody trying the
# software has to be able to say «on dev2 this no longer happens», which is
# information, instead of «on the latest version», which is not.
#
# ONE PYTHON LITERAL, HERE. `app/main.py` used to declare its own — measured on
# 2026-09-18, two literals in one package, free to diverge. It now imports this
# one. `pyproject.toml` necessarily repeats the string (it is static metadata),
# and `tests/test_version.py` refuses a disagreement.
__version__ = "1.6.0.dev1"
