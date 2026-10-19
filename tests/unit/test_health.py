from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_live_and_error_envelope() -> None:
    app = create_app(Settings(app_env="test", llm_provider="fake"))  # type: ignore[arg-type]
    client = TestClient(app)
    assert client.get("/health/live").json() == {"status": "alive"}
    r = client.get("/nope")
    assert r.status_code == 404
    assert set(r.json()) == {"error", "detail", "request_id"}
