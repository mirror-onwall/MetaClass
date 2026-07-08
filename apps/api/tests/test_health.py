from unittest.mock import Mock

from fastapi.testclient import TestClient

from metaclass.main import create_app


def test_health(tmp_path) -> None:
    app = create_app(tmp_path)
    dispose = Mock(wraps=app.state.services.database.dispose)
    app.state.services.database.dispose = dispose

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    dispose.assert_called_once_with()
