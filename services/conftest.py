import sys
from pathlib import Path

SERVICES_ROOT = Path(__file__).resolve().parent
for path in (SERVICES_ROOT / "foundation" / "src", SERVICES_ROOT / "demo" / "backend"):
    sys.path.insert(0, str(path))
