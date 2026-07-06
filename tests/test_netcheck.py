from ptt import netcheck


def test_returns_true_immediately_when_host_resolves():
    calls = []
    ok = netcheck.wait_online(
        "example.com",
        timeout_s=100,
        interval_s=5,
        resolves=lambda h: True,
        sleep=calls.append,
        monotonic=lambda: 0.0,
    )
    assert ok is True
    assert calls == []  # resolved on the first probe — never slept


def test_returns_true_after_a_few_retries():
    attempts = {"n": 0}
    slept = []

    def resolves(_host):
        attempts["n"] += 1
        return attempts["n"] >= 3  # fails twice, then succeeds

    ok = netcheck.wait_online(
        "example.com",
        timeout_s=100,
        interval_s=5,
        resolves=resolves,
        sleep=slept.append,
        monotonic=lambda: 0.0,
    )
    assert ok is True
    assert attempts["n"] == 3
    assert slept == [5, 5]  # slept between the two failed probes


def test_gives_up_after_timeout():
    clock = {"t": 0.0}
    slept = []

    def monotonic():
        return clock["t"]

    def sleep(dt):
        slept.append(dt)
        clock["t"] += dt  # advance the fake clock as we sleep

    ok = netcheck.wait_online(
        "example.com",
        timeout_s=10,
        interval_s=4,
        resolves=lambda h: False,  # never resolves
        sleep=sleep,
        monotonic=monotonic,
    )
    assert ok is False
    # Probes at t=0, 4, 8; at t>=10 (after the third sleep) it gives up.
    assert slept == [4, 4, 4]


def test_default_resolver_maps_getaddrinfo_to_bool(monkeypatch):
    # Fake getaddrinfo so the check stays offline: success -> True, OSError -> False.
    monkeypatch.setattr(netcheck.socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    assert netcheck._resolves("example.com") is True

    def boom(*a, **k):
        raise OSError("Temporary failure in name resolution")

    monkeypatch.setattr(netcheck.socket, "getaddrinfo", boom)
    assert netcheck._resolves("example.com") is False
