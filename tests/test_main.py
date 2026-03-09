from src import main


def test_serve_uses_production_mode(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        captured.update(app=app, host=host, port=port, reload=reload)

    monkeypatch.setattr("uvicorn.run", fake_run)

    main._serve(reload=False)

    assert captured["app"] == "src.web.app:app"
    assert captured["reload"] is False


def test_serve_dev_enables_autoreload(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        captured.update(app=app, host=host, port=port, reload=reload)

    monkeypatch.setattr("uvicorn.run", fake_run)

    main._serve(reload=True)

    assert captured["app"] == "src.web.app:app"
    assert captured["reload"] is True
