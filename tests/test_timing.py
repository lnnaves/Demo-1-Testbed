import unittest

from scripts.testbed.timing import Timeline


class FakeClocks:
    def __init__(self):
        self.mono = 100.0
        self.wall = 1_800_000_000.0

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.clocks = FakeClocks()
        self.timeline = Timeline(monotonic=lambda: self.clocks.mono, wall=lambda: self.clocks.wall)

    def test_phases_are_closed_by_the_next_start(self):
        self.timeline.start("build_network")
        self.clocks.advance(7.5)
        self.timeline.start("wait_network")
        self.clocks.advance(2.0)
        self.timeline.finish()

        result = self.timeline.to_dict()
        self.assertEqual([p["name"] for p in result["phases"]], ["build_network", "wait_network"])
        self.assertEqual([p["duration_seconds"] for p in result["phases"]], [7.5, 2.0])
        self.assertEqual(result["phases"][0]["started_at"], 1_800_000_000.0)
        self.assertEqual(result["phases"][0]["ended_at"], result["phases"][1]["started_at"])
        self.assertEqual(result["duration_seconds"], 9.5)
        self.assertEqual(result["ended_at"] - result["started_at"], 9.5)

    def test_failed_phase_is_marked(self):
        self.timeline.start("wait_network")
        self.clocks.advance(60.0)
        self.timeline.fail()
        self.timeline.start("cleanup")
        self.timeline.finish()

        statuses = [(p["name"], p["status"]) for p in self.timeline.to_dict()["phases"]]
        self.assertEqual(statuses, [("wait_network", "failed"), ("cleanup", "ok")])

    def test_fail_without_running_phase_is_harmless(self):
        self.timeline.fail()
        self.timeline.finish()

        self.assertEqual(self.timeline.to_dict()["phases"], [])

    def test_local_time_is_readable_iso(self):
        self.timeline.start("x")
        self.timeline.finish()
        phase = self.timeline.to_dict()["phases"][0]

        self.assertRegex(phase["started_at_local"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")

    def test_durations_ignore_wall_clock_jumps(self):
        self.timeline.start("x")
        self.clocks.mono += 1.0
        self.clocks.wall -= 3600.0  # e.g. NTP correction
        self.timeline.finish()

        self.assertEqual(self.timeline.to_dict()["phases"][0]["duration_seconds"], 1.0)


if __name__ == "__main__":
    unittest.main()
