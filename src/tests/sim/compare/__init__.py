"""Model comparison (spec 004 tasks 9 and 10, ADRs 0025 and 0028).

- `sim.compare.dataset`: the JSONL dataset (one line per persona run) and its summary;
- `sim.compare.runner`: plays a persona set against a stack and writes the dataset;
- `sim.compare.import_dataset`: loads a dataset into the usage ledger as replay rows, which the
  economics dashboard's comparison view reads (`GET /api/metrics/comparison`).

Run it with ``python -m sim.compare --help`` (see `sim.compare.__main__`).
"""
