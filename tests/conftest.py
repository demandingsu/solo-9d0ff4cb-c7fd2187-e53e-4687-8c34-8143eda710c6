from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_sample_packing.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
