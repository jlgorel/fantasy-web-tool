"""Draft Help services.

Backend support for the "Draft Help" tab:

- ``rankings_source`` -- normalized historical auction/snake value + ADP
  baseline, generated from the dynamic ranking spreadsheets (see
  ``tools/build_draft_rankings.py``) into ``draft_rankings_{year}.json`` blobs.

The Excel ingestion itself lives in ``tools/`` because it depends on
``xlwings``/Excel and only runs locally; everything in this package is pure
Python so it is importable in the Flask app and unit-testable without Excel.
"""
