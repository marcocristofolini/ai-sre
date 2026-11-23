"""O servidor de demonstração (scripts/demo_server.py) sobe o app real com dados sintéticos e exige login."""

import importlib.util
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("demo_server", ROOT / "scripts" / "demo_server.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_demo_app_requires_login_and_serves_labelled_synthetic_data():
    app = demo.build_demo_app(hash_cost=2**4)
    with TestClient(app) as client:
        assert client.get("/incidents").status_code == 401
        bad = client.post("/auth/login", json={"username": "admin", "password": "errada"})
        assert bad.status_code == 401
        ok = client.post("/auth/login", json={"username": "admin", "password": demo.DEMO_PASSWORD})
        assert ok.status_code == 200
        incidents = client.get("/incidents").json()
        assert len(incidents) == 6
        assert all(demo.DEMO_TAG in i["alert_name"] and i["labels"]["ambiente"] == demo.DEMO_TAG for i in incidents)
        assert {i["status"] for i in incidents} == {"done", "failed", "investigating"}
        assert sum(1 for i in incidents if i["rca"] and i["rca"]["unknown"]) == 1
        approved = [i for i in incidents if client.get(f"/incidents/{i['id']}").json()["approvals"]]
        assert len(approved) == 1
        # Sem placar: nunca fabrica números de precisão.
        assert client.get("/scoreboard").json() == {"measured": False, "reason": "missing"}
        deadline = time.time() + 5
        while time.time() < deadline:
            states = {r["workload"]: r["state"] for r in client.get("/risk").json()}
            if states.get("demo/pagamentos-api") == "ativo":
                break
            time.sleep(0.05)
        assert states == {"demo/pagamentos-api": "ativo", "demo/relatorios-batch": "aprendendo"}
