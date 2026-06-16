"""Deterministic regression tests for the weight-estimator math tail.

These exercise the PURE functions only (no fal/Anthropic/PIL calls), so they are
flake-free and run with plain `python3 -m pytest` or `python3 -m unittest`. Live
VLM output is nondeterministic and is covered separately by the eval harness, not
here. Run from repo root:  python3 -m unittest tests.test_weight_regression -v
"""

import math
import os
import tempfile
import unittest

import weight_estimator as we


class TestVolumeToWeight(unittest.TestCase):
    def test_basic(self):
        # 1000 mm^3 = 1 cm^3; * 15.6 g/cm^3 * 0.97 casting
        self.assertAlmostEqual(we.volume_to_weight(1000, 15.6, 1.0), 15.6, 3)
        self.assertAlmostEqual(we.volume_to_weight(1000, 15.6, 0.97), 15.132, 3)

    def test_negative_clamps_to_zero(self):
        self.assertEqual(we.volume_to_weight(-500, 15.6), 0.0)


class TestValidation(unittest.TestCase):
    def test_has_valid_total(self):
        self.assertTrue(we._has_valid_total({"total_metal_volume_mm3": 300}))
        self.assertTrue(we._has_valid_total({"total_metal_volume_mm3": "300"}))
        self.assertFalse(we._has_valid_total({}))
        self.assertFalse(we._has_valid_total({"total_metal_volume_mm3": 0}))
        self.assertFalse(we._has_valid_total({"total_metal_volume_mm3": -5}))
        self.assertFalse(we._has_valid_total({"total_metal_volume_mm3": float("nan")}))
        self.assertFalse(we._has_valid_total({"total_metal_volume_mm3": float("inf")}))
        self.assertFalse(we._has_valid_total({"total_metal_volume_mm3": "abc"}))

    def test_sanitize_volumes(self):
        d = {"total_metal_volume_mm3": 300, "shank_volume_mm3": float("inf"),
             "head_volume_mm3": "x", "stone_seat_volume_mm3": float("nan")}
        we._sanitize_volumes(d)
        self.assertEqual(d["total_metal_volume_mm3"], 300.0)
        self.assertEqual(d["shank_volume_mm3"], 0.0)
        self.assertEqual(d["head_volume_mm3"], 0.0)
        self.assertEqual(d["stone_seat_volume_mm3"], 0.0)

    def test_inf_never_reaches_grams(self):
        # The whole point: an inf volume must be rejected, not averaged to inf.
        self.assertFalse(we._has_valid_total({"total_metal_volume_mm3": float("inf")}))


class TestDensities(unittest.TestCase):
    """White/rose golds must NOT collapse to the yellow density (the ~6% bug)."""

    def test_color_specific(self):
        self.assertAlmostEqual(we.alloy_density("18k_yellow_gold"), 15.60, 2)
        self.assertAlmostEqual(we.alloy_density("18k_white_gold"), 14.64, 2)
        self.assertAlmostEqual(we.alloy_density("18k_rose_gold"), 15.18, 2)
        self.assertAlmostEqual(we.alloy_density("14k_white_gold"), 12.61, 2)
        self.assertAlmostEqual(we.alloy_density("14k_rose_gold"), 13.26, 2)

    def test_white_differs_from_yellow(self):
        self.assertLess(we.alloy_density("18k_white_gold"),
                        we.alloy_density("18k_yellow_gold"))

    def test_unknown_alloy_falls_back_to_18k(self):
        self.assertAlmostEqual(we.alloy_density("mystery"), 15.60, 2)


class TestConstructionFill(unittest.TestCase):
    def test_known(self):
        self.assertEqual(we._construction_fill("solid"), (0.85, None))
        self.assertEqual(we._construction_fill("hollow"), (0.50, None))

    def test_normalised_variants(self):
        self.assertEqual(we._construction_fill("open back"), (0.55, None))
        self.assertEqual(we._construction_fill("Partially Hollow"), (0.68, None))

    def test_unrecognized_is_flagged(self):
        fill, warn = we._construction_fill("weird-thing")
        self.assertEqual(fill, we._SHANK_FILL)
        self.assertEqual(warn, "weird-thing")

    def test_missing_is_flagged(self):
        fill, warn = we._construction_fill("")
        self.assertEqual(fill, we._SHANK_FILL)
        self.assertEqual(warn, "(missing)")


class TestRingSizeAndCalibration(unittest.TestCase):
    def test_us7_inner_diameter(self):
        # Published US 7 inner diameter ~17.3 mm
        self.assertAlmostEqual(we.us_ring_inner_diameter_mm(7), 17.32, 1)
        self.assertEqual(we.us_ring_inner_diameter_mm(""), 0.0)
        self.assertEqual(we.us_ring_inner_diameter_mm(None), 0.0)

    def test_calibration_noop_without_inner(self):
        est = {"total_metal_volume_mm3": 300}
        we.calibrate_to_ring_size(est, 7)
        self.assertEqual(est["_scale_applied"], 1.0)
        self.assertFalse(est["_scale_clamped"])

    def test_calibration_clamped_flag(self):
        # Model inner-diameter wildly small -> scale would blow past the clamp.
        est = {"total_metal_volume_mm3": 300, "inner_diameter_mm": 2.0}
        we.calibrate_to_ring_size(est, 7)
        self.assertTrue(est["_scale_clamped"])
        self.assertEqual(est["_scale_applied"], we._SCALE_MAX)

    def test_calibration_within_band_not_clamped(self):
        known = we.us_ring_inner_diameter_mm(7)
        est = {"total_metal_volume_mm3": 300, "inner_diameter_mm": known * 1.1}
        we.calibrate_to_ring_size(est, 7)
        self.assertFalse(est["_scale_clamped"])


class TestRefineShankConstructionWarning(unittest.TestCase):
    def test_unrecognized_construction_recorded(self):
        est = {"total_metal_volume_mm3": 300, "shank_volume_mm3": 100,
               "inner_diameter_mm": 17.3, "band_construction": "weird",
               "key_dimensions_mm": {"band_width": 2.0, "band_thickness": 1.8}}
        we.refine_shank_volume(est)
        self.assertEqual(est.get("_construction_warning"), "weird")

    def test_known_construction_no_warning(self):
        est = {"total_metal_volume_mm3": 300, "shank_volume_mm3": 100,
               "inner_diameter_mm": 17.3, "band_construction": "hollow",
               "key_dimensions_mm": {"band_width": 2.0, "band_thickness": 1.8}}
        we.refine_shank_volume(est)
        self.assertIsNone(est.get("_construction_warning"))


class TestReconcile(unittest.TestCase):
    def _two(self, a, b):
        return [{"_model": "m1", "total_metal_volume_mm3": a, "shank_volume_mm3": a * 0.5},
                {"_model": "m2", "total_metal_volume_mm3": b, "shank_volume_mm3": b * 0.5}]

    def test_mean_weight(self):
        r = we.reconcile(self._two(320, 360), "18k_yellow_gold")
        # mean vol 340 -> 0.34 * 15.6 * 0.97
        self.assertAlmostEqual(r["gold_weight_g"], 5.145, 2)
        self.assertEqual(r["volume_mm3"], 340.0)

    def test_confidence_medium_on_moderate_disagreement(self):
        r = we.reconcile(self._two(320, 360), "18k_yellow_gold")
        self.assertEqual(r["confidence"], "medium")  # ~11.8% disagreement

    def test_confidence_low_on_high_disagreement(self):
        r = we.reconcile(self._two(300, 420), "18k_yellow_gold")
        self.assertEqual(r["confidence"], "low")  # ~33%

    def test_single_model_capped_at_medium(self):
        r = we.reconcile([{"_model": "m1", "total_metal_volume_mm3": 300,
                           "shank_volume_mm3": 150}], "18k_yellow_gold")
        self.assertTrue(r["single_model"])
        self.assertEqual(r["confidence"], "medium")


class TestFinalizeConfidence(unittest.TestCase):
    def test_calibrated_stays_confident(self):
        out = {"scale_calibrated": True, "confidence": "high"}
        we._finalize_confidence(out, typed_ring=True, clamped=False)
        self.assertEqual(out["confidence"], "high")
        self.assertFalse(out["uncalibrated"])

    def test_typed_ring_but_uncalibrated_forced_low(self):
        out = {"scale_calibrated": False, "confidence": "high"}
        we._finalize_confidence(out, typed_ring=True, clamped=False)
        self.assertEqual(out["confidence"], "low")
        self.assertTrue(out["uncalibrated"])
        self.assertIn("could not be scale-anchored", out["confidence_reason"])

    def test_no_ring_size_forced_low(self):
        out = {"scale_calibrated": False, "confidence": "high"}
        we._finalize_confidence(out, typed_ring=False, clamped=False)
        self.assertEqual(out["confidence"], "low")
        self.assertIn("no ring size", out["confidence_reason"])

    def test_clamped_forced_low(self):
        out = {"scale_calibrated": True, "confidence": "high"}
        we._finalize_confidence(out, typed_ring=True, clamped=True)
        self.assertEqual(out["confidence"], "low")
        self.assertTrue(out["scale_clamped"])


class TestLoadImageBytes(unittest.TestCase):
    """The geometry-mode coupling fix: bytes/path/data must load, not just URLs."""

    def test_bytes_passthrough(self):
        self.assertEqual(we._load_image_bytes(b"abc"), b"abc")
        self.assertEqual(we._load_image_bytes(bytearray(b"xyz")), b"xyz")

    def test_data_url(self):
        # "abc" base64 == YWJj
        self.assertEqual(we._load_image_bytes("data:image/png;base64,YWJj"), b"abc")

    def test_local_path(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            f.write(b"\x89PNGdata")
            path = f.name
        try:
            self.assertEqual(we._load_image_bytes(path), b"\x89PNGdata")
        finally:
            os.unlink(path)

    def test_garbage_returns_none(self):
        self.assertIsNone(we._load_image_bytes(123))
        self.assertIsNone(we._load_image_bytes("/no/such/file.png"))
        self.assertIsNone(we._load_image_bytes(None))


class TestScaleToTarget(unittest.TestCase):
    """Target-weight mode: pin absolute volume from a user target; dimensions
    follow by cube-root; gemstones are independent of metal weight."""

    def _est(self):
        return {
            "gold_weight_g": 4.0, "volume_mm3": 264.0, "total_metal_volume_mm3": 264.0,
            "shank_volume_mm3": 180.0, "head_volume_mm3": 60.0,
            "shank_weight_range_g": [1.5, 1.7],
            "key_dimensions_mm": {"band_width": 2.0, "band_thickness": 1.6,
                                  "head_height": 5.0, "head_diameter": 8.0},
            "stones": [{"shape": "round", "count": 1, "length_mm": 6.5, "carat_each": 1.0}],
        }

    def test_weight_hits_target_exactly(self):
        out = we.scale_to_target(self._est(), 3.0)
        self.assertAlmostEqual(out["gold_weight_g"], 3.0, 3)

    def test_volume_scales_linearly(self):
        out = we.scale_to_target(self._est(), 2.0)  # k = 0.5
        self.assertAlmostEqual(out["volume_mm3"], 132.0, 1)
        self.assertAlmostEqual(out["shank_volume_mm3"], 90.0, 1)
        self.assertAlmostEqual(out["head_volume_mm3"], 30.0, 1)

    def test_linear_dims_scale_by_cuberoot(self):
        out = we.scale_to_target(self._est(), 8.0)  # k = 2 -> dims x 2**(1/3)=1.26
        f = 2.0 ** (1 / 3)
        self.assertAlmostEqual(out["key_dimensions_mm"]["band_width"], round(2.0 * f, 2), 2)
        self.assertAlmostEqual(out["key_dimensions_mm"]["head_diameter"], round(8.0 * f, 2), 2)

    def test_stones_unchanged(self):
        out = we.scale_to_target(self._est(), 1.0)  # big metal change
        self.assertEqual(out["stones"], self._est()["stones"])  # gemstones independent

    def test_noop_without_target(self):
        out = we.scale_to_target(self._est(), 0)
        self.assertEqual(out["target_scale"], 1.0)
        self.assertEqual(out["gold_weight_g"], 4.0)

    def test_cuberoot_dampens_weight_error(self):
        # a +16% weight scaling moves linear dims only ~+5% (cube-root law)
        out = we.scale_to_target(self._est(), 4.0 * 1.16)
        shift = out["key_dimensions_mm"]["band_width"] / 2.0 - 1
        self.assertLess(abs(shift - 0.05), 0.01)


class TestDimensionAnnotation(unittest.TestCase):
    """Code-drawn dimension labels: crisp, exact, never garbled (vs AI text)."""

    def test_annotate_draws_crisp_labels(self):
        import io
        import numpy as np
        from PIL import Image, ImageDraw
        import views3d
        img = Image.new("RGB", (480, 480), (255, 255, 255))
        ImageDraw.Draw(img).ellipse([120, 110, 360, 420], outline=(196, 178, 150), width=40)
        buf = io.BytesIO(); img.save(buf, format="PNG")
        meas = {"band_width": 1.9, "band_thickness": 1.55, "head_diameter": 7.78,
                "head_height": 7.35, "ring_size": "7", "gold_weight_g": 3.0}
        out = views3d.annotate_view(buf.getvalue(), "Front Elevation", meas)
        res = Image.open(io.BytesIO(out)).convert("RGB")
        self.assertEqual(res.size, (480, 480))           # same canvas
        a = np.asarray(res)
        dark = int(((a[:, :, 0] < 80) & (a[:, :, 1] < 80) & (a[:, :, 2] < 80)).sum())
        self.assertGreater(dark, 150)                    # crisp dark text/lines drawn

    def test_overall_height_from_ring_size(self):
        import views3d
        h = views3d._overall_height_mm({"ring_size": "7", "band_thickness": 1.55, "head_height": 7.35})
        self.assertTrue(27 < h < 29)  # ~17.3 inner + 3.1 band + 7.35 head


if __name__ == "__main__":
    unittest.main(verbosity=2)
