def test_collect_uses_linux_on_linux(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "linux")

    captured = {}

    def fake_linux():
        captured["called"] = "linux"
        return []

    from server_monitor_agent import collect

    monkeypatch.setattr(collect, "_collect_linux", fake_linux)
    collect.collect()
    assert captured["called"] == "linux"


def test_collect_uses_windows_on_win32(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "win32")

    captured = {}

    def fake_windows():
        captured["called"] = "windows"
        return []

    from server_monitor_agent import collect

    monkeypatch.setattr(collect, "_collect_windows", fake_windows)
    collect.collect()
    assert captured["called"] == "windows"
