import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "startup-launch-audit", "scripts"))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")

import calc  # noqa: E402

NBSP = " "
KEYS = ("pain", "som", "growth", "ability_to_pay", "build_ease", "advantage")

BASE = dict(price=990, variable_cost_pct=0.4, churn_month=0.15, traffic_month=1000,
            cr_visit_signup=0.05, cr_signup_paid=0.10, marketing_month=10000)

ONE_TIME = dict(model="one_time", price=2000, variable_cost_pct=0.1, repeat_purchases=0.5,
                traffic_month=500, cr_visit_signup=0.1, cr_signup_paid=0.1, marketing_month=10000)


def make_card(scores=8, evidence=None, someone_pays=True):
    if not isinstance(scores, dict):
        scores = {key: scores for key in KEYS}
    evidence = evidence or {}
    card = {key: {"score": scores[key], "evidence": evidence.get(key, "fact"), "note": ""} for key in KEYS}
    if someone_pays is not None:
        card["someone_pays"] = someone_pays
    return card


def scores_for(pain, som, growth, ability_to_pay, build_ease, advantage):
    return dict(pain=pain, som=som, growth=growth, ability_to_pay=ability_to_pay,
                build_ease=build_ease, advantage=advantage)


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = calc.main(argv)
    return code, out.getvalue(), err.getvalue()


class FormattingTests(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(calc.fmt_money(20554.3), "20" + NBSP + "554" + NBSP + "₽")
        self.assertEqual(calc.fmt_pct(0.15), "15" + NBSP + "%")
        self.assertEqual(calc.fmt_num(1.98), "2,0")
        self.assertEqual(calc.fmt_money(1500, "USD"), "$1" + NBSP + "500")

    def test_money_currencies_and_sign(self):
        self.assertEqual(calc.fmt_money(20554.3, "EUR"), "20" + NBSP + "554" + NBSP + "€")
        self.assertEqual(calc.fmt_money(-1500), "−1" + NBSP + "500" + NBSP + "₽")
        self.assertEqual(calc.fmt_money(-1500, "USD"), "−$1" + NBSP + "500")
        self.assertEqual(calc.fmt_money(-0.4), "−0,40" + NBSP + "₽")
        self.assertEqual(calc.fmt_money(7.5, "USD"), "$7,50")
        self.assertEqual(calc.fmt_money(7, "USD"), "$7")
        self.assertEqual(calc.fmt_money(99.994), "99,99" + NBSP + "₽")
        self.assertEqual(calc.fmt_money(150.4), "150" + NBSP + "₽")

    def test_num_and_pct_rounding(self):
        self.assertEqual(calc.fmt_num(20.76, 0), "21")
        self.assertEqual(calc.fmt_num(15000000, 0), "15" + NBSP + "000" + NBSP + "000")
        self.assertEqual(calc.fmt_num(2.5, 0), "3")
        self.assertEqual(calc.fmt_num(1234.56), "1" + NBSP + "234,6")
        self.assertEqual(calc.fmt_pct(0.025, 1), "2,5" + NBSP + "%")
        self.assertEqual(calc.fmt_pct(0.285), "29" + NBSP + "%")

    def test_missing_values_are_dash(self):
        self.assertEqual(calc.fmt_money(None), "—")
        self.assertEqual(calc.fmt_num(float("inf")), "—")
        self.assertEqual(calc.fmt_pct(None), "—")


class ScorecardTests(unittest.TestCase):
    def test_scorecard_go(self):
        r = calc.scorecard(make_card(8))
        self.assertEqual(r["total"], 8.0)
        self.assertEqual(r["verdict"], "go")
        self.assertEqual(r["gates"], [])
        self.assertEqual(r["confidence"], 1.0)
        self.assertEqual(r["confidence_label"], "high")

    def test_assumption_cap_and_gate(self):
        scores = {key: 9 for key in KEYS}
        r = calc.scorecard(make_card(scores, evidence={"pain": "assumption"}))
        self.assertEqual(r["criteria"]["pain"]["score"], 5)
        self.assertEqual(r["criteria"]["pain"]["raw_score"], 9)
        self.assertTrue(r["criteria"]["pain"]["capped"])
        self.assertEqual(r["total"], 8.0)
        self.assertEqual(r["verdict_raw"], "go")
        self.assertEqual(r["gates"], ["pain_unproven"])
        self.assertEqual(r["verdict"], "check")
        self.assertEqual(r["confidence"], 0.75)
        self.assertEqual(r["confidence_label"], "high")

    def test_cap_flag_only_when_score_is_lowered(self):
        scores = {key: 9 for key in KEYS}
        scores["pain"] = 4
        r = calc.scorecard(make_card(scores, evidence={"pain": "assumption"}))
        self.assertEqual(r["criteria"]["pain"]["score"], 4)
        self.assertFalse(r["criteria"]["pain"]["capped"])
        self.assertEqual(r["gates"], ["pain_unproven", "pain_low"])

    def _card(self, score, evidence="estimate", pain=None, someone_pays=True):
        card = {k: {"score": score, "evidence": evidence} for k in
                ("pain", "som", "growth", "ability_to_pay", "build_ease", "advantage")}
        if pain:
            card["pain"] = pain
        card["someone_pays"] = someone_pays
        return card

    def test_mvp_low_total_medium_confidence_means_test_first(self):
        sc = calc.scorecard(self._card(4, "estimate"), "mvp")
        self.assertEqual(sc["verdict_raw"], "shelve")
        self.assertEqual(sc["verdict"], "check")
        self.assertEqual(sc["adjustments"], ["mvp_test_first"])
        self.assertEqual(sc["verdict_label"], "Сначала проверить спрос")

    def test_mvp_low_total_high_confidence_means_stop(self):
        sc = calc.scorecard(self._card(4, "fact"), "mvp")
        self.assertEqual(sc["verdict"], "shelve")
        self.assertEqual(sc["adjustments"], [])
        self.assertEqual(sc["verdict_label"], "Не вкладываться — менять продукт или сегмент")

    def test_mvp_go_label(self):
        sc = calc.scorecard(self._card(9, "fact"), "mvp")
        self.assertEqual(sc["verdict_label"], "Вкладываться в рост")

    def test_idea_stage_keeps_shelve(self):
        sc = calc.scorecard(self._card(4, "estimate"), "idea")
        self.assertEqual(sc["verdict"], "shelve")
        self.assertEqual(sc["verdict_label"], "Отложить")

    def test_mvp_markdown_shows_adjustment(self):
        md = calc.render_md(calc.compute({"project": {"name": "x", "stage": "mvp"}, "scorecard": self._card(4)}))
        self.assertIn("Сначала проверить спрос", md)
        self.assertIn("Поправка на стадию", md)

    def test_boundaries(self):
        cases = [
            (scores_for(7, 8, 7, 8, 7, 8), 7.5, "check"),
            (scores_for(8, 8, 7, 8, 7, 7), 7.6, "go"),
            (scores_for(6, 6, 6, 6, 6, 6), 6.0, "check"),
            (scores_for(6, 6, 5, 6, 6, 6), 5.9, "shelve"),
        ]
        for scores, total, verdict in cases:
            with self.subTest(total=total):
                r = calc.scorecard(make_card(scores))
                self.assertEqual(r["total"], total)
                self.assertEqual(r["verdict"], verdict)

    def test_total_rounds_half_up_before_verdict(self):
        r = calc.scorecard(make_card(scores_for(6, 5, 3, 7, 7, 7)))
        self.assertEqual(r["total"], 6.0)
        self.assertEqual(r["verdict"], "check")

    def test_nobody_pays_gate(self):
        r = calc.scorecard(make_card(9, someone_pays=False))
        self.assertEqual(r["total"], 9.0)
        self.assertIn("nobody_pays", r["gates"])
        self.assertEqual(r["verdict"], "check")

    def test_pain_low_gate(self):
        scores = {key: 10 for key in KEYS}
        scores["pain"] = 4
        r = calc.scorecard(make_card(scores))
        self.assertIn("pain_low", r["gates"])
        self.assertNotIn("pain_unproven", r["gates"])
        self.assertEqual(r["verdict_raw"], "go")
        self.assertEqual(r["verdict"], "check")

    def test_gate_does_not_raise_a_low_verdict(self):
        r = calc.scorecard(make_card(4, someone_pays=False))
        self.assertEqual(r["verdict_raw"], "shelve")
        self.assertEqual(r["verdict"], "shelve")

    def test_someone_pays_is_optional(self):
        r = calc.scorecard(make_card(8, someone_pays=None))
        self.assertEqual(r["verdict"], "go")
        self.assertNotIn("nobody_pays", r["gates"])

    def test_confidence_levels(self):
        estimate = calc.scorecard(make_card(8, evidence={key: "estimate" for key in KEYS}))
        self.assertEqual(estimate["confidence"], 0.5)
        self.assertEqual(estimate["confidence_label"], "medium")
        assumption = calc.scorecard(make_card(8, evidence={key: "assumption" for key in KEYS}))
        self.assertEqual(assumption["confidence"], 0.0)
        self.assertEqual(assumption["confidence_label"], "low")

    def test_all_six_criteria_are_required(self):
        card = make_card(8)
        del card["advantage"]
        with self.assertRaises(calc.InputError) as ctx:
            calc.scorecard(card)
        self.assertTrue(str(ctx.exception).startswith("scorecard.advantage:"))

    def test_invalid_fields_name_the_field(self):
        card = make_card(8)
        card["growth"]["score"] = 11
        with self.assertRaises(calc.InputError) as ctx:
            calc.scorecard(card)
        self.assertEqual(str(ctx.exception), "scorecard.growth.score: должно быть от 1 до 10")
        card = make_card(8)
        card["som"]["evidence"] = "guess"
        with self.assertRaises(calc.InputError) as ctx:
            calc.scorecard(card)
        self.assertTrue(str(ctx.exception).startswith("scorecard.som.evidence:"))
        card = make_card(8)
        card["someone_pays"] = "yes"
        with self.assertRaises(calc.InputError) as ctx:
            calc.scorecard(card)
        self.assertTrue(str(ctx.exception).startswith("scorecard.someone_pays:"))


class SegmentsTests(unittest.TestCase):
    def test_segments_mean_and_pick(self):
        items = [
            {"name": "A", "pain": 5, "wtp": 6, "ease": 3},
            {"name": "B", "pain": 7, "wtp": 5, "ease": 8},
            {"name": "C", "pain": 9, "wtp": 9, "ease": 9, "size": 50000, "size_unit": "салонов"},
        ]
        rows = calc.segments(items)
        self.assertEqual([r["total"] for r in rows], [9.0, 6.7, 4.7])
        self.assertEqual([r["name"] for r in rows], ["C", "B", "A"])
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3])
        self.assertEqual([r["pick"] for r in rows], [True, False, False])
        self.assertEqual([r["meets_threshold"] for r in rows], [True, False, False])
        self.assertEqual(rows[0]["size"], 50000)
        self.assertEqual(rows[0]["size_unit"], "салонов")

    def test_threshold_is_strictly_above_seven(self):
        rows = calc.segments([{"name": "A", "pain": 7, "wtp": 7, "ease": 7}])
        self.assertEqual(rows[0]["total"], 7.0)
        self.assertFalse(rows[0]["meets_threshold"])
        self.assertTrue(rows[0]["pick"])

    def test_ties_keep_input_order(self):
        rows = calc.segments([{"name": "A", "pain": 6, "wtp": 6, "ease": 6},
                              {"name": "B", "pain": 6, "wtp": 6, "ease": 6}])
        self.assertEqual([r["name"] for r in rows], ["A", "B"])

    def test_invalid_segment(self):
        with self.assertRaises(calc.InputError) as ctx:
            calc.segments([{"name": "A", "pain": 5, "wtp": 12, "ease": 3}])
        self.assertEqual(str(ctx.exception), "segments[0].wtp: должно быть от 1 до 10")


class MarketTests(unittest.TestCase):
    MARKET = {"tam_count": 15e6, "tam_price_year": 10000, "sam_count": 60000, "price_year": 50000}

    def test_market_bottom_up(self):
        r = calc.market(self.MARKET, None)
        self.assertAlmostEqual(r["tam"], 1.5e11)
        self.assertAlmostEqual(r["sam"], 3e9)
        self.assertAlmostEqual(r["som_share"], 9e7)
        self.assertIsNone(r["som_capacity"])
        self.assertEqual(r["som_basis"], "share")
        self.assertAlmostEqual(r["som"], 9e7)

    def test_price_for_tam_falls_back_to_price_year(self):
        r = calc.market({"tam_count": 1000, "sam_count": 100, "price_year": 50000}, None)
        self.assertEqual(r["tam"], 5e7)
        self.assertEqual(r["sam"], 5e6)

    def test_som_share_year3_is_configurable(self):
        r = calc.market({**self.MARKET, "som_share_year3": 0.01}, None)
        self.assertAlmostEqual(r["som_share"], 3e7)

    def test_som_capped_by_funnel(self):
        f = calc.forecast(BASE)
        capacity = sum(m["revenue"] for m in f["months"][24:36])
        r = calc.market(self.MARKET, f)
        self.assertAlmostEqual(r["som_capacity"], capacity)
        self.assertEqual(r["som"], r["som_capacity"])
        self.assertLess(r["som"], r["som_share"])
        self.assertEqual(r["som_basis"], "capacity")

    def test_som_uses_share_when_it_is_smaller(self):
        f = calc.forecast(BASE)
        r = calc.market({"sam_count": 100, "price_year": 1000}, f)
        self.assertAlmostEqual(r["som_share"], 3000)
        self.assertEqual(r["som"], r["som_share"])
        self.assertEqual(r["som_basis"], "share")

    def test_short_horizon_has_no_capacity(self):
        f = calc.forecast({**BASE, "horizon_months": 24})
        r = calc.market(self.MARKET, f)
        self.assertIsNone(r["som_capacity"])
        self.assertEqual(r["som_basis"], "share")
        self.assertEqual([y["year"] for y in r["revenue_by_year"]], [1, 2])

    def test_revenue_by_year(self):
        f = calc.forecast(BASE)
        r = calc.market(self.MARKET, f)
        years = r["revenue_by_year"]
        self.assertEqual([y["year"] for y in years], [1, 2, 3])
        for i, year in enumerate(years):
            expected = sum(m["revenue"] for m in f["months"][12 * i:12 * (i + 1)])
            self.assertAlmostEqual(year["revenue"], expected)
            self.assertAlmostEqual(year["share_of_sam"], expected / 3e9)

    def test_top_down_gap(self):
        r = calc.market({**self.MARKET, "top_down": {"tam": 1.4e11, "sam": 2.5e9, "source": "отчёт"}}, None)
        sam = r["top_down"]["sam"]
        self.assertAlmostEqual(sam["gap"], 0.1818, places=3)
        self.assertEqual(sam["status"], "ok")
        self.assertEqual(r["top_down"]["tam"]["status"], "ok")
        self.assertEqual(r["top_down"]["source"], "отчёт")

    def test_top_down_warn_and_bad(self):
        warn = calc.market({**self.MARKET, "top_down": {"sam": 2e9}}, None)
        self.assertAlmostEqual(warn["top_down"]["sam"]["gap"], 0.4, places=3)
        self.assertEqual(warn["top_down"]["sam"]["status"], "warn")
        bad = calc.market({**self.MARKET, "top_down": {"sam": 1.5e9}}, None)
        self.assertAlmostEqual(bad["top_down"]["sam"]["gap"], 0.667, places=3)
        self.assertEqual(bad["top_down"]["sam"]["status"], "bad")
        self.assertNotIn("tam", bad["top_down"])

    def test_missing_price_is_reported(self):
        with self.assertRaises(calc.InputError) as ctx:
            calc.market({"tam_count": 1000}, None)
        self.assertTrue(str(ctx.exception).startswith("market."))


class UnitEconomicsTests(unittest.TestCase):
    def test_ltv_on_margin(self):
        u = calc.unit_economics(BASE)
        self.assertLess(abs(u["ltv"] - 3960), 0.5)
        self.assertEqual(u["cac"], 2000)
        self.assertEqual(round(u["ltv_cac"], 2), 1.98)
        self.assertEqual(round(u["payback_months"], 2), 3.37)

    def test_other_metrics(self):
        u = calc.unit_economics(BASE)
        self.assertAlmostEqual(u["gross_margin"], 0.6)
        self.assertAlmostEqual(u["margin"], 594)
        self.assertAlmostEqual(u["lifetime_months"], 1 / 0.15)
        self.assertAlmostEqual(u["ltv_revenue"], 990 / 0.15)
        self.assertAlmostEqual(u["new_paid"], 5)
        self.assertAlmostEqual(u["annual_churn"], 1 - 0.85 ** 12)
        self.assertEqual(u["warnings"], [])

    def test_zero_churn_caps_lifetime(self):
        u = calc.unit_economics({**BASE, "churn_month": 0})
        self.assertEqual(u["lifetime_months"], 60)
        self.assertTrue(u["warnings"])
        self.assertAlmostEqual(u["ltv"], 594 * 60)
        self.assertEqual(u["annual_churn"], 0)

    def test_founder_time_counts_in_cac_but_not_in_cash(self):
        e = {**BASE, "marketing_month": 0, "founder_time_cost_month": 10000}
        u = calc.unit_economics(e)
        self.assertIsNone(u["cac"])
        self.assertAlmostEqual(u["cac_with_time"], 2000)
        self.assertAlmostEqual(u["ltv_cac_with_time"], 1.98, places=2)
        self.assertFalse(any("оцените время" in w for w in u["warnings"]))
        self.assertEqual(calc.forecast(e)["months"][0]["costs"], calc.forecast({**BASE, "marketing_month": 0})["months"][0]["costs"])

    def test_founder_time_adds_to_money_cac(self):
        u = calc.unit_economics({**BASE, "founder_time_cost_month": 5000})
        self.assertEqual(u["cac"], 2000)
        self.assertAlmostEqual(u["cac_with_time"], 3000)

    def test_goal_reports_acquisition_with_time(self):
        e = {**BASE, "marketing_month": 0, "founder_time_cost_month": 10000, "goal_mrr": 100000}
        g = calc.goal(e, calc.forecast(e))
        self.assertIsNone(g["marketing_needed"])
        self.assertAlmostEqual(g["acquisition_with_time_needed"], 15.15 * 2000, delta=20)

    def test_free_acquisition_no_cac(self):
        u = calc.unit_economics({**BASE, "marketing_month": 0})
        self.assertIsNone(u["cac"])
        self.assertIsNone(u["ltv_cac"])
        self.assertIsNone(u["payback_months"])
        self.assertIsNone(u["payback_status"])
        self.assertTrue(any("привлечение бесплатное" in w for w in u["warnings"]))

    def test_zero_conversion_does_not_divide_by_zero(self):
        for field in ("cr_signup_paid", "cr_visit_signup", "traffic_month"):
            with self.subTest(field=field):
                u = calc.unit_economics({**BASE, field: 0})
                self.assertEqual(u["new_paid"], 0)
                self.assertIsNone(u["cac"])
                self.assertIsNone(u["ltv_cac"])
                self.assertTrue(u["warnings"])

    def test_explicit_cac_overrides_funnel(self):
        u = calc.unit_economics({**BASE, "cac": 1000})
        self.assertEqual(u["cac"], 1000)
        self.assertAlmostEqual(u["ltv_cac"], 3.96)

    def test_zero_margin_has_no_payback(self):
        u = calc.unit_economics({**BASE, "variable_cost_pct": 1})
        self.assertEqual(u["ltv"], 0)
        self.assertEqual(u["ltv_cac"], 0)
        self.assertIsNone(u["payback_months"])
        self.assertEqual(u["payback_status"], "never")
        self.assertTrue(u["warnings"])

    def test_ltv_cac_status_bands(self):
        cases = [(4000, "loss"), (3960, "weak"), (1320, "weak"), (1319, "healthy"),
                 (792, "healthy"), (790, "excellent")]
        for cac, status in cases:
            with self.subTest(cac=cac):
                self.assertEqual(calc.unit_economics({**BASE, "cac": cac})["ltv_cac_status"], status)

    def test_churn_status_bands(self):
        cases = [(0.049, "excellent"), (0.05, "ok"), (0.10, "ok"), (0.11, "warning"),
                 (0.15, "warning"), (0.16, "critical")]
        for churn, status in cases:
            with self.subTest(churn=churn):
                self.assertEqual(calc.unit_economics({**BASE, "churn_month": churn})["churn_status"], status)

    def test_payback_status_bands(self):
        slow_churn = {**BASE, "churn_month": 0.05}
        cases = [(3564, "good"), (3565, "acceptable"), (7128, "acceptable"), (7130, "trap"),
                 (11880, "trap"), (11881, "never")]
        for cac, status in cases:
            with self.subTest(cac=cac):
                self.assertEqual(calc.unit_economics({**slow_churn, "cac": cac})["payback_status"], status)
        self.assertEqual(calc.unit_economics({**BASE, "cac": 4158})["payback_status"], "never")

    def test_payback_longer_than_lifetime_warns(self):
        u = calc.unit_economics({**BASE, "cac": 4158})
        self.assertEqual(u["payback_status"], "never")
        self.assertTrue(any("дольше жизни клиента" in w for w in u["warnings"]))

    def test_one_time_ltv(self):
        u = calc.unit_economics(ONE_TIME)
        self.assertAlmostEqual(u["ltv"], 2700)
        self.assertAlmostEqual(u["ltv_revenue"], 3000)
        self.assertEqual(u["cac"], 2000)
        self.assertAlmostEqual(u["ltv_cac"], 1.35)
        self.assertAlmostEqual(u["payback_purchases"], 2000 / 1800)
        for key in ("churn_month", "lifetime_months", "annual_churn", "churn_status",
                    "payback_months", "payback_status"):
            self.assertIsNone(u[key], key)
        f = calc.forecast(ONE_TIME)
        self.assertTrue(all(m["mrr"] is None for m in f["months"]))
        for key in ("mrr_m6", "mrr_m12", "mrr_m36", "steady_customers"):
            self.assertIsNone(f[key], key)

    def test_invalid_economics_fields(self):
        cases = [
            ({**BASE, "cr_signup_paid": 1.5}, "economics.cr_signup_paid: должно быть от 0 до 1"),
            ({**BASE, "price": 0}, "economics.price: должно быть больше 0"),
            ({**BASE, "churn_month": "много"}, "economics.churn_month: должно быть числом"),
            ({**BASE, "horizon_months": 0}, "economics.horizon_months: должно быть целым числом от 1 до 120"),
            ({**BASE, "model": "lifetime"}, "economics.model: должно быть одним из: subscription, one_time"),
        ]
        for econ, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(calc.InputError) as ctx:
                    calc.unit_economics(econ)
                self.assertEqual(str(ctx.exception), message)
        missing = {k: v for k, v in BASE.items() if k != "marketing_month"}
        with self.assertRaises(calc.InputError) as ctx:
            calc.unit_economics(missing)
        self.assertEqual(str(ctx.exception), "economics.marketing_month: обязательное поле")


class ForecastTests(unittest.TestCase):
    def test_forecast_month6_with_churn(self):
        f = calc.forecast(BASE)
        month = f["months"][5]
        self.assertEqual(month["m"], 6)
        self.assertEqual(round(month["customers"], 2), 20.76)
        self.assertLessEqual(abs(month["mrr"] - 20554), 1)

    def test_steady_state(self):
        self.assertEqual(round(calc.forecast(BASE)["steady_customers"], 2), 33.33)

    def test_no_steady_state_with_growth_or_zero_churn(self):
        self.assertIsNone(calc.forecast({**BASE, "traffic_growth_month": 0.05})["steady_customers"])
        self.assertIsNone(calc.forecast({**BASE, "churn_month": 0})["steady_customers"])

    def test_month_fields_and_defaults(self):
        f = calc.forecast(BASE)
        self.assertEqual(len(f["months"]), 36)
        first = f["months"][0]
        self.assertEqual(set(first), {"m", "traffic", "new", "customers", "mrr", "revenue", "costs", "profit", "cash"})
        self.assertAlmostEqual(first["new"], 5)
        self.assertAlmostEqual(first["customers"], 5)
        self.assertAlmostEqual(first["costs"], 4950 * 0.4 + 10000)
        self.assertAlmostEqual(first["profit"], 4950 - first["costs"])
        self.assertAlmostEqual(first["cash"], first["profit"])
        self.assertAlmostEqual(f["mrr_m12"], f["months"][11]["mrr"])
        self.assertAlmostEqual(f["customers_m12"], f["months"][11]["customers"])
        self.assertAlmostEqual(f["mrr_m36"], f["months"][35]["mrr"])

    def test_start_customers_traffic_growth_and_horizon(self):
        e = {**BASE, "start_customers": 10, "traffic_growth_month": 0.1, "horizon_months": 12, "cash_now": 500}
        f = calc.forecast(e)
        self.assertEqual(len(f["months"]), 12)
        self.assertAlmostEqual(f["months"][0]["customers"], 10 * 0.85 + 5)
        self.assertAlmostEqual(f["months"][2]["traffic"], 1000 * 1.1 ** 2)
        self.assertAlmostEqual(f["months"][0]["cash"], 500 + f["months"][0]["profit"])
        self.assertIsNone(f["mrr_m36"])

    def test_goal_month(self):
        e = {**BASE, "goal_mrr": 20000}
        self.assertEqual(calc.forecast(e)["goal_month"], 6)
        self.assertIsNone(calc.forecast({**BASE, "goal_mrr": 100000})["goal_month"])
        self.assertIsNone(calc.forecast(BASE)["goal_month"])

    def test_breakeven_and_runway(self):
        e = dict(price=100, variable_cost_pct=0, churn_month=0, traffic_month=100,
                 cr_visit_signup=1, cr_signup_paid=0.1, marketing_month=5000, cash_now=6000)
        f = calc.forecast(e)
        self.assertEqual(f["breakeven_month"], 5)
        self.assertEqual(f["cash_out_month"], 2)
        self.assertFalse(f["default_alive"])

    def test_default_alive_when_profit_comes_before_cash_runs_out(self):
        e = dict(price=100, variable_cost_pct=0, churn_month=0, traffic_month=100,
                 cr_visit_signup=1, cr_signup_paid=0.1, marketing_month=5000, cash_now=20000)
        f = calc.forecast(e)
        self.assertEqual(f["breakeven_month"], 5)
        self.assertIsNone(f["cash_out_month"])
        self.assertTrue(f["default_alive"])

    def test_budget_ends_paid_traffic_and_spend_stop(self):
        e = {**BASE, "marketing_months": 3, "paid_traffic_share": 0.5}
        f = calc.forecast(e)
        m3, m4 = f["months"][2], f["months"][3]
        self.assertAlmostEqual(m3["traffic"], 1000)
        self.assertAlmostEqual(m4["traffic"], 500)
        self.assertAlmostEqual(m3["costs"], m3["revenue"] * 0.4 + 10000)
        self.assertAlmostEqual(m4["costs"], m4["revenue"] * 0.4)
        self.assertAlmostEqual(f["steady_customers"], 2.5 / 0.15)

    def test_budget_end_requires_paid_share(self):
        with self.assertRaises(calc.InputError):
            calc.forecast({**BASE, "marketing_months": 3})

    def test_budget_covering_horizon_changes_nothing(self):
        a = calc.forecast(BASE)
        b = calc.forecast({**BASE, "marketing_months": 36})
        self.assertAlmostEqual(a["months"][-1]["cash"], b["months"][-1]["cash"])
        self.assertAlmostEqual(a["steady_customers"], b["steady_customers"])

    def test_budget_note_in_markdown(self):
        md = calc.render_md(calc.compute({"economics": {**BASE, "marketing_months": 3, "paid_traffic_share": 0.5}}))
        self.assertIn("Бюджет на привлечение — 3 мес.", md)

    def test_unknown_cash_is_not_treated_as_zero(self):
        f = calc.forecast(BASE)
        self.assertFalse(f["cash_known"])
        self.assertIsNone(f["cash_out_month"])
        self.assertIsNone(f["default_alive"])
        self.assertGreater(f["cash_needed"], 0)

    def test_cash_needed_is_deepest_cumulative_loss(self):
        e = dict(price=100, variable_cost_pct=0, churn_month=0, traffic_month=100,
                 cr_visit_signup=1, cr_signup_paid=0.1, marketing_month=5000, cash_now=6000)
        f = calc.forecast(e)
        self.assertAlmostEqual(f["cash_needed"], 10000)
        self.assertTrue(f["cash_known"])
        self.assertEqual(f["cash_out_month"], 2)

    def test_profitable_from_start_is_alive_without_cash(self):
        e = dict(price=1000, variable_cost_pct=0, churn_month=0.1, traffic_month=100,
                 cr_visit_signup=1, cr_signup_paid=0.1, marketing_month=1000)
        f = calc.forecast(e)
        self.assertEqual(f["cash_needed"], 0)
        self.assertTrue(f["default_alive"])

    def test_one_time_revenue_by_month(self):
        f = calc.forecast(ONE_TIME)
        month = f["months"][2]
        self.assertAlmostEqual(month["new"], 5)
        self.assertAlmostEqual(month["customers"], 15)
        self.assertAlmostEqual(month["revenue"], 5 * 2000 * 1.5)
        self.assertAlmostEqual(month["costs"], 15000 * 0.1 + 10000)
        self.assertEqual(f["breakeven_month"], 1)
        self.assertTrue(f["default_alive"])


class ScenarioTests(unittest.TestCase):
    def test_scenarios_order(self):
        s = calc.scenarios(BASE)
        self.assertEqual(set(s), {"base", "pessimistic", "optimistic"})
        self.assertLess(s["pessimistic"]["mrr_m12"], s["base"]["mrr_m12"])
        self.assertLess(s["base"]["mrr_m12"], s["optimistic"]["mrr_m12"])

    def test_scenario_multipliers(self):
        s = calc.scenarios(BASE)
        self.assertEqual(s["pessimistic"]["factors"], {"acq": 0.7, "churn": 1.2, "price": 0.85})
        self.assertEqual(s["optimistic"]["factors"], {"acq": 1.3, "churn": 0.8, "price": 1.15})
        customers = 5 * 0.7 * (1 - 0.82 ** 12) / 0.18
        self.assertAlmostEqual(s["pessimistic"]["customers_m12"], customers)
        self.assertAlmostEqual(s["pessimistic"]["mrr_m12"], customers * 990 * 0.85)
        self.assertAlmostEqual(s["pessimistic"]["steady_customers"], 3.5 / 0.18)
        self.assertAlmostEqual(s["base"]["mrr_m6"], calc.forecast(BASE)["mrr_m6"])

    def test_scenario_cac_follows_attraction(self):
        s = calc.scenarios(BASE)
        self.assertAlmostEqual(s["base"]["cac"], 2000)
        self.assertAlmostEqual(s["pessimistic"]["cac"], 2000 / 0.7)
        self.assertAlmostEqual(s["optimistic"]["cac"], 2000 / 1.3)
        explicit = calc.scenarios({**BASE, "cac": 1000})
        self.assertAlmostEqual(explicit["pessimistic"]["cac"], 1000 / 0.7)
        self.assertLess(s["pessimistic"]["ltv_cac"], s["base"]["ltv_cac"])
        self.assertLess(s["base"]["ltv_cac"], s["optimistic"]["ltv_cac"])

    def test_scenario_marketing_budget_is_not_scaled(self):
        s = calc.scenarios(BASE)
        for name in ("pessimistic", "optimistic"):
            self.assertAlmostEqual(s[name]["months"][0]["costs"],
                                   s[name]["months"][0]["revenue"] * 0.4 + 10000)


class SensitivityTests(unittest.TestCase):
    def test_sensitivity_sorted(self):
        rows = calc.sensitivity(BASE)
        swings = [r["swing"] for r in rows]
        self.assertEqual(swings, sorted(swings, reverse=True))
        by_param = {r["param"]: r["swing"] for r in rows}
        self.assertAlmostEqual(by_param["churn_month"], by_param["cr_signup_paid"],
                               delta=0.05 * by_param["churn_month"])
        self.assertGreaterEqual(by_param["churn_month"], by_param["price"])
        self.assertGreaterEqual(by_param["cr_signup_paid"], by_param["price"])

    def test_sensitivity_ties_follow_parameter_list(self):
        rows = calc.sensitivity(BASE)
        self.assertEqual([r["param"] for r in rows],
                         ["churn_month", "marketing_month", "price", "cr_signup_paid",
                          "traffic_month", "variable_cost_pct"])

    def test_sensitivity_values(self):
        rows = {r["param"]: r for r in calc.sensitivity(BASE)}
        price = rows["price"]
        self.assertAlmostEqual(price["low_value"], 792)
        self.assertAlmostEqual(price["high_value"], 1188)
        self.assertAlmostEqual(price["ltv_cac_low"], 1.584)
        self.assertAlmostEqual(price["ltv_cac_high"], 2.376)
        self.assertAlmostEqual(price["swing"], 0.792)
        base_mrr = calc.forecast(BASE)["mrr_m12"]
        self.assertAlmostEqual(price["mrr_m12_low"], base_mrr * 0.8)
        self.assertAlmostEqual(price["mrr_m12_high"], base_mrr * 1.2)
        churn = rows["churn_month"]
        self.assertAlmostEqual(churn["ltv_cac_low"], 2.475)
        self.assertAlmostEqual(churn["ltv_cac_high"], 1.65)

    def test_missing_parameter_is_skipped(self):
        params = [r["param"] for r in calc.sensitivity(ONE_TIME)]
        self.assertNotIn("churn_month", params)
        self.assertIn("price", params)

    def test_rates_are_capped_at_one(self):
        rows = {r["param"]: r for r in calc.sensitivity({**BASE, "cr_signup_paid": 0.9})}
        self.assertEqual(rows["cr_signup_paid"]["high_value"], 1.0)


class GoalTests(unittest.TestCase):
    def test_goal(self):
        g = calc.goal({**BASE, "goal_mrr": 100000}, calc.forecast(BASE))
        self.assertEqual(round(g["customers_needed"], 2), 101.01)
        self.assertEqual(round(g["new_per_month_needed"], 2), 15.15)
        self.assertEqual(round(g["traffic_needed"]), 3030)
        self.assertAlmostEqual(g["marketing_needed"], 15.1515 * 2000, delta=1)
        self.assertAlmostEqual(g["mrr_ceiling"], 33000, delta=1)
        self.assertIsNone(g["goal_month_base"])
        self.assertIs(g["reachable"], False)

    def test_reachable_goal(self):
        g = calc.goal({**BASE, "goal_mrr": 20000}, calc.forecast(BASE))
        self.assertIs(g["reachable"], True)
        self.assertEqual(g["goal_month_base"], 6)

    def test_goal_without_target_is_empty(self):
        self.assertEqual(calc.goal(BASE, calc.forecast(BASE)), {})

    def test_goal_without_cac_has_no_marketing_estimate(self):
        g = calc.goal({**BASE, "marketing_month": 0, "goal_mrr": 100000}, None)
        self.assertIsNone(g["marketing_needed"])

    def test_goal_with_zero_conversion(self):
        e = {**BASE, "cr_signup_paid": 0, "goal_mrr": 100000}
        g = calc.goal(e, calc.forecast(e))
        self.assertIsNone(g["traffic_needed"])
        self.assertIs(g["reachable"], False)

    def test_goal_one_time(self):
        e = {**ONE_TIME, "goal_mrr": 30000}
        g = calc.goal(e, calc.forecast(e))
        self.assertIsNone(g["customers_needed"])
        self.assertAlmostEqual(g["new_per_month_needed"], 30000 / (2000 * 1.5))
        self.assertAlmostEqual(g["traffic_needed"], 10 / 0.01)
        self.assertIs(g["reachable"], False)
        self.assertIsNone(g["goal_month_base"])


class ComputeTests(unittest.TestCase):
    FULL = {
        "project": {"name": "Тест", "currency": "RUB", "stage": "mvp"},
        "scorecard": make_card(8),
        "segments": [{"name": "A", "pain": 8, "wtp": 8, "ease": 8}],
        "market": {"unit": "клиентов", "tam_count": 15e6, "tam_price_year": 10000,
                   "sam_count": 60000, "price_year": 50000},
        "economics": {**BASE, "goal_mrr": 100000, "cash_now": 100000},
    }

    def test_partial_input(self):
        r = calc.compute({"scorecard": make_card(8)})
        self.assertIn("scorecard", r)
        for key in ("forecast", "unit_economics", "funnel", "sensitivity", "goal", "market", "segments"):
            self.assertNotIn(key, r)
        self.assertEqual(calc.compute({}), {"warnings": []})

    def test_full_output_keys(self):
        r = calc.compute(self.FULL)
        self.assertEqual(list(r), ["project", "scorecard", "segments", "market", "unit_economics",
                                   "funnel", "forecast", "sensitivity", "goal", "warnings"])
        self.assertEqual(set(r["forecast"]), {"base", "pessimistic", "optimistic"})
        self.assertEqual(r["funnel"]["visits"], 1000)
        self.assertAlmostEqual(r["funnel"]["signups"], 50)
        self.assertAlmostEqual(r["funnel"]["paid"], 5)
        self.assertNotIn("warnings", r["unit_economics"])
        self.assertEqual(r["market"]["som_basis"], "capacity")
        self.assertEqual(r["project"]["currency"], "RUB")
        json.dumps(r, allow_nan=False)

    def test_input_is_not_modified(self):
        data = copy.deepcopy(self.FULL)
        calc.compute(data)
        self.assertEqual(data, self.FULL)

    def test_warnings_collected_in_russian(self):
        r = calc.compute(self.FULL)
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("цель недостижима", r["warnings"][0].lower())
        self.assertIn("потолок MRR 33" + NBSP + "000" + NBSP + "₽", r["warnings"][0])

    def test_warning_currency_follows_project(self):
        data = copy.deepcopy(self.FULL)
        data["project"]["currency"] = "USD"
        warning = calc.compute(data)["warnings"][0]
        self.assertIn("$33" + NBSP + "000", warning)

    def test_market_gap_bad_warns(self):
        data = copy.deepcopy(self.FULL)
        data["market"]["top_down"] = {"sam": 1.5e9}
        r = calc.compute(data)
        self.assertTrue(any("рынок определён неверно" in w for w in r["warnings"]))

    def test_zero_values_do_not_crash(self):
        economics = {**BASE, "churn_month": 0, "marketing_month": 0, "cr_signup_paid": 0,
                     "goal_mrr": 50000}
        r = calc.compute({"economics": economics})
        json.dumps(r, allow_nan=False)
        self.assertIsNone(r["unit_economics"]["cac"])
        self.assertIsNone(r["goal"]["traffic_needed"])
        text = " ".join(r["warnings"])
        self.assertIn("Отток равен нулю", text)
        self.assertIn("привлечение бесплатное", text)
        self.assertIn("Новых платящих нет", text)
        self.assertEqual(len(r["warnings"]), len(set(r["warnings"])))

    def test_one_time_model_end_to_end(self):
        r = calc.compute({"economics": {**ONE_TIME, "goal_mrr": 30000}})
        json.dumps(r, allow_nan=False)
        self.assertAlmostEqual(r["unit_economics"]["ltv"], 2700)
        base = r["forecast"]["base"]
        self.assertIsNone(base["mrr_m12"])
        self.assertAlmostEqual(base["months"][11]["revenue"], 15000)
        self.assertTrue(r["warnings"])
        self.assertIn("цель недостижима", r["warnings"][0].lower())

    def test_invalid_top_level_and_project(self):
        with self.assertRaises(calc.InputError):
            calc.compute([])
        with self.assertRaises(calc.InputError) as ctx:
            calc.compute({"project": {"currency": "GBP"}})
        self.assertTrue(str(ctx.exception).startswith("project.currency:"))


class CliTests(unittest.TestCase):
    def write(self, directory, name, payload):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        return path

    def test_invalid_rate_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "data.json", {"economics": {**BASE, "cr_signup_paid": 1.5}})
            code, out, err = run_main([path])
            self.assertEqual(code, 2)
            self.assertEqual(err.strip(), "economics.cr_signup_paid: должно быть от 0 до 1")
            self.assertFalse(os.path.exists(os.path.join(tmp, "calc.json")))

    def test_unreadable_inputs_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run_main([os.path.join(tmp, "missing.json")])
            self.assertEqual(code, 2)
            self.assertIn("missing.json", err)
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{oops")
            code, _, err = run_main([bad])
            self.assertEqual(code, 2)
            self.assertIn("некорректный JSON", err)
            code, _, err = run_main([self.write(tmp, "list.json", [1, 2])])
            self.assertEqual(code, 2)

    def test_cli_writes_outputs(self):
        data = {"project": {"name": "Тест", "currency": "RUB", "stage": "mvp"},
                "scorecard": make_card(8), "economics": {**BASE, "goal_mrr": 100000}}
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "data.json", data)
            out_path = os.path.join(tmp, "result", "calc.json")
            md_path = os.path.join(tmp, "tables.md")
            code, out, err = run_main([path, "--out", out_path, "--md", md_path])
            self.assertEqual((code, err), (0, ""))
            with open(out_path, encoding="utf-8") as fh:
                result = json.load(fh)
            self.assertIn("scorecard", result)
            self.assertIn("forecast", result)
            self.assertAlmostEqual(result["forecast"]["base"]["months"][5]["customers"],
                                   5 * (1 - 0.85 ** 6) / 0.15, places=5)
            with open(md_path, encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("### Оценка идеи", md)
            self.assertIn("20" + NBSP + "554" + NBSP + "₽", md)
            self.assertIn("цель недостижима", out.lower())

    def test_input_with_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.json")
            with open(path, "w", encoding="utf-8-sig") as fh:
                json.dump({"scorecard": make_card(8)}, fh, ensure_ascii=False)
            code, _, err = run_main([path])
            self.assertEqual((code, err), (0, ""))

    def test_default_output_next_to_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "data.json", {"scorecard": make_card(8)})
            code, _, _ = run_main([path])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "calc.json")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "tables.md")))

    def test_s3_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "x.json")
            md_path = os.path.join(tmp, "x.md")
            code, _, err = run_main([os.path.join(FIXTURES, "s3-avatar.json"), "--out", out_path, "--md", md_path])
            self.assertEqual((code, err), (0, ""))
            with open(out_path, encoding="utf-8") as fh:
                r = json.load(fh)
            u = r["unit_economics"]
            self.assertAlmostEqual(u["ltv"], 858)
            self.assertAlmostEqual(u["cac"], 1000)
            self.assertAlmostEqual(u["ltv_cac"], 0.858)
            self.assertLess(u["ltv_cac"], 1)
            self.assertEqual(u["ltv_cac_status"], "loss")
            self.assertAlmostEqual(u["payback_months"], 1000 / 214.5, places=4)
            self.assertEqual(u["payback_status"], "never")
            self.assertEqual(u["churn_status"], "critical")
            base = r["forecast"]["base"]
            self.assertAlmostEqual(base["steady_customers"], 120)
            self.assertEqual(base["cash_out_month"], 4)
            self.assertIsNone(base["breakeven_month"])
            self.assertFalse(base["default_alive"])
            self.assertIs(r["goal"]["reachable"], False)
            self.assertTrue(any("цель недостижима" in w.lower() for w in r["warnings"]))
            self.assertTrue(any("дольше жизни клиента" in w for w in r["warnings"]))
            with open(md_path, encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("### Юнит-экономика", md)
            self.assertIn("### Предупреждения", md)


class RenderMdTests(unittest.TestCase):
    HEADINGS = ["### Оценка идеи", "### Сегменты", "### Рынок", "### Юнит-экономика",
                "### Прогноз выручки", "### Чувствительность", "### Цель", "### Предупреждения"]

    def test_empty_result(self):
        self.assertEqual(calc.render_md({}, "RUB"), "")
        self.assertEqual(calc.render_md({"warnings": []}, "RUB"), "")

    def test_scorecard_only(self):
        card = make_card(scores_for(9, 9, 9, 9, 9, 9), evidence={"pain": "assumption"})
        md = calc.render_md(calc.compute({"scorecard": card}), "RUB")
        self.assertIn("### Оценка идеи", md)
        for heading in self.HEADINGS[1:]:
            self.assertNotIn(heading, md)
        self.assertIn("Сначала проверить", md)
        self.assertIn("Делать", md)
        self.assertIn("высокая", md)
        self.assertIn("допущениях", md)
        self.assertIn("8,0", md)

    def test_verdict_words(self):
        for scores, expected in ((8, "Делать"), (6, "Сначала проверить"), (4, "Отложить")):
            with self.subTest(expected=expected):
                md = calc.render_md(calc.compute({"scorecard": make_card(scores)}), "RUB")
                self.assertIn("**Вердикт:** " + expected, md)

    def test_full_report_has_every_section_in_order(self):
        data = dict(ComputeTests.FULL)
        data["market"] = {**data["market"], "top_down": {"tam": 1.4e11, "sam": 2.5e9, "source": "отчёт"}}
        md = calc.render_md(calc.compute(data), "RUB")
        positions = [md.index(heading) for heading in self.HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("20" + NBSP + "554" + NBSP + "₽", md)
        self.assertIn("1,98", md)
        self.assertNotIn("None", md)
        self.assertNotIn("nan", md.lower())

    def test_currency_symbols(self):
        economics = {**BASE, "goal_mrr": 100000}
        usd = calc.render_md(calc.compute({"project": {"currency": "USD"}, "economics": economics}), "USD")
        self.assertIn("$", usd)
        self.assertNotIn("₽", usd)
        eur = calc.render_md(calc.compute({"project": {"currency": "EUR"}, "economics": economics}), "EUR")
        self.assertIn("€", eur)
        self.assertNotIn("₽", eur)

    def test_table_cells_escape_pipes_and_newlines(self):
        card = make_card(8)
        card["pain"]["note"] = "ищут | платят\nза костыли"
        md = calc.render_md(calc.compute({"scorecard": card}), "RUB")
        self.assertIn("ищут \\| платят за костыли", md)
        rows = [line for line in md.splitlines() if line.startswith("| ")]
        counts = {line.replace("\\|", "").count("|") for line in rows}
        self.assertEqual(len(counts), 1)

    def test_one_time_report(self):
        md = calc.render_md(calc.compute({"economics": {**ONE_TIME, "goal_mrr": 30000}}), "RUB")
        self.assertIn("### Юнит-экономика", md)
        self.assertNotIn("MRR через", md)
        self.assertNotIn("None", md)

    def test_status_labels_come_from_benchmarks(self):
        with open(os.path.join(FIXTURES, "s3-avatar.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        md = calc.render_md(calc.compute(data), "RUB")
        self.assertIn("Убыток на каждом клиенте", md)
        self.assertIn("Привлечение не окупается никогда", md)
        self.assertIn("Критично: продукт не решает проблему или решает плохо", md)


if __name__ == "__main__":
    unittest.main()
