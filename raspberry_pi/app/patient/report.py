"""Activity reports: compares one period of camera readings with the period before it.

The report describes activity ("moved in 42% of checks, up from 28%"). It never says
the patient is improving or getting worse: more movement can also mean restlessness or
discomfort, and that judgement belongs to the care team.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta

from app.models import ActivityReport, ActivitySample, DailyActivity, PeriodStats
from app.patient.history import ActivityHistory

MIN_OBSERVED_MIN = 30      # less camera time than this is too little to compare
MIN_COMPARABLE = 20        # readings with a movement value
TREND_POINTS = 10          # active % must differ by this much to call it a change
DAYS_SHOWN = 7
AI_CACHE_S = 15 * 60       # an AI summary is reused this long, so page refreshes don't each cost a call


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _whole(value: float) -> str:
    """Rounds half up, like the web pages do (Python's round() sends 42.5 to 42)."""
    return str(int(value + 0.5))


def _duration(minutes: float) -> str:
    minutes = int(round(minutes))
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def period_stats(samples: list[ActivitySample], start: float, end: float, interval_s: float) -> PeriodStats:
    # Readings closer together than max_gap are treated as continuous watching; a bigger
    # gap means the camera was off or the phone page was closed.
    max_gap = max(30.0, 5 * interval_s)
    observed_s = sum(min(b.timestamp - a.timestamp, max_gap) for a, b in zip(samples, samples[1:]))
    observed_s += interval_s if samples else 0

    in_view = [s for s in samples if s.in_view]
    postures = [s.posture for s in in_view if s.posture in ("lying", "reclined", "upright")]
    comparable = [s for s in in_view if s.moved is not None]

    longest = run_start = last = 0.0
    for s in comparable:
        if s.moved or (last and s.timestamp - last > max_gap):
            run_start = 0.0
        if not s.moved:
            run_start = run_start or s.timestamp
            longest = max(longest, s.timestamp - run_start)
        last = s.timestamp

    observed_min = round(observed_s / 60, 1)
    return PeriodStats(
        start=datetime.fromtimestamp(start).astimezone(), end=datetime.fromtimestamp(end).astimezone(),
        readings=len(samples), observed_min=observed_min,
        enough_data=observed_min >= MIN_OBSERVED_MIN and len(comparable) >= MIN_COMPARABLE,
        in_view_pct=_pct(len(in_view), len(samples)),
        lying_pct=_pct(postures.count("lying"), len(postures)),
        reclined_pct=_pct(postures.count("reclined"), len(postures)),
        upright_pct=_pct(postures.count("upright"), len(postures)),
        active_pct=_pct(sum(1 for s in comparable if s.moved), len(comparable)),
        longest_still_min=round(longest / 60, 1) if comparable else None)


def highlights(current: PeriodStats, previous: PeriodStats, trend: str, hours: int) -> list[str]:
    span = f"the last {hours} hours" if hours != 24 else "the last 24 hours"
    if current.readings == 0:
        return [f"No camera readings in {span}. Open the bedside phone page and start the camera."]
    lines = [f"The camera watched for {_duration(current.observed_min)} in {span}."]
    if not current.enough_data:
        lines.append(f"That's too little to describe activity reliably (needs at least {MIN_OBSERVED_MIN} min).")
        return lines
    activity = f"Moved in {_whole(current.active_pct)}% of checks"
    if trend == "not_enough_data":
        lines.append(f"{activity}. There isn't enough data from the period before to compare.")
    else:
        word = {"more_active": "more active than", "less_active": "less active than",
                "about_the_same": "about as active as"}[trend]
        lines.append(f"{activity}, {word} the {hours} hours before ({_whole(previous.active_pct)}%).")
    if current.lying_pct is not None:
        lines.append(f"Position: lying {_whole(current.lying_pct)}%, reclined {_whole(current.reclined_pct)}%, "
                     f"sitting or standing {_whole(current.upright_pct)}% of the time.")
    if current.longest_still_min:
        lines.append(f"Longest time without movement: {_duration(current.longest_still_min)}.")
    if current.in_view_pct is not None and current.in_view_pct < 90:
        lines.append(f"Out of the camera's view {_whole(100 - current.in_view_pct)}% of the time.")
    return lines


class ReportBuilder:
    def __init__(self, history: ActivityHistory, interval_s: float, summarizer=None,
                 clock: Callable[[], float] = time.time):
        self._history = history
        self._interval_s = interval_s
        self._summarizer = summarizer  # app.llm.openai_summary.OpenAISummarizer or None
        self._clock = clock
        self._ai_cache: dict[int, tuple[float, str]] = {}  # hours -> (made at, summary)

    async def build(self, hours: int, with_ai: bool) -> ActivityReport:
        now = self._clock()
        span = hours * 3600
        # One query covers the comparison periods and the daily chart.
        today = datetime.fromtimestamp(now).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        days = [today - timedelta(days=i) for i in range(DAYS_SHOWN - 1, -1, -1)]
        oldest = min(now - 2 * span, days[0].timestamp())
        samples = await self._history.between(oldest, now)

        def window(start: float, end: float) -> list[ActivitySample]:
            return [s for s in samples if start <= s.timestamp < end]

        current = period_stats(window(now - span, now), now - span, now, self._interval_s)
        previous = period_stats(window(now - 2 * span, now - span), now - 2 * span, now - span, self._interval_s)
        change = None
        if current.enough_data and previous.enough_data:
            change = round(current.active_pct - previous.active_pct, 1)
            trend = ("more_active" if change >= TREND_POINTS else
                     "less_active" if change <= -TREND_POINTS else "about_the_same")
        else:
            trend = "not_enough_data"

        daily = []
        for day in days:
            start = day.timestamp()
            end = min((day + timedelta(days=1)).timestamp(), now)
            stats = period_stats(window(start, end), start, end, self._interval_s)
            daily.append(DailyActivity(day=day.date().isoformat(), observed_min=stats.observed_min,
                                       active_pct=stats.active_pct if stats.enough_data else None))

        report = ActivityReport(generated_at=datetime.fromtimestamp(now).astimezone(), hours=hours, current=current,
                                previous=previous, trend=trend, active_change_points=change, daily=daily,
                                highlights=highlights(current, previous, trend, hours))
        if with_ai and self._summarizer is not None and current.enough_data:
            cached = self._ai_cache.get(hours)
            if cached and now - cached[0] < AI_CACHE_S:
                report.ai_summary = cached[1]
            else:
                try:
                    report.ai_summary = await self._summarizer.summarize(report)
                    self._ai_cache[hours] = (now, report.ai_summary)
                except Exception as exc:  # the numbers are still useful without the AI text
                    report.ai_error = f"AI summary unavailable: {exc}"
        return report
