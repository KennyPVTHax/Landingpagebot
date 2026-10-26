from fastapi.testclient import TestClient
from release_manager.settings import Settings
from release_manager.web import create_app

def test_access_and_slots(tmp_path):
    config = Settings(database_url=f"sqlite:///{tmp_path}/db.sqlite",
                      internal_api_token="X"*40, key_pepper="Y"*40,
                      admin_telegram_ids="100")
    with TestClient(create_app(config)) as client:
        headers = {"X-Internal-Token": "X"*40}
        def post(path, payload):
            return client.post("/internal/"+path, json=payload, headers=headers)
        assert client.get("/health").json()["ok"]
        assert post("status", {"telegram_id": 200}).json()["active"] is False
        assert post("slots", {"telegram_id": 200, "title": "Demo"}).status_code == 403
        assert post("issue", {"telegram_id": 200, "days": 30, "slots": 1}).status_code == 403
        key = post("issue", {"telegram_id": 100, "days": 30, "slots": 1}).json()["key"]
        assert post("redeem", {"telegram_id": 200, "key": key}).status_code == 200
        assert post("redeem", {"telegram_id": 201, "key": key}).status_code == 400
        slot = post("slots", {"telegram_id": 200, "title": "<script>"}).json()
        assert client.get(slot["path"]).status_code == 200
        assert "<script>" not in client.get(slot["path"]).text
        assert post("slots", {"telegram_id": 200, "title": "Second"}).status_code == 403
        assert post("slots/disable", {"telegram_id": 201, "slot_id": slot["id"]}).status_code == 403
        assert post("slots/disable", {"telegram_id": 200, "slot_id": slot["id"]}).status_code == 200
        assert client.get(slot["path"]).status_code == 404
        assert client.post("/internal/status", json={"telegram_id": 200}).status_code == 401

def test_admin_suspension(tmp_path):
    config = Settings(database_url=f"sqlite:///{tmp_path}/db.sqlite",
                      internal_api_token="X"*40, key_pepper="Y"*40,
                      admin_telegram_ids="100")
    with TestClient(create_app(config)) as client:
        h = {"X-Internal-Token": "X"*40}
        def post(path, payload):
            return client.post("/internal/"+path, json=payload, headers=h)
        assert post("grant", {"telegram_id": 100, "target_id": 200,
                              "days": 7, "slots": 2}).status_code == 200
        assert post("suspend", {"telegram_id": 100, "target_id": 200,
                                "value": True}).status_code == 200
        assert post("status", {"telegram_id": 200}).json()["active"] is False
        assert post("slots", {"telegram_id": 200, "title": "Example"}).status_code == 403
