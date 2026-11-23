import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def built(*names: str) -> bool:
    """True when the processed datasets exist (they are not in the repository; tests needing them skip)."""
    from indintel.paths import PROCESSED
    return all((PROCESSED / f"{n}.parquet").exists() for n in names)


def requires(*names: str):
    return pytest.mark.skipif(not built(*names), reason=f"needs built data: {', '.join(names)}")
