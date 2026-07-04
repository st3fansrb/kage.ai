"""WP-G1 — !stop kill switch: omoară procesele-agent + pauzează scheduler-ul."""
import orchestrator as o


class _FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


class _FakeScheduler:
    def __init__(self):
        self.paused = False

    def pause(self):
        self.paused = True


def test_register_unregister():
    o._running_procs.clear()
    p = _FakeProc()
    o._register_proc(p)
    assert p in o._running_procs
    o._unregister_proc(p)
    assert p not in o._running_procs


def test_unregister_none_is_safe():
    o._unregister_proc(None)  # nu trebuie să arunce


def test_stop_all_kills_procs_and_pauses(monkeypatch):
    o._running_procs.clear()
    procs = [_FakeProc() for _ in range(3)]
    for p in procs:
        o._register_proc(p)
    sched = _FakeScheduler()
    monkeypatch.setattr(o, "_scheduler", sched)

    result = o._stop_all()

    assert result["procs_killed"] == 3
    assert result["scheduler_paused"] is True
    assert all(p.terminated for p in procs)
    assert len(o._running_procs) == 0  # registrul e golit


def test_stop_all_no_scheduler(monkeypatch):
    o._running_procs.clear()
    monkeypatch.setattr(o, "_scheduler", None)
    result = o._stop_all()
    assert result["procs_killed"] == 0
    assert result["scheduler_paused"] is False


def test_stop_all_survives_dead_proc(monkeypatch):
    o._running_procs.clear()

    class _Dead:
        def terminate(self):
            raise ProcessLookupError()

    o._register_proc(_Dead())
    monkeypatch.setattr(o, "_scheduler", None)
    # nu trebuie să arunce, doar să curețe registrul
    o._stop_all()
    assert len(o._running_procs) == 0
