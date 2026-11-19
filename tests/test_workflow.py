from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base


class FakeTracker:
    def __init__(self):
        self.campaign = {}
        self.flows = {}
        self.updates = []
        self.writes = []

    def get(self, path):
        if path.startswith("offers/"):
            return {"id": int(path.split("/")[-1]), "state": "active"}
        if path == "campaigns/1":
            return self.campaign
        if path == "campaigns/1/streams":
            return list(self.flows.values())
        return self.flows[int(path.split("/")[-1])]

    def post(self, path, json=None):
        self.writes.append(path)
        if path == "campaigns":
            self.campaign = {**json, "id": 1}
            return self.campaign
        if path == "streams":
            flow_id = len(self.flows) + 1
            self.flows[flow_id] = {**json, "id": flow_id}
            return self.flows[flow_id]
        if path == "campaigns/1/enable":
            self.campaign["state"] = "active"
        if path == "report/build":
            assert json["dimensions"] == ["stream_id", "offer_id"]
            assert json["filters"][0]["expression"] == 1
            return {"rows": [{"stream_id": 2, "offer_id": 11, "clicks": 14}]}
        return None

    def request(self, method, path, json):
        assert method == "PUT"
        self.updates.append(json)
        self.get(path).update(json)


@pytest.fixture
def workflow(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    stub = FakeTracker()

    @contextmanager
    def connected():
        yield stub

    monkeypatch.setattr(main, "Session", sessionmaker(engine))
    monkeypatch.setattr(main, "tracker", connected)
    monkeypatch.setenv("KEITARO_URL", "https://tracker.example.org")
    with TestClient(main.app) as client:
        yield client, stub
    engine.dispose()


def create(client):
    return client.post("/api/campaigns", json={
        "name": "Test campaign", "country": "au", "offer_id": 11,
        "domain_id": 3, "group_id": 4, "traffic_source_id": 5,
    })


def test_create_campaign_and_read_stats(workflow):
    client, tracker = workflow
    assert client.get("/").status_code == 200
    assert create(client).status_code == 201
    assert tracker.writes == ["campaigns", "streams", "streams", "campaigns/1/enable"]
    assert tracker.campaign["state"] == "active"
    assert tracker.campaign["domain_id"] == 3
    assert tracker.campaign["group_id"] == "4"
    assert tracker.campaign["traffic_source_id"] == 5
    geo, fallback = tracker.flows.values()
    assert geo["position"] == 1
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert geo["action_payload"] == "https://www.google.com/"
    assert fallback["type"] == "default"
    assert fallback["offers"] == [{"offer_id": 11, "share": 100, "state": "active"}]
    assert client.get("/api/campaigns/1/stats").json()["rows"][0]["clicks"] == 14


def test_draft_publish_cancel_restore_and_conflict(workflow):
    client, tracker = workflow
    assert create(client).status_code == 201
    endpoint = "/api/campaigns/1/flows/2/edit"

    def edit(action, revision, **data):
        response = client.post(endpoint, json={"action": action, "revision": revision, **data})
        assert response.status_code == 200, response.text
        return response.json()

    draft = edit("add", 0, offer_id=12)
    assert draft["dirty"]
    assert [row["share"] for row in draft["offers"]] == [50, 50]
    draft = edit("pin", draft["revision"], offer_id=11, share=34)
    draft = edit("add", draft["revision"], offer_id=13)
    assert [row["share"] for row in draft["offers"]] == [34, 33, 33]
    assert client.get("/api/campaigns/1").json()["flows"][1] == draft
    assert tracker.updates == []
    assert client.post(endpoint, json={"action": "cancel", "revision": 1}).status_code == 409
    published = edit("publish", draft["revision"])
    assert not published["dirty"]
    assert [row["share"] for row in tracker.updates[-1]["offers"]] == [34, 33, 33]
    draft = edit("remove", published["revision"], offer_id=12)
    assert [row["share"] for row in draft["offers"]] == [34, 0, 66]
    cancelled = edit("cancel", draft["revision"])
    assert [row["share"] for row in cancelled["offers"]] == [34, 33, 33]
    draft = edit("remove", cancelled["revision"], offer_id=12)
    published = edit("publish", draft["revision"])
    assert [row["offer_id"] for row in tracker.updates[-1]["offers"]] == [11, 13]
    assert next(row for row in published["offers"] if row["offer_id"] == 12)["state"] == "removed"
    draft = edit("restore", published["revision"], offer_id=12)
    assert sum(row["share"] for row in draft["offers"]) == 100
    tracker.flows[2]["offers"][0]["share"] = 35
    assert client.post(endpoint, json={"action": "publish", "revision": draft["revision"]}).status_code == 409
