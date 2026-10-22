"""Regression cases for budget direction, freshness, and actual Qt deadline delivery."""
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from codex_pet.bridge.plan_usage import (
    FRESH_USAGE_S, HOLD_LAST_GOOD_S, PlanUsage, UsageState,
    parse_usage_payload, usage_grade, usage_pill_parts, usage_short_text, usage_text,
)
from codex_pet.task_panel import TaskPanel

app = QApplication.instance() or QApplication([])
NOW = 1790600000.0
WEEK = 604800


def snap(used=50, elapsed=.5, **kwargs):
    return PlanUsage(UsageState.OK, used_percent=used, fetched_at=NOW,
                     reset_at=NOW + (1 - elapsed) * WEEK, **kwargs)


@pytest.mark.parametrize("elapsed", [.199, .2, .201])
def test_no_artificial_twenty_percent_gate(elapsed):
    assert usage_grade(snap(21, elapsed), NOW) == "neutral"


def test_grade_has_correct_direction_over_budget_grid():
    rank = {"red": -1, "neutral": 0, "green": 1}
    # With fresh observations: more usage cannot improve grade;
    # less time remaining with unchanged usage cannot worsen it.
    grid = [[rank[usage_grade(snap(used, elapsed / 100), NOW)]
             for used in range(101)] for elapsed in range(100)]
    assert all(row == sorted(row, reverse=True) for row in grid)
    assert all(list(column) == sorted(column) for column in zip(*grid))


@pytest.mark.parametrize("used, expected", [(35.01, "neutral"), (34.99, "green"),
                                            (64.99, "neutral"), (65.01, "red")])
def test_display_rounding_does_not_drive_color(used, expected):
    u = parse_usage_payload({"rate_limit": {"primary_window": {
        "used_percent": used, "reset_at": NOW + WEEK / 2,
        "limit_window_seconds": WEEK}}}, NOW)
    assert u.used_percent == used
    assert isinstance(u.remaining_percent, int)
    assert usage_grade(u, NOW) == expected


@pytest.mark.parametrize("used", [None, 30, 100])
def test_explicit_limit_is_visible_even_without_percentage(used):
    u = snap(used, limit_reached=True)
    assert usage_pill_parts(u, NOW) == ("额度受限", "#b65f62")
    assert "额度受限" in usage_short_text(u, NOW)
    assert "额度受限" in usage_text(u, NOW)


@pytest.mark.parametrize("used, limited", [(0, False), (100, True), (None, True)])
def test_reset_boundary_overrides_old_balance_and_limit(used, limited):
    u = replace(snap(used, limit_reached=limited), reset_at=NOW + 30)
    for at in (NOW + 30, NOW + 31):
        assert usage_grade(u, at) == "neutral"
        assert usage_pill_parts(u, at) == ("待刷新", "")
        assert "等待刷新" in usage_short_text(u, at)
        assert "受限" not in usage_text(u, at)


@pytest.mark.parametrize("limited", [False, True])
def test_stale_and_expired_retention_boundaries(limited):
    u = snap(15, limit_reached=limited)
    assert usage_grade(u, NOW + FRESH_USAGE_S - .01) in ("red", "green")
    for at in (NOW + FRESH_USAGE_S, NOW + HOLD_LAST_GOOD_S - .01):
        assert usage_grade(u, at) == "neutral"
        text, color = usage_pill_parts(u, at)
        assert "待" in text and color == ""
        assert "最后成功更新" in usage_text(u, at)
    assert usage_pill_parts(u, NOW + HOLD_LAST_GOOD_S) == ("", "")
    assert "暂不可用" in usage_short_text(u, NOW + HOLD_LAST_GOOD_S)


@pytest.mark.parametrize("remaining, label", [(1, "1分钟"), (3599, "60分钟"),
    (3600, "1小时"), (86399, "24小时"), (86400, "1.0天")])
def test_near_reset_uses_hours_or_minutes(remaining, label):
    u = replace(snap(10), reset_at=NOW + remaining)
    assert usage_pill_parts(u, NOW)[0] == f"90% · {label}"


def test_panel_cache_deadlines_auth_invalidation_and_recovery():
    parent = QWidget()
    clock = [NOW]
    panel = TaskPanel(parent, clock=lambda: clock[0])
    try:
        good = snap(15)
        panel.set_plan_usage(good)
        clock[0] += 300
        panel.set_plan_usage(PlanUsage(UsageState.UNREACHABLE))
        assert panel.collapse.usage_color == "#39836e"
        assert "上次成功值" in panel.toolTip()
        clock[0] = NOW + FRESH_USAGE_S
        panel._refresh_usage()
        assert panel.collapse.usage == "85% · 待更新"
        assert panel.collapse.usage_color == ""
        clock[0] = NOW + HOLD_LAST_GOOD_S
        panel._refresh_usage()
        assert panel.collapse.usage == "" and not panel._usage_timer.isActive()
        panel.set_plan_usage(replace(good, fetched_at=clock[0]))
        assert panel.collapse.usage_color == "#39836e"
        panel.set_plan_usage(PlanUsage(UsageState.AUTH_EXPIRED))
        assert panel.collapse.usage == "登录过期"
        panel.set_plan_usage(PlanUsage(UsageState.UNREACHABLE))
        assert panel.collapse.usage == ""  # Cannot resurrect pre-logout quota.
        assert panel._last_good_usage is None
    finally:
        parent.close()
        parent.deleteLater()
        app.processEvents()


def test_repeated_failure_cannot_refresh_old_snapshot_or_cross_reset():
    parent = QWidget()
    panel = TaskPanel(parent)
    try:
        good = replace(snap(100, limit_reached=True), reset_at=NOW + 30)
        panel.set_plan_usage(good, NOW)
        for offset in (10, 29, 30, 90, 7201):
            panel.set_plan_usage(PlanUsage(UsageState.UNREACHABLE), NOW + offset)
            assert panel._usage.fetched_at == NOW
            if offset >= 30:
                assert panel.collapse.usage == "待刷新"
                assert panel.collapse.usage_color == ""
        new = replace(snap(0), fetched_at=NOW + 7201, reset_at=NOW + 7201 + WEEK)
        panel.set_plan_usage(new, NOW + 7201)
        assert panel.collapse.usage == "100% · 7.0天"
        assert "上次成功值" not in panel.toolTip()
    finally:
        parent.close()
        parent.deleteLater()
        app.processEvents()


def test_real_timer_expires_quota_without_any_network_callback():
    parent = QWidget()
    panel = TaskPanel(parent)
    try:
        now = time.time()
        panel.set_plan_usage(PlanUsage(UsageState.OK, used_percent=100,
            limit_reached=True, fetched_at=now, reset_at=now + .08))
        assert panel.collapse.usage == "额度受限"
        QTest.qWait(180)
        assert panel.collapse.usage == "待刷新"
        assert panel.collapse.usage_color == ""
        assert not panel._usage_timer.isActive()
    finally:
        parent.close()
        parent.deleteLater()
        app.processEvents()
