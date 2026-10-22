"""Codex 套餐用量（余额）只读查询。

数据源：官方 ChatGPT 后端的 codex/usage 端点（与 Codex CLI /usage 同源）。
认证：读取本机 ~/.codex/auth.json 的登录令牌，仅在内存中用于请求头，
不复制、不落盘、不写日志；不刷新令牌、不改写 auth.json。
令牌过期时如实显示"凭据过期"；检测官方登录文件更新后立即重新查询。
非官方稳定接口：结构变化时降级为"暂不可用"，不影响其他功能。
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

log = logging.getLogger("codex_pet.bridge.plan_usage")

USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"
POLL_MS = 10 * 60 * 1000  # 空闲常规间隔：周额度窗口，10 分钟刷新足够
ACTIVE_POLL_MS = 60 * 1000  # Codex 有任务运行时的高频间隔（与官方客户端量级一致）
FIRST_POLL_DELAY_MS = 2000
AUTH_CHECK_MS = 5000  # 仅检查文件元数据，不读取或缓存认证材料
REQUEST_TIMEOUT_S = 15.0
MAX_BODY_BYTES = 256 * 1024

# 与 Codex CLI 相同的请求头；缺少 originator/User-Agent 会被网关拦为 403 HTML。
BASE_HEADERS = {
    "User-Agent": "codex_cli_rs/0.51.0 (Windows 11; x86_64) unknown",
    "originator": "codex_cli_rs",
    "OpenAI-Beta": "responses=experimental",
    "Accept": "application/json",
}

PLAN_LABELS = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "prolite": "Pro Lite",
    "team": "Team",
    "business": "Business",
    "edu": "Edu",
    "enterprise": "Enterprise",
}

DEFAULT_WINDOW_S = 7 * 24 * 3600.0  # 周窗口；接口异常时按 7 天
SLACK_PP = 15.0   # 偏离阈值（百分点）：100/7≈14.3，约一天的用量
FRESH_USAGE_S = 2 * POLL_MS / 1000  # 两个正常刷新周期后只保留旧数值，不继续标色
HOLD_LAST_GOOD_S = 2 * 60 * 60  # 旧数值最多保留两小时，不能跨重置继续解释
GRADE_COLORS = {"green": "#39836e", "red": "#b65f62", "neutral": ""}


class UsageState(Enum):
    OK = "ok"
    NO_AUTH = "no-auth"          # 本机没有 Codex 登录信息
    AUTH_EXPIRED = "auth-expired"  # 令牌被服务端拒绝
    UNREACHABLE = "unreachable"  # 网络不通或被网关拦截
    BAD_RESPONSE = "bad-response"  # 接口结构变化或响应异常


@dataclass(frozen=True)
class PlanUsage:
    state: UsageState
    plan_label: str = ""
    used_percent: Optional[float] = None
    limit_reached: bool = False
    reset_at: Optional[float] = None  # epoch 秒
    fetched_at: Optional[float] = None
    window_seconds: float = DEFAULT_WINDOW_S  # 末位新增，兼容位置参数构造

    @property
    def remaining_percent(self) -> Optional[int]:
        if self.used_percent is None:
            return None
        return round(max(0, min(100, 100 - self.used_percent)))


def plan_label_of(plan_type) -> str:
    if not isinstance(plan_type, str) or not plan_type:
        return ""
    return PLAN_LABELS.get(plan_type, plan_type)


def parse_usage_payload(payload, fetched_at: float) -> PlanUsage:
    """从 usage 端点 JSON 构造快照；结构不认识时返回 BAD_RESPONSE。"""
    if not isinstance(payload, dict):
        return PlanUsage(UsageState.BAD_RESPONSE, fetched_at=fetched_at)
    rate = payload.get("rate_limit")
    if not isinstance(rate, dict):
        return PlanUsage(UsageState.BAD_RESPONSE, fetched_at=fetched_at)
    primary = rate.get("primary_window")
    used = reset_at = None
    window_s = DEFAULT_WINDOW_S
    if isinstance(primary, dict):
        used_raw = primary.get("used_percent")
        if isinstance(used_raw, (int, float)) and 0 <= used_raw <= 100:
            used = float(used_raw)  # 计算保留精度，只有展示百分比时取整
        reset_raw = primary.get("reset_at")
        if isinstance(reset_raw, (int, float)):
            reset_at = float(reset_raw)
        elif isinstance(primary.get("reset_after_seconds"), (int, float)):
            reset_at = fetched_at + float(primary["reset_after_seconds"])
        if isinstance(primary.get("limit_window_seconds"), (int, float)) and primary["limit_window_seconds"] > 0:
            window_s = float(primary["limit_window_seconds"])
    limit_reached = bool(rate.get("limit_reached")) or rate.get("allowed") is False
    return PlanUsage(
        state=UsageState.OK,
        plan_label=plan_label_of(payload.get("plan_type")),
        used_percent=used,
        limit_reached=limit_reached,
        reset_at=reset_at,
        window_seconds=window_s,
        fetched_at=fetched_at,
    )


def format_reset(reset_at: Optional[float], now: Optional[float] = None) -> str:
    if not reset_at:
        return ""
    try:
        return datetime.fromtimestamp(reset_at).strftime("%m月%d日 %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def usage_freshness(usage: PlanUsage, now: float) -> str:
    """快照时效；旧周期一律待刷新，绝不推断新周期额度。"""
    if usage.reset_at is not None and now >= usage.reset_at:
        return "expired"
    if usage.fetched_at is not None:
        age = now - usage.fetched_at
        if age < 0 or age >= HOLD_LAST_GOOD_S:
            return "unavailable"
        if age >= FRESH_USAGE_S:
            return "stale"
    return "fresh"


def usage_text(usage: Optional[PlanUsage], now: Optional[float] = None) -> str:
    """单行摘要；不伪造：非 OK 状态只说明原因。"""
    if usage is None:
        return ""
    if usage.state is UsageState.OK:
        at = time.time() if now is None else now
        freshness = usage_freshness(usage, at)
        if freshness == "expired":
            return "Codex 套餐：已到重置时刻，等待刷新"
        if freshness == "unavailable":
            return "Codex 套餐：旧数据已失效，暂时取不到用量"
        reset = format_reset(usage.reset_at)
        reset_part = f"（{reset} 重置）" if reset else ""
        if usage.limit_reached:
            body = "额度受限"
        elif usage.remaining_percent is not None:
            body = f"剩余 {usage.remaining_percent}%"
        else:
            body = "已连接"
        plan = f"{usage.plan_label} · " if usage.plan_label else ""
        updated = format_reset(usage.fetched_at)
        update_part = f"；最后成功更新 {updated}" if updated else ""
        if freshness == "stale":
            body = f"上次{body}（待更新）"
        return f"Codex 套餐：{plan}{body}{reset_part}{update_part}"
    if usage.state is UsageState.NO_AUTH:
        return "Codex 套餐：未检测到本机登录"
    if usage.state is UsageState.AUTH_EXPIRED:
        return "Codex 套餐：本机登录凭据失效；请在 Codex 检查登录，更新后自动重试"
    return "Codex 套餐：暂时取不到用量"


def usage_grade(usage: Optional[PlanUsage], now: float) -> str:
    """均匀预算余量 Δ=100e-used；±15 个百分点以外标绿/红。

    这是预算进度比较，不是近期速度预测；旧快照不能继续标色。
    """
    if usage is None or usage.state is not UsageState.OK:
        return "neutral"
    if usage_freshness(usage, now) != "fresh":
        return "neutral"
    if usage.limit_reached:
        return "red"
    if usage.used_percent is None or usage.reset_at is None:
        return "neutral"
    remain_s = usage.reset_at - now
    if remain_s <= 0:
        return "neutral"  # 重置时刻未刷新，等下一次取数
    window = usage.window_seconds if usage.window_seconds > 0 else DEFAULT_WINDOW_S
    if remain_s > window:
        return "neutral"
    elapsed = 1.0 - remain_s / window
    slack = round(elapsed * 100.0 - usage.used_percent, 9)
    if slack <= -SLACK_PP:
        return "red"
    if slack >= SLACK_PP:
        return "green"
    return "neutral"


def days_until_reset(usage: Optional[PlanUsage], now: float) -> Optional[float]:
    """距重置的天数（保留一位小数）；数据缺失或已过期返回 None。"""
    if usage is None or usage.state is not UsageState.OK or usage.reset_at is None:
        return None
    remain_s = usage.reset_at - now
    if remain_s <= 0:
        return None
    return round(remain_s / 86400.0, 1)


def time_until_reset(usage: PlanUsage, now: float) -> str:
    """临近重置用小时/分钟，避免 0.0 天的模糊倒计时。"""
    if usage.reset_at is None or usage.reset_at <= now:
        return ""
    seconds = usage.reset_at - now
    if seconds >= 86400:
        return f"{seconds / 86400:.1f}天"
    if seconds >= 3600:
        return f"{math.ceil(seconds / 3600)}小时"
    return f"{math.ceil(seconds / 60)}分钟"


def usage_pill_parts(usage: Optional[PlanUsage], now: Optional[float] = None) -> tuple[str, str]:
    """胶囊常驻余额段：(文本, 颜色 hex)。颜色为空串表示默认文本色。

    只放有把握的短信息：未登录/凭据过期常驻提醒；网络抖动与接口异常
    不进胶囊（tooltip 已有完整说明），避免常驻文本闪烁。
    """
    if usage is None:
        return "", ""
    if usage.state is UsageState.OK:
        at = time.time() if now is None else now
        freshness = usage_freshness(usage, at)
        if freshness == "expired":
            return "待刷新", ""
        if freshness == "unavailable":
            return "", ""
        if usage.limit_reached:
            return ("额度受限", GRADE_COLORS["red"]) if freshness == "fresh" else ("受限待确认", "")
        if usage.remaining_percent is None:
            return "", ""
        text = f"{usage.remaining_percent}%"
        if freshness == "stale":
            return f"{text} · 待更新", ""
        remaining = time_until_reset(usage, at)
        if remaining:
            text += f" · {remaining}"
        return text, GRADE_COLORS[usage_grade(usage, at)]
    if usage.state is UsageState.NO_AUTH:
        return "未登录", ""
    if usage.state is UsageState.AUTH_EXPIRED:
        return "登录过期", ""
    return "", ""


def usage_short_text(usage: Optional[PlanUsage], now: Optional[float] = None) -> str:
    """详情面板页脚单行文案：余额 + 天数 + 重置时刻（约 190px，不超详情宽度）。

    不含套餐名（plan 标签用户不需要，tooltip 完整版保留）。
    """
    if usage is None:
        return ""
    if usage.state is UsageState.OK:
        at = time.time() if now is None else now
        freshness = usage_freshness(usage, at)
        if freshness == "expired":
            return "已到重置时刻，等待刷新"
        if freshness == "unavailable":
            return "套餐用量暂不可用"
        if usage.limit_reached:
            body = "额度受限"
        elif usage.remaining_percent is not None:
            body = f"剩余 {usage.remaining_percent}%"
        else:
            body = "已连接"
        if freshness == "stale":
            return f"上次{body} · 待更新"
        tail = ""
        reset = format_reset(usage.reset_at)
        remaining = time_until_reset(usage, at)
        if remaining and reset:
            tail = f" · {remaining}后重置（{reset}）"
        elif reset:
            tail = f" · {reset} 重置"
        return f"{body}{tail}"
    if usage.state is UsageState.NO_AUTH:
        return "未登录 Codex"
    if usage.state is UsageState.AUTH_EXPIRED:
        return "登录失效，请在 Codex 检查登录"
    return "套餐用量暂不可用"



def _load_auth(codex_home: Path):
    """只取请求所需的最少字段；文件缺失/结构异常都视为未登录。"""
    auth_file = codex_home / "auth.json"
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        tokens = data["tokens"]
        return tokens["access_token"], tokens["account_id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


class _HttpError(Exception):
    def __init__(self, status: int, body: bytes):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


class _RejectUsageRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The fixed endpoint must not forward sign-in headers to another URL.
        return None


def _open(url: str, headers: dict, timeout: float):
    """默认传输实现；测试可注入替身。"""
    req = urllib.request.Request(url, headers=headers)
    transport = urllib.request.build_opener(_RejectUsageRedirects())
    try:
        with transport.open(req, timeout=timeout) as response:
            return response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _HttpError(exc.code, exc.read(MAX_BODY_BYTES + 1)) from exc


def fetch_usage_snapshot(
    codex_home: Path,
    url: str = USAGE_URL,
    timeout: float = REQUEST_TIMEOUT_S,
    opener: Callable[[str, dict, float], bytes] = _open,
    clock: Callable[[], float] = time.time,
) -> PlanUsage:
    """同步取一次用量（阻塞网络 IO，供后台线程调用）。"""
    token, account_id = _load_auth(codex_home)
    if token is None:
        return PlanUsage(UsageState.NO_AUTH)
    headers = dict(BASE_HEADERS)
    headers["Authorization"] = "Bearer " + token
    headers["chatgpt-account-id"] = account_id
    try:
        body = opener(url, headers, timeout)
    except _HttpError as exc:
        if exc.status in (401,):
            return PlanUsage(UsageState.AUTH_EXPIRED, fetched_at=clock())
        # 403 多为网关拦截页（非 JSON），与网络不可达同样降级。
        return PlanUsage(UsageState.UNREACHABLE, fetched_at=clock())
    except (OSError, ValueError):
        return PlanUsage(UsageState.UNREACHABLE, fetched_at=clock())
    if len(body) > MAX_BODY_BYTES:
        return PlanUsage(UsageState.BAD_RESPONSE, fetched_at=clock())
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return PlanUsage(UsageState.BAD_RESPONSE, fetched_at=clock())
    return parse_usage_payload(payload, clock())


class _FetchJob(QRunnable):
    def __init__(self, fetch: Callable[[], PlanUsage], emit: Callable[[PlanUsage], None]):
        super().__init__()
        self._fetch = fetch
        self._emit = emit
        self.setAutoDelete(True)

    def run(self):
        try:
            usage = self._fetch()
        except Exception:  # 后台线程兜底：任何意外都不带崩主程序
            log.exception("套餐用量查询异常")
            usage = PlanUsage(UsageState.BAD_RESPONSE, fetched_at=time.time())
        try:
            self._emit(usage)
        except RuntimeError:
            pass  # 应用退出中，信号接收方可能已销毁


RETRY_MS = 60 * 1000          # 取数失败后的快速重试间隔
NOISE_STATES = (UsageState.UNREACHABLE, UsageState.BAD_RESPONSE)
RETRY_STATES = (*NOISE_STATES, UsageState.AUTH_EXPIRED, UsageState.NO_AUTH)


class PlanUsageProvider(QObject):
    """定时在后台线程查询套餐用量，经 updated 信号投递到主线程。

    取数或登录失败时把轮询间隔临时缩到 60 秒，登录文件更新立即重查，
    成功后恢复常规间隔；失败状态照常上报，由显示端决定是否保留旧值。

    频率随 Codex 工作状态自适应（set_work_active）：有任务运行时用
    active_poll_ms 高频刷新并在开始时立即取一次；空闲回到 poll_ms。
    失败重试间隔优先于工作状态，两种模式互不干扰。
    """

    updated = Signal(object)
    _fetched = Signal(object)

    def __init__(
        self,
        codex_home: Optional[Path] = None,
        poll_ms: int = POLL_MS,
        fetch: Optional[Callable[[], PlanUsage]] = None,
        active_poll_ms: int = ACTIVE_POLL_MS,
    ):
        super().__init__()
        env_home = os.environ.get("CODEX_HOME")
        home = Path(codex_home or env_home or Path.home() / ".codex")
        self._auth_file = home / "auth.json"
        self._auth_signature = self._auth_file_signature()
        self._fetch = fetch or (lambda: fetch_usage_snapshot(home))
        self._poll_ms = poll_ms
        self._active_poll_ms = active_poll_ms
        self._work_active = False
        self._retrying = False
        self._last_poll_at: Optional[float] = None
        self._timer: Optional[QTimer] = None
        self._auth_timer: Optional[QTimer] = None
        self._in_flight = False
        self._refresh_pending = False
        self._last_state: Optional[UsageState] = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._fetched.connect(self._on_fetched)

    def start(self) -> None:
        self.stop()
        self._timer = QTimer(self)
        self._timer.setInterval(FIRST_POLL_DELAY_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._auth_signature = self._auth_file_signature()
        self._auth_timer = QTimer(self)
        self._auth_timer.setInterval(AUTH_CHECK_MS)
        self._auth_timer.timeout.connect(self._check_auth_changed)
        self._auth_timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        if self._auth_timer is not None:
            self._auth_timer.stop()
            self._auth_timer.deleteLater()
            self._auth_timer = None
        self._refresh_pending = False

    def _auth_file_signature(self):
        try:
            info = self._auth_file.stat()
            return info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino
        except OSError:
            return None

    def _check_auth_changed(self) -> None:
        signature = self._auth_file_signature()
        if signature != self._auth_signature:
            self._auth_signature = signature
            if self._in_flight:
                self._refresh_pending = True
            else:
                self._poll()

    def _wanted_interval(self) -> int:
        if self._retrying:
            return RETRY_MS
        return self._active_poll_ms if self._work_active else self._poll_ms

    def set_work_active(self, active: bool) -> None:
        """Codex 是否有任务在运行：True→高频轮询，False→空闲常规间隔。

        上升沿且上次取数已超过高频间隔时立即取一次，让开始工作后的
        余额变化尽快可见（慢请求期间只改标记，完成时归位间隔）。
        """
        active = bool(active)
        if active == self._work_active:
            return
        self._work_active = active
        log.info("套餐轮询模式切换: %s（常规间隔 %d 秒）",
                 "Codex 工作中" if active else "空闲",
                 (self._active_poll_ms if active else self._poll_ms) // 1000)
        if self._timer is None or self._in_flight:
            return
        if active:
            stale = (self._last_poll_at is None
                     or time.time() - self._last_poll_at >= self._active_poll_ms / 1000)
            if stale:
                self._poll()  # 完成后由 _on_fetched 设置高频间隔
                return
        wanted = self._wanted_interval()
        if self._timer.interval() != wanted:
            self._timer.setInterval(wanted)

    def _poll(self) -> None:
        if self._in_flight:
            return  # 慢请求期间不堆积后台查询
        self._in_flight = True
        self._last_poll_at = time.time()
        self._pool.start(_FetchJob(self._fetch, self._fetched.emit))

    def _on_fetched(self, usage: PlanUsage) -> None:
        self._in_flight = False
        if self._refresh_pending:
            self._refresh_pending = False
            self._poll()
            return  # 旧凭据请求返回时不覆盖新登录状态
        retry = usage.state in RETRY_STATES
        self._retrying = retry
        if self._timer is not None:
            wanted = self._wanted_interval()
            if self._timer.interval() != wanted:
                self._timer.setInterval(wanted)
        if retry:
            log.info("套餐用量取数失败: %s，%d 秒后重试", usage.state.value, RETRY_MS // 1000)
        if usage.state is UsageState.OK and self._last_state is not UsageState.OK:
            log.info("套餐用量查询已恢复: ok")
        self._last_state = usage.state
        self.updated.emit(usage)
