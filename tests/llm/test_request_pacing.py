import asyncio
import threading
import time

from esprit.llm.request_pacing import RequestPacer


def test_request_slot_works_across_event_loops(monkeypatch) -> None:
    config_values = {
        "esprit_llm_max_inflight": "1",
        "esprit_llm_min_start_interval_ms": "0",
        "esprit_llm_start_jitter_ms": "0",
        "esprit_llm_rate_limit_cooldown_s": "0",
        "esprit_llm_rate_limit_cooldown_max_s": "1",
    }
    monkeypatch.setattr(
        "esprit.llm.request_pacing.Config.get",
        lambda name: config_values.get(name),
    )

    pacer = RequestPacer()

    async def _acquire_slot() -> None:
        async with pacer.request_slot():
            return

    asyncio.run(_acquire_slot())
    asyncio.run(_acquire_slot())


def test_request_slot_emits_queue_wait_callbacks(monkeypatch) -> None:
    config_values = {
        "esprit_llm_max_inflight": "1",
        "esprit_llm_min_start_interval_ms": "0",
        "esprit_llm_start_jitter_ms": "0",
        "esprit_llm_rate_limit_cooldown_s": "0",
        "esprit_llm_rate_limit_cooldown_max_s": "1",
        "esprit_llm_wait_poll_interval_s": "0.05",
    }
    monkeypatch.setattr(
        "esprit.llm.request_pacing.Config.get",
        lambda name: config_values.get(name),
    )

    pacer = RequestPacer()
    callbacks: list[tuple[str, float]] = []

    async def _holder() -> None:
        async with pacer.request_slot():
            await asyncio.sleep(0.2)

    async def _queued_waiter() -> None:
        async with pacer.request_slot(
            on_wait=lambda reason, waited_s: callbacks.append((reason, waited_s))
        ):
            return

    async def _run() -> None:
        holder_task = asyncio.create_task(_holder())
        await asyncio.sleep(0.03)
        await _queued_waiter()
        await holder_task

    asyncio.run(_run())

    assert any(reason == "queue" for reason, _ in callbacks)


def test_request_slot_enforces_global_capacity_across_threads(monkeypatch) -> None:
    config_values = {
        "esprit_llm_max_inflight": "1",
        "esprit_llm_min_start_interval_ms": "0",
        "esprit_llm_start_jitter_ms": "0",
        "esprit_llm_rate_limit_cooldown_s": "0",
        "esprit_llm_rate_limit_cooldown_max_s": "1",
        "esprit_llm_wait_poll_interval_s": "0.05",
    }
    monkeypatch.setattr(
        "esprit.llm.request_pacing.Config.get",
        lambda name: config_values.get(name),
    )

    pacer = RequestPacer()
    release_first = threading.Event()
    first_acquired = threading.Event()
    acquisitions: list[str] = []

    def _worker(name: str) -> None:
        async def _run() -> None:
            async with pacer.request_slot():
                acquisitions.append(name)
                if name == "first":
                    first_acquired.set()
                    await asyncio.to_thread(release_first.wait)

        asyncio.run(_run())

    t1 = threading.Thread(target=_worker, args=("first",), daemon=True)
    t2 = threading.Thread(target=_worker, args=("second",), daemon=True)
    t1.start()
    assert first_acquired.wait(timeout=2.0)

    t2.start()
    time.sleep(0.15)
    assert acquisitions == ["first"]

    release_first.set()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert acquisitions == ["first", "second"]
