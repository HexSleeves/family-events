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


def test_pipeline_cli_runs_scrape_tag_then_notify(monkeypatch):
    calls: list[str] = []

    async def fake_scrape_then_tag():
        calls.append("scrape_then_tag")
        return {"summary": "done"}

    async def fake_notify():
        calls.append("notify")
        return "ok"

    monkeypatch.setattr("src.scheduler.run_scrape_then_tag", fake_scrape_then_tag)
    monkeypatch.setattr("src.scheduler.run_notify", fake_notify)
    monkeypatch.setattr("sys.argv", ["src.main", "pipeline"])

    main.cli()

    assert calls == ["scrape_then_tag", "notify"]
