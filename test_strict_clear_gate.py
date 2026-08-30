import unittest
from result_detector import ResultStateMachine, CLEAR, FINAL_WIN, FINAL_LOSS


class StrictClearGateTests(unittest.TestCase):
    def test_activity_cannot_release_post_result_lock(self):
        s = ResultStateMachine()
        # Initial CLEAR arms detector.
        s.observe(CLEAR, 2, 2, 5.0, now=6.0)
        s.observe(CLEAR, 2, 2, 5.0, now=6.8)
        self.assertTrue(s.armed)
        # Confirm a WIN.
        s.observe(FINAL_WIN, 2, 2, 5.0, now=7.0, gameplay_activity=True)
        r = s.observe(FINAL_WIN, 2, 2, 5.0, now=7.8, gameplay_activity=True)
        self.assertEqual(r, "win")
        s.external_mutation(now=7.8)
        self.assertTrue(s.post_result_lock)
        # Even strong gameplay activity after cooldown cannot unlock it.
        for t in (13.0, 14.0, 15.0, 17.0):
            r = s.observe(FINAL_LOSS, 2, 2, 5.0, now=t, gameplay_activity=True)
            self.assertIsNone(r)
            self.assertTrue(s.post_result_lock)

    def test_stable_clear_is_required_before_next_result(self):
        s = ResultStateMachine()
        s.observe(CLEAR, 2, 2, 5.0, now=6.0)
        s.observe(CLEAR, 2, 2, 5.0, now=6.8)
        s.observe(FINAL_WIN, 2, 2, 5.0, now=7.0, gameplay_activity=True)
        self.assertEqual(s.observe(FINAL_WIN, 2, 2, 5.0, now=7.8, gameplay_activity=True), "win")
        s.external_mutation(now=7.8)

        # CLEAR during cooldown does not count toward unlock.
        for t in (8.5, 10.0, 12.0):
            self.assertIsNone(s.observe(CLEAR, 2, 2, 5.0, now=t))
            self.assertTrue(s.post_result_lock)

        # Need 5 continuous seconds of CLEAR after cooldown.
        self.assertIsNone(s.observe(CLEAR, 2, 2, 5.0, now=13.0))
        self.assertTrue(s.post_result_lock)
        self.assertIsNone(s.observe(CLEAR, 2, 2, 5.0, now=17.9))
        self.assertTrue(s.post_result_lock)
        self.assertIsNone(s.observe(CLEAR, 2, 2, 5.0, now=18.1))
        self.assertFalse(s.post_result_lock)
        self.assertTrue(s.armed)


if __name__ == "__main__":
    unittest.main()
