"""Source switching on the production LVGL canvas, without network or USB."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "desktop"), str(PROJECT / "host")]

from PySide6.QtCore import QDateTime, QSettings
from PySide6.QtWidgets import QApplication

from app import MembershipExpiryDialog, Window
from desktop_ipc import DesktopClient, DesktopHub
from protocol import LABEL_BYTES, packet


def test_account_menu_ring_and_provider_round_trip(tmp_path):
    application = QApplication.instance() or QApplication([])
    hub = DesktopHub(tmp_path / "ipc")
    client = DesktopClient(tmp_path / "ipc")
    window = Window(client=client, settings=QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat))
    window.timer.stop()
    window.show()
    try:
        client.subscribe()
        hub.poll()
        window.api_actions["official"].trigger()
        hub.poll()
        assert hub.api_mode_request == "official"
        window.rotation_actions["all"].trigger()
        hub.poll()
        assert hub.rotation_mode_request == "all"
        hub.publish_rotation_mode("off")
        window.pump()
        assert window.rotation_actions["off"].isChecked()
        assert [action.text() for action in window.rotation_actions.values()] == [
            "关闭滚动", "只滚动执行任务", "滚动全部"]
        themes = (("beach", 2), ("pixel", 3), ("mechanical", 4), ("paper", 5), ("glass", 6), ("vangogh", 7))
        for theme, theme_id in themes:
            window.select_theme(theme_id)
            data = packet(1, state="thinking", total=1, labels=bytes(LABEL_BYTES),
                          theme=theme, api_source="official", api_mode="auto", account_kind=1,
                          week_remaining_x10=755, plan_name="Pro Lite", week_reset="09-29 11:29",
                          input_tps_x10=1020, output_tps_x10=540, usage_stale=False)
            assert window.renderer.feed(data) == 1
            window.renderer.tick(16)
            before = window.renderer.value(13)
            for _ in range(45):
                window.renderer.tick(16)
            assert window.renderer.value(11) == 1
            assert window.renderer.value(12) == window.renderer.value(13) == 755
            assert window.renderer.account_text(2) == "Pro Lite"
            assert "未设置" in window.renderer.account_text(3)
            assert "09-29 11:29" in window.renderer.account_text(4)
            assert before <= 755
            window.canvas.update()
            application.processEvents()
            destination = PROJECT / "output/account-ui"
            destination.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(destination / f"official-{theme}.png"))

        window.motion_menu.popup(window.motion_button.mapToGlobal(window.motion_button.rect().bottomLeft()))
        application.processEvents()
        window.motion_menu.grab().save(str(destination / "api-menu.png"))
        window.motion_menu.hide()
        window.select_theme(6)
        window.renderer.feed(packet(2, state="done", labels=bytes(LABEL_BYTES), theme="glass",
                                    api_source="official", account_kind=2, api_mode="official"))
        window.renderer.tick(32)
        assert window.renderer.account_text(1) == "--"
        assert "无会员额度" in window.renderer.account_text(3)
        window.renderer.feed(packet(3, state="done", labels=bytes(LABEL_BYTES), theme="glass",
                                    api_source="morecode", api_mode="morecode", balance_quota=5000000))
        window.renderer.tick(32)
        assert "MoreCode" in window.renderer.account_text(0)
        assert "各模型消费" == window.renderer.account_text(5)
        assert window.renderer.value(10) == 2
        window.renderer.feed(packet(4, state="working", labels=bytes(LABEL_BYTES), theme="glass",
                                    api_source="official", account_kind=1,
                                    membership_expires_at="2026-10-22T23:59"))
        window.renderer.tick(32)
        assert window.renderer.account_text(3) == "到期: 2026-10-22"
        assert window.renderer.account_text(6) == "2026-10-22T23:59"
        dialog = MembershipExpiryDialog(window.renderer.account_text(6), window)
        assert dialog.value() == "2026-10-22T23:59"
        dialog.date_time.setDateTime(QDateTime.fromString("2026-11-01T10:30", "yyyy-MM-ddTHH:mm"))
        assert dialog.value() == "2026-11-01T10:30"
        dialog.enabled.setChecked(False)
        assert dialog.value() == ""
        dialog.grab().save(str(destination / "expiry-settings.png"))
        dialog.close()
    finally:
        window.close()
        hub.close()
