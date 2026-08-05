"""Tests for background task tracking and the /ping status it drives.

The status is what stops AgentCore Runtime from reclaiming the microVM while
work is still in flight, so the mapping and the bookkeeping around it are load
bearing: a stuck "HealthyBusy" leaks sessions until maxLifetime, and a premature
"Healthy" kills work mid-flight.
"""
import threading

import pytest

from agent import async_tasks


@pytest.fixture(autouse=True)
def clean_registry():
    async_tasks.reset()
    yield
    async_tasks.reset()


class TestPingStatus:
    """The status string is the platform's only idle signal."""

    def test_idle_when_nothing_is_registered(self):
        assert async_tasks.ping_status() == "Healthy"

    def test_busy_while_work_is_in_flight(self):
        async_tasks.begin("research")
        assert async_tasks.ping_status() == "HealthyBusy"

    def test_idle_again_once_work_ends(self):
        task_id = async_tasks.begin("research")
        async_tasks.end(task_id)
        assert async_tasks.ping_status() == "Healthy"

    # The platform reads these two exact spellings; anything else is not a
    # recognised status and the session's liveness stops being predictable.
    def test_uses_the_exact_status_spellings_the_platform_expects(self):
        assert async_tasks.ping_status() == "Healthy"
        async_tasks.begin("research")
        assert async_tasks.ping_status() == "HealthyBusy"

    def test_stays_busy_until_the_last_task_ends(self):
        """Concurrent researches must not let each other's container be reclaimed."""
        first = async_tasks.begin("research-1")
        second = async_tasks.begin("research-2")

        async_tasks.end(first)
        assert async_tasks.ping_status() == "HealthyBusy", (
            "one research finishing marked the session idle while another ran"
        )

        async_tasks.end(second)
        assert async_tasks.ping_status() == "Healthy"


class TestTaskBookkeeping:
    def test_ids_are_unique_across_tasks(self):
        ids = {async_tasks.begin(f"task-{i}") for i in range(50)}
        assert len(ids) == 50

    def test_reused_id_does_not_end_another_task(self):
        """end() runs from finally blocks that can be reached twice."""
        first = async_tasks.begin("research-1")
        async_tasks.end(first)
        second = async_tasks.begin("research-2")

        assert async_tasks.end(first) is False
        assert async_tasks.ping_status() == "HealthyBusy", (
            f"ending stale id {first} also ended the live task {second}"
        )

    def test_unknown_id_is_reported_not_raised(self):
        # Raising here would propagate out of a finally block and mask the real error.
        assert async_tasks.end(9999) is False

    def test_active_tasks_reports_names(self):
        async_tasks.begin("research", {"session": "s1"})
        active = async_tasks.active_tasks()
        assert len(active) == 1
        task = next(iter(active.values()))
        assert task["name"] == "research"
        assert task["metadata"] == {"session": "s1"}

    def test_active_tasks_snapshot_does_not_alias_internal_state(self):
        task_id = async_tasks.begin("research")
        snapshot = async_tasks.active_tasks()
        snapshot[task_id]["name"] = "mutated"
        assert async_tasks.active_tasks()[task_id]["name"] == "research"

    def test_concurrent_registration_from_threads(self):
        """Tools run in a ThreadPoolExecutor, so begin/end arrive off the loop."""
        ids = []
        ids_lock = threading.Lock()

        def worker():
            task_id = async_tasks.begin("threaded")
            with ids_lock:
                ids.append(task_id)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(set(ids)) == 20
        assert len(async_tasks.active_tasks()) == 20

        for task_id in ids:
            assert async_tasks.end(task_id) is True
        assert async_tasks.ping_status() == "Healthy"


class TestPingEndpoint:
    """The endpoint has to reflect the tracker and stay cheap."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import health

        app = FastAPI()
        app.include_router(health.router)
        return TestClient(app)

    def test_reports_healthy_when_idle(self):
        response = self._client().get("/ping")
        assert response.status_code == 200
        assert response.json() == {"status": "Healthy"}

    def test_reports_busy_while_work_is_in_flight(self):
        async_tasks.begin("research")
        response = self._client().get("/ping")
        assert response.status_code == 200
        assert response.json() == {"status": "HealthyBusy"}

    # A timestamp that advances on every ping reads as a continuous status
    # change, which prevents the idle timeout from ever firing and exhausts the
    # session quota. The platform tracks changes itself, so the field is omitted.
    def test_omits_time_of_last_update(self):
        response = self._client().get("/ping")
        assert "time_of_last_update" not in response.json()

    def test_ping_is_get_only(self):
        assert self._client().post("/ping").status_code == 405
