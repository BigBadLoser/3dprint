"""Tests for tools/belt_tension.py on synthetic recordings (tools/belt_synth.py).

    python3 -m unittest discover -s tests

Synthetic data only checks the logic; the real test is recordings from the K3.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import belt_synth  # noqa: E402
import belt_tension  # noqa: E402


def run(belts, names=None, **kw):
    an = {k: kw.pop(k) for k in ("target", "band") if k in kw}
    d = tempfile.mkdtemp()
    try:
        tt, x, ev = belt_synth.make(belts, **kw)
        belt_synth.write(d, tt, x)
        res = belt_tension.analyse(d, names or [], **an)
    finally:
        shutil.rmtree(d)
    return res, ev


class BeltTension(unittest.TestCase):
    def check(self, res, belts, tol=0.15, min_good=5):
        self.assertEqual(len(res["groups"]), len(belts))
        for g, fb in zip(res["groups"], belts):
            s = g["summary"]
            self.assertIsNotNone(s, g["name"])
            self.assertGreaterEqual(s["n_agree"], min_good, g)
            self.assertAlmostEqual(s["mean"], fb, delta=tol)

    def test_basic_pair(self):
        res, _ = run((110.0, 104.0), plucks=6)
        self.check(res, (110.0, 104.0))
        for g in res["groups"]:
            self.assertEqual(len(g["plucks"]), 6, "every pluck found once")
        lines = [l["f"] for l in res["taps"]["lines"]]
        self.assertTrue(any(abs(f - 132) < 2 for f in lines), lines)
        self.assertTrue(any(abs(f - 215) < 3 for f in lines), lines)

    def test_knock_rejected(self):
        res, _ = run((110.0, 104.0), plucks=6, knock_in=1)
        g = res["groups"][1]
        self.assertEqual(len(g["plucks"]), 7)
        self.assertEqual(sum(p["ok"] for p in g["plucks"]), 6)
        self.check(res, (110.0, 104.0))

    def test_seeds(self):
        for seed in range(2, 7):
            res, _ = run((95.0, 97.5), plucks=6, seed=seed)
            self.check(res, (95.0, 97.5))

    def test_weak_tone_not_read_as_2f(self):
        # f much weaker than 2f: must still report f, not 2f
        res, _ = run((110.0, 104.0), plucks=6, ratio_2f=5.0)
        self.check(res, (110.0, 104.0))

    def test_2f_on_toolhead_mode(self):
        # 66 Hz belt: 2f = 132 sits on the toolhead mode
        res, _ = run((66.0, 66.5), plucks=6)
        self.check(res, (66.0, 66.5))

    def test_tone_on_toolhead_mode(self):
        res, _ = run((131.0, 133.0), plucks=6, band=(60, 200))
        self.check(res, (131.0, 133.0))

    def test_lower_sample_rate(self):
        res, _ = run((110.0, 104.0), plucks=6, fs=1000.0)
        self.check(res, (110.0, 104.0))

    def test_no_2f_is_rejected_not_wrong(self):
        # with no usable 2f the tool must not invent a number
        res, _ = run((110.0, 104.0), plucks=6, ratio_2f=0.02)
        for g, fb in zip(res["groups"], (110.0, 104.0)):
            for p in g["plucks"]:
                if p["ok"]:
                    self.assertAlmostEqual(p["f"], fb, delta=0.3)

    def test_target_narrows_search(self):
        res, _ = run((110.0, 104.0), plucks=6, target=105.0)
        self.assertAlmostEqual(res["band"][0], 0.55 * 105)
        self.check(res, (110.0, 104.0))


if __name__ == "__main__":
    unittest.main()
