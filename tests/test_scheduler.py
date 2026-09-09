"""Faelligkeit des taeglichen Laufs."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

from conftest import TZ

from dynprice.config import AddonOptions
from dynprice.scheduler import Scheduler


class FakeRunner:
    def __init__(self, results=None):
        self.options = AddonOptions(run_hour=13, run_minute=30)
        self.tz = TZ
        self.calls = []
        self.results = list(results or [{"status": "ok", "day": "2026-11-21"}])

    def run_daily(self):
        self.calls.append("daily")
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    def run_day(self, day, publish_states=True):
        self.calls.append(day)
        return {"status": "ok", "day": day.isoformat()}


def scheduler(runner=None):
    return Scheduler(runner or FakeRunner(), threading.Event())


def moment(hour, minute=0, day=20):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def test_vor_der_laufzeit_nicht_faellig():
    assert scheduler().due(moment(12, 0)) is False


def test_ab_der_laufzeit_faellig():
    assert scheduler().due(moment(13, 30)) is True


def test_nach_erfolg_am_selben_tag_nicht_erneut():
    sched = scheduler()
    sched.last_success = date(2026, 11, 20)
    assert sched.due(moment(15, 0)) is False
    assert sched.due(moment(15, 0, day=21)) is True


def test_wiederholung_erst_nach_der_wartezeit():
    sched = scheduler()
    sched.last_attempt = moment(13, 30)
    assert sched.due(moment(13, 40)) is False
    assert sched.due(moment(14, 0)) is True


def test_spaeter_abend_bricht_die_versuche_ab():
    sched = scheduler()
    sched.last_attempt = moment(22, 0)
    assert sched.due(moment(23, 30)) is False


def test_erfolgreicher_lauf_merkt_sich_den_tag():
    runner = FakeRunner()
    sched = scheduler(runner)
    sched.run_once(moment(13, 30))
    assert sched.last_success == date(2026, 11, 20)
    assert runner.calls == ["daily"]


def test_fehlschlag_merkt_sich_keinen_erfolg():
    runner = FakeRunner([{"status": "keine_preise", "message": "noch nicht da"}])
    sched = scheduler(runner)
    sched.run_once(moment(13, 30))
    assert sched.last_success is None
    assert sched.status()["last_status"] == "keine_preise"


def test_startlauf_weicht_auf_heute_aus():
    class NurHeute(FakeRunner):
        def run_day(self, day, publish_states=True):
            self.calls.append(day)
            if len(self.calls) == 1:   # der Folgetag ist noch nicht bepreist
                return {"status": "keine_preise", "message": "morgen fehlt"}
            return {"status": "ok", "day": day.isoformat()}

    runner = NurHeute()
    sched = scheduler(runner)
    sched.startup()
    assert len(runner.calls) == 2
    assert runner.calls[0] == runner.calls[1] + timedelta(days=1)
    assert sched.last_result["status"] == "ok"
    assert sched.last_success is None
