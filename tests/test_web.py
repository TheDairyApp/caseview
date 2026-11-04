import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from caseview.core.config import Config  # noqa: E402
from caseview.core.web import WebServer, _allowed, new_pin  # noqa: E402


class FakeController:
    def __init__(self) -> None:
        self.rgb: dict[str, Any] = {}
        self.profile = ""
        self.slot: tuple[str, str] | None = None

    def web_state(self) -> dict[str, Any]:
        return {"status": "ok", "rgb": self.rgb}

    def web_set_rgb(self, changes: dict[str, Any]) -> None:
        self.rgb.update(changes)

    def web_apply_profile(self, name: str) -> None:
        if name == "missing":
            raise ValueError("no profile")
        self.profile = name

    def web_set_slot(self, slot: str, sensor: str) -> None:
        self.slot = (slot, sensor)


@pytest.fixture
def server() -> WebServer:
    s = WebServer(FakeController(), 0, "123456")
    s.start()
    assert s._httpd is not None
    s.port = s._httpd.server_address[1]
    yield s
    s.stop()


def call(s: WebServer, path: str, pin: str = "123456", body: Any = None) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{s.port}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-CaseView-PIN": pin, "Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            raw = res.read()
            return res.status, raw if path == "/" else json.loads(raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_page_is_served_without_pin(server: WebServer) -> None:
    code, body = call(server, "/", pin="")
    assert code == 200 and b"CaseView" in body


def test_api_needs_correct_pin(server: WebServer) -> None:
    assert call(server, "/api/state", pin="")[0] == 401
    assert call(server, "/api/state", pin="000000")[0] == 401
    code, state = call(server, "/api/state")
    assert code == 200 and state["status"] == "ok"


def test_lockout_after_five_wrong_pins(server: WebServer) -> None:
    for _ in range(5):
        call(server, "/api/state", pin="999999")
    assert call(server, "/api/state", pin="999999")[0] == 429
    assert call(server, "/api/state")[0] == 429  # even the right PIN waits out the lockout


def test_posts_reach_controller_and_bad_input_is_400(server: WebServer) -> None:
    ctrl = server.controller
    assert call(server, "/api/rgb", body={"enabled": True})[0] == 200
    assert ctrl.rgb == {"enabled": True}  # type: ignore[attr-defined]
    assert call(server, "/api/slot", body={"slot": "cpu_fan", "sensor": "Fan #1"})[0] == 200
    assert call(server, "/api/profile", body={"name": "missing"})[0] == 400
    assert call(server, "/api/profile", body={})[0] == 400
    assert call(server, "/api/nope", body={})[0] == 404


def test_only_private_networks_allowed() -> None:
    assert _allowed("192.168.1.20") and _allowed("10.0.0.5") and _allowed("127.0.0.1")
    assert not _allowed("8.8.8.8") and not _allowed("not-an-ip")


def test_pin_format_and_ui_config_round_trip() -> None:
    pin = new_pin()
    assert len(pin) == 6 and pin.isdigit()
    cfg = Config.from_toml(
        f'[ui]\nstats_bar = false\nstats_bar_pos = [10, 20]\nweb_enabled = true\nweb_pin = "{pin}"'
    )
    assert (cfg.ui.stats_bar, cfg.ui.stats_bar_pos, cfg.ui.web_pin) == (False, (10, 20), pin)
    assert Config.from_toml(cfg.to_toml()).ui == cfg.ui


def test_dashboard_and_bar_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from PySide6.QtWidgets import QApplication

    from caseview.core.metrics import History
    from caseview.ui.dashboard import Dashboard
    from caseview.ui.taskbar_bar import TaskbarBar
    from caseview.ui.theme import Theme

    QApplication.instance() or QApplication([])
    values = {
        "cpu_load": 21.0,
        "cpu_temp": 36.0,
        "gpu_load": 3.0,
        "ram_pct": 57.0,
        "ram_used_gb": 18.2,
        "ram_total_gb": 32.0,
        "cpu_fan": 1750.0,
    }
    hist = History()
    for i in range(10):
        hist.add({"cpu_load": float(i * 5)})
    dash = Dashboard(lambda: values, lambda: hist, lambda: "Display: live", Theme())
    dash.refresh()
    assert dash.cards[0].ring._value == 21.0 and dash.cards[2].detail.text() == "18.2 / 32 GB"
    bar = TaskbarBar(lambda: values, Theme())
    assert [t[0] for t in bar._text] == ["CPU", "GPU", "RAM", "FAN"]
    assert bar._text[0][1] == "21% 36°"
    dash.close()
    bar.close()
