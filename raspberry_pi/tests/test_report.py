import asyncio
import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, load_settings
from app.llm.openai_summary import INSTRUCTIONS, OpenAISummarizer
from app.main import create_app
from app.models import ActivitySample
from app.patient.history import ActivityHistory
from app.patient.report import ReportBuilder, period_stats
from tests.test_condition import POSE_ENV, FakePose
from tests.conftest import FakeDetector

NOW = datetime(2026, 10, 4, 18, 0).astimezone().timestamp()
INTERVAL = 3.0


def run(coro):
    return asyncio.run(coro)


def readings(start: float, minutes: float, moved_every: int, posture="lying", in_view=True) -> list[ActivitySample]:
    """One reading every INTERVAL seconds; every `moved_every`-th one has movement (0 = never)."""
    count = int(minutes * 60 / INTERVAL)
    return [ActivitySample(timestamp=start + i * INTERVAL, in_view=in_view, posture=posture if in_view else None,
                           movement=0.05 if moved_every and i % moved_every == 0 else 0.01,
                           moved=bool(moved_every and i % moved_every == 0) if in_view else None)
            for i in range(count)]


@pytest.fixture
def history(tmp_path):
    h = ActivityHistory(tmp_path / "patient.db", keep_days=30)
    yield h
    h.close()


def fill(history, samples):
    async def go():
        for s in samples:
            await history.record(s)
    run(go())


class FakeSummarizer:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def summarize(self, report):
        self.calls += 1
        if self.fail:
            raise RuntimeError("OpenAI HTTP 429: quota")
        return "Moved more than yesterday."


# ---------------------------------------------------------------- numbers

def test_period_stats_counts_coverage_postures_and_movement():
    samples = (readings(NOW - 3600, 30, moved_every=4)                       # 30 min lying, 25% moving
               + readings(NOW - 1800, 10, moved_every=0, posture="upright")  # 10 min sitting up, still
               + readings(NOW - 1200, 5, moved_every=0, in_view=False))      # 5 min out of view
    stats = period_stats(samples, NOW - 3600, NOW, INTERVAL)
    assert stats.observed_min == 45
    assert stats.enough_data
    assert stats.lying_pct == 75 and stats.upright_pct == 25 and stats.reclined_pct == 0
    assert stats.active_pct == 18.8                  # 150 moved of 800 comparable readings
    assert round(stats.in_view_pct) == 89
    assert stats.longest_still_min == pytest.approx(10, abs=0.1)


def test_gaps_are_not_counted_as_watching():
    samples = readings(NOW - 7200, 10, 2) + readings(NOW - 600, 10, 2)  # 1 h 40 min gap between
    # The gap adds at most 30 s (the "camera off" cut-off), not 1 h 40 min.
    assert period_stats(samples, NOW - 7200, NOW, INTERVAL).observed_min == pytest.approx(20, abs=0.6)


def test_little_data_is_flagged():
    stats = period_stats(readings(NOW - 600, 10, 2), NOW - 3600, NOW, INTERVAL)
    assert not stats.enough_data


# ---------------------------------------------------------------- report

def build(history, summarizer=None, hours=24, ai=True, builder=None):
    builder = builder or ReportBuilder(history, INTERVAL, summarizer, clock=lambda: NOW)
    return run(builder.build(hours, with_ai=ai))


@pytest.mark.parametrize("before, after, trend", [(10, 2, "more_active"), (2, 10, "less_active"),
                                                   (4, 4, "about_the_same")])
def test_trend_compares_with_previous_period(history, before, after, trend):
    fill(history, readings(NOW - 30 * 3600, 60, before) + readings(NOW - 6 * 3600, 60, after))
    report = build(history)
    assert report.trend == trend
    assert any("Moved in" in line for line in report.highlights)


def test_no_trend_without_data_for_both_periods(history):
    fill(history, readings(NOW - 6 * 3600, 60, 2))
    report = build(history)
    assert report.trend == "not_enough_data" and report.active_change_points is None


def test_report_never_calls_it_improvement(history):
    fill(history, readings(NOW - 30 * 3600, 60, 20) + readings(NOW - 6 * 3600, 60, 2))
    text = " ".join(build(history).highlights).lower()
    for word in ("improv", "better", "worse", "recover"):
        assert word not in text


def test_daily_series_covers_seven_local_days(history):
    fill(history, readings(NOW - 30 * 3600, 60, 2) + readings(NOW - 6 * 3600, 60, 4))
    daily = build(history).daily
    assert [d.day for d in daily][-2:] == ["2026-10-03", "2026-10-04"]
    assert len(daily) == 7
    assert daily[-1].active_pct == 25 and daily[-2].active_pct == 50
    assert daily[0].active_pct is None and daily[0].observed_min == 0


def test_ai_summary_is_cached_and_failures_keep_the_numbers(history):
    fill(history, readings(NOW - 6 * 3600, 60, 2))
    summarizer = FakeSummarizer()
    builder = ReportBuilder(history, INTERVAL, summarizer, clock=lambda: NOW)
    assert build(history, builder=builder).ai_summary == "Moved more than yesterday."
    build(history, builder=builder)
    assert summarizer.calls == 1

    report = build(history, FakeSummarizer(fail=True))
    assert report.ai_summary is None and "429" in report.ai_error and report.current.enough_data


def test_no_ai_call_without_enough_data(history):
    summarizer = FakeSummarizer()
    assert build(history, summarizer).ai_summary is None and summarizer.calls == 0


def test_history_drops_old_readings(tmp_path):
    path = tmp_path / "patient.db"
    first = ActivityHistory(path, keep_days=30)
    fill(first, [ActivitySample(timestamp=1.0, in_view=False), ActivitySample(timestamp=4e9, in_view=False)])
    first.close()
    second = ActivityHistory(path, keep_days=30)
    assert [s.timestamp for s in run(second.between(0, 5e9))] == [4e9]
    second.close()


# ---------------------------------------------------------------- OpenAI

LLM_ENV = dict(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test-abcdef", LLM_MODEL="gpt-5.4-mini", LLM_TIMEOUT_S="30",
               LLM_ASSISTANT="false")


def test_summarizer_sends_only_numbers_with_safety_rules(env, history):
    env.update(LLM_ENV)
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["auth"] = request.headers["authorization"]
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": " Moved in 50% of checks. "}}]})

    fill(history, readings(NOW - 6 * 3600, 60, 2))
    report = build(history, ai=False)
    text = run(OpenAISummarizer(load_settings(env), transport=httpx.MockTransport(handler)).summarize(report))
    assert text == "Moved in 50% of checks."
    assert sent["auth"] == "Bearer sk-test-abcdef"
    body = sent["body"]
    assert body["model"] == "gpt-5.4-mini"
    assert body["messages"][0]["content"] == INSTRUCTIONS and "never say or imply" in INSTRUCTIONS.lower()
    data = json.loads(body["messages"][1]["content"])
    assert set(data) == {"hours", "current", "previous", "trend", "active_change_points", "daily"}
    assert "image" not in body["messages"][1]["content"]


@pytest.mark.parametrize("response, error", [
    (httpx.Response(401, json={"error": {"message": "Incorrect API key"}}), "401: Incorrect API key"),
    (httpx.Response(200, json={"choices": []}), "no text"),
    (httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}), "empty"),
])
def test_summarizer_errors(env, history, response, error):
    env.update(LLM_ENV)
    summarizer = OpenAISummarizer(load_settings(env), transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(RuntimeError, match=error):
        run(summarizer.summarize(build(history, ai=False)))


def test_llm_settings(env):
    env.update(LLM_ENV)
    del env["LLM_MODEL"]
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        load_settings(env)
    env.update(LLM_PROVIDER="anthropic")
    with pytest.raises(ConfigError, match="not implemented"):
        load_settings(env)
    env.update(LLM_ENV)
    assert "sk-test-abcdef" not in repr(load_settings(env))


# ---------------------------------------------------------------- end to end

def test_pose_readings_are_stored_and_reported(env, mock_client, caregiver, tmp_path):
    env.update(POSE_ENV, DATABASE_PATH=str(tmp_path / "patient.db"), HISTORY_DAYS="30")
    app = create_app(load_settings(env), controller=mock_client, caregiver=caregiver, detector=FakeDetector(),
                     pose=FakePose())
    with TestClient(app) as http:
        http.post("/vision/detect", files={"image": ("live.jpg", b"jpeg", "image/jpeg")})
        report = http.get("/report/data", params={"hours": 24, "ai": False}).json()
        assert report["current"]["readings"] == 1 and report["trend"] == "not_enough_data"
        assert "Activity report" in http.get("/report").text
        assert http.get("/report/data", params={"hours": 500}).status_code == 422


def test_reports_off_without_database(settings, mock_client):
    with TestClient(create_app(settings, controller=mock_client)) as http:
        assert http.get("/report/data").status_code == 503


def test_half_percentages_round_up_like_the_web_page():
    from app.models import PeriodStats
    now = datetime.now().astimezone()
    stats = PeriodStats(start=now, end=now, readings=999, observed_min=180, enough_data=True, in_view_pct=100,
                        lying_pct=42.5, reclined_pct=42.5, upright_pct=15, active_pct=42.5, longest_still_min=1)
    from app.patient.report import highlights
    text = " ".join(highlights(stats, stats, "about_the_same", 24))
    assert "Moved in 43%" in text and "lying 43%" in text
