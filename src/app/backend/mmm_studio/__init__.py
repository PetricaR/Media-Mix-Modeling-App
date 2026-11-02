"""MMM Studio — standalone Marketing Mix Modeling & forecasting application."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__version__ = "0.1.0"

_mpl_dir = Path(tempfile.gettempdir()) / "mmm_studio_mpl"
_mpl_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_dir))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
