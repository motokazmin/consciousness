"""Ядро HRV: источники, pipeline, БД, сводки."""

from hrv_core.constants import DB_PATH, SESSION_TAGS
from hrv_core.db import init_db, load_hour_baseline, update_session_baseline
from hrv_core.mock_verify import run_mock_verify
from hrv_core.pipeline import HRVSessionState, compute_rmssd
from hrv_core.sources import (
    AntPlusHRVSource,
    FallbackBleAntSource,
    HRVSource,
    MockHRVSource,
    PolarH10Source,
    build_source,
    require_openant,
)
from hrv_core.summary import print_session_summary, session_summary_dict

__all__ = [
    "DB_PATH",
    "SESSION_TAGS",
    "AntPlusHRVSource",
    "FallbackBleAntSource",
    "HRVSessionState",
    "HRVSource",
    "MockHRVSource",
    "PolarH10Source",
    "build_source",
    "compute_rmssd",
    "init_db",
    "load_hour_baseline",
    "print_session_summary",
    "require_openant",
    "run_mock_verify",
    "session_summary_dict",
    "update_session_baseline",
]
