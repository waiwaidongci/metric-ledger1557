import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The services import DATABASE_URL at module import time, before any app
# startup hook runs, so point the tests at a throwaway SQLite file first.
DB_PATH = os.path.join(tempfile.mkdtemp(prefix="ledger-tests-"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH
os.environ["SEED_DEMO"] = "false"
sys.path.insert(0, ROOT)

import pytest
from fastapi.testclient import TestClient

from src import models  # noqa: F401  (registers all tables on metadata)
from src.config.database import Base, SessionLocal, engine
from src.main import app
from src.models import Metric, SourceRow, User
from src.utils.security import hash_password


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all([
            User(email="admin@hengchi.demo", name="陆知衡", role="admin",
                 password_hash=hash_password("Hengchi2026!")),
            User(email="analyst@hengchi.demo", name="宋予", role="analyst",
                 password_hash=hash_password("Hengchi2026!")),
            User(email="viewer@hengchi.demo", name="叶言", role="viewer",
                 password_hash=hash_password("Hengchi2026!")),
            Metric(key="net_revenue", name="净收入", expression="revenue-refunds",
                   unit="元", description="", owner="陆知衡"),
        ])
        for i in range(1, 7):
            db.add(SourceRow(
                source_key=f"HC-{i:06}", day="2026-10-01", channel="书店合作",
                region="华东", revenue=100.0 + i, cost=10.0, refunds=2.0, orders=3,
                reference_net=98.0 + i,
            ))
        db.commit()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def token(client, email="analyst@hengchi.demo"):
    response = client.post("/auth/login", json={"email": email, "password": "Hengchi2026!"})
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture
def auth(client):
    return {"Authorization": "Bearer " + token(client)}
