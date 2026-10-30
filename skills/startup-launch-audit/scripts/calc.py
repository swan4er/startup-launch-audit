"""Расчёты скилла startup-launch-audit: data.json → calc.json и таблицы Markdown."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

NBSP = "\u00a0"
MINUS = "\u2212"
DASH = "\u2014"

CURRENCIES = ("RUB", "USD", "EUR")
STAGES = ("idea", "mvp", "revenue")
MODELS = ("subscription", "one_time")
DEFAULT_HORIZON = 36
MAX_HORIZON = 120

CRITERIA = (
    ("pain", "Боль реальная", 25),
    ("som", "Объём рынка (SOM)", 20),
    ("growth", "Рост рынка", 10),
    ("ability_to_pay", "Платёжеспособность", 15),
    ("build_ease", "Простота запуска", 15),
    ("advantage", "Преимущество", 15),
)
EVIDENCE_HALVES = {"fact": 2, "estimate": 1, "assumption": 0}
EVIDENCE_LABELS = {"fact": "факт", "estimate": "оценка", "assumption": "допущение"}
ASSUMPTION_SCORE_CAP = 5
PAIN_LOW_BELOW = 5
VERDICT_GO_ABOVE = Decimal("7.5")
VERDICT_CHECK_FROM = Decimal("6")
VERDICT_RANK = {"shelve": 0, "check": 1, "go": 2}
VERDICT_LABELS = {"go": "Делать", "check": "Сначала проверить", "shelve": "Отложить"}
CONFIDENCE_HIGH_FROM = 0.7
CONFIDENCE_MEDIUM_FROM = 0.4
CONFIDENCE_LABELS = {"high": "высокая", "medium": "средняя", "low": "низкая"}
GATE_LABELS = {
    "pain_unproven": "боль держится только на допущениях",
    "pain_low": "боль оценена ниже 5",
    "nobody_pays": "никто не платит за решение этой проблемы",
}

SEGMENT_PICK_ABOVE = 7

SOM_SHARE_DEFAULT = 0.03
GAP_OK_UPTO = 0.30
GAP_WARN_UPTO = 0.50
GAP_LABELS = {
    "ok": "норма",
    "warn": "перепроверить допущения",
    "bad": "рынок определён неверно",
}

LIFETIME_CAP_MONTHS = 60
LTV_CAC_BANDS = ((1, True, "loss"), (3, False, "weak"), (5, False, "healthy"))
LTV_CAC_TOP = "excellent"
LTV_CAC_LABELS = {
    "loss": ("Убыток на каждом клиенте", "Срочно менять цену, CAC или модель"),
    "weak": ("Еле окупается", "Улучшать конверсию и удержание"),
    "healthy": ("Здоровая экономика", "Масштабировать"),
    "excellent": ("Отличная экономика", "Можно смелее вкладывать в рост"),
}
CHURN_BANDS = ((0.05, True, "excellent"), (0.10, False, "ok"), (0.15, False, "warning"))
CHURN_TOP = "critical"
CHURN_LABELS = {
    "excellent": "Отлично: продукт нужен",
    "ok": "Нормально для старта",
    "warning": "Тревожно: клиенты не видят ценности",
    "critical": "Критично: продукт не решает проблему или решает плохо",
}
PAYBACK_BANDS = ((6, False, "good"), (12, False, "acceptable"))
PAYBACK_TOP = "trap"
PAYBACK_LABELS = {
    "good": "Хорошо",
    "acceptable": "Приемлемо",
    "trap": "Ловушка для денег: даже при хорошем LTV/CAC деньги кончатся раньше, чем вернутся",
    "never": "Привлечение не окупается никогда",
}

SCENARIOS = (
    ("base", "Базовый", {"acq": 1.0, "churn": 1.0, "price": 1.0}),
    ("pessimistic", "Пессимистичный", {"acq": 0.7, "churn": 1.2, "price": 0.85}),
    ("optimistic", "Оптимистичный", {"acq": 1.3, "churn": 0.8, "price": 1.15}),
)
SENSITIVITY_STEP = 0.2
SENSITIVITY_PARAMS = ("price", "churn_month", "cr_signup_paid", "variable_cost_pct",
                      "marketing_month", "traffic_month")
SENSITIVITY_LABELS = {
    "price": "Цена",
    "churn_month": "Отток в месяц",
    "cr_signup_paid": "Конверсия из регистрации в оплату",
    "variable_cost_pct": "Доля переменных затрат",
    "marketing_month": "Стоимость привлечения (бюджет при том же трафике)",
    "traffic_month": "Трафик в месяц",
}
RATE_PARAMS = ("churn_month", "cr_signup_paid", "variable_cost_pct")

TOLERANCE = 1e-9


class InputError(ValueError):
    """Ошибка во входных данных: текст сообщения показывается пользователю."""


def _decimal(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        d = Decimal(str(x))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _render_decimal(d, digits):
    with localcontext() as ctx:
        ctx.prec = 120
        d = d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    sign = MINUS if d < 0 else ""
    whole, _, frac = format(abs(d), "f").partition(".")
    grouped = f"{int(whole):,}".replace(",", NBSP)
    return sign + grouped + ("," + frac if frac else "")


def fmt_num(x: float, digits: int = 1) -> str:
    """Число с десятичной запятой и неразрывными пробелами в разрядах."""
    d = _decimal(x)
    return DASH if d is None else _render_decimal(d, digits)


def fmt_pct(x: float, digits: int = 0) -> str:
    """Доля 0…1 как проценты: 0.15 → «15 %»."""
    d = _decimal(x)
    return DASH if d is None else _render_decimal(d * 100, digits) + NBSP + "%"


def fmt_money(x: float, currency: str = "RUB") -> str:
    """Деньги, округление до целого: «20 554 ₽», «$20 554», «20 554 €»."""
    d = _decimal(x)
    if d is None:
        return DASH
    text = _render_decimal(d, 0)
    sign, body = (MINUS, text[1:]) if text.startswith(MINUS) else ("", text)
    if currency == "USD":
        return sign + "$" + body
    symbol = {"RUB": "₽", "EUR": "€"}.get(currency, currency)
    return sign + body + NBSP + symbol


def _pct_auto(x):
    scaled = x * 100
    for digits in (0, 1):
        if abs(scaled * 10 ** digits - round(scaled * 10 ** digits)) < 1e-7:
            return fmt_pct(x, digits)
    return fmt_pct(x, 2)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _as_dict(value, path):
    if not isinstance(value, dict):
        raise InputError(f"{path}: должен быть объектом JSON")
    return value


def _number(obj, key, path, *, required=False, default=None, lo=None, hi=None, gt=None):
    where = f"{path}.{key}"
    value = obj.get(key)
    if value is None:
        if required:
            raise InputError(f"{where}: обязательное поле")
        return default
    if not _is_number(value):
        raise InputError(f"{where}: должно быть числом")
    if lo is not None and hi is not None:
        if not lo <= value <= hi:
            raise InputError(f"{where}: должно быть от {lo} до {hi}")
    elif lo is not None and value < lo:
        raise InputError(f"{where}: должно быть не меньше {lo}")
    elif hi is not None and value > hi:
        raise InputError(f"{where}: должно быть не больше {hi}")
    if gt is not None and value <= gt:
        raise InputError(f"{where}: должно быть больше {gt}")
    return value


def _integer(obj, key, path, *, default, lo, hi):
    where = f"{path}.{key}"
    value = obj.get(key)
    if value is None:
        return default
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise InputError(f"{where}: должно быть целым числом от {lo} до {hi}")
    return value


def _text(obj, key, path, *, required=False):
    where = f"{path}.{key}"
    value = obj.get(key)
    if value is None:
        if required:
            raise InputError(f"{where}: обязательное поле")
        return None
    if not isinstance(value, str):
        raise InputError(f"{where}: должно быть строкой")
    if required and not value.strip():
        raise InputError(f"{where}: не должно быть пустым")
    return value


def _choice(obj, key, path, choices, *, required=False, default=None):
    where = f"{path}.{key}"
    value = obj.get(key)
    if value is None:
        if required:
            raise InputError(f"{where}: обязательное поле")
        return default
    if not isinstance(value, str) or value not in choices:
        raise InputError(f"{where}: должно быть одним из: {', '.join(choices)}")
    return value


def _round_half_up(value, digits):
    return value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def _band(value, bands, last):
    value = round(value, 9)
    for limit, strict, key in bands:
        if (value < limit) if strict else (value <= limit):
            return key
    return last


def _ge(a, b):
    return a >= b - TOLERANCE * max(1.0, abs(b))


def _first_month(months, predicate):
    for row in months:
        if predicate(row):
            return row["m"]
    return None


def _factors(factors):
    k = {"acq": 1.0, "churn": 1.0, "price": 1.0}
    if factors:
        k.update(factors)
    return k


def _project(p):
    p = _as_dict(p, "project")
    return {
        "name": _text(p, "name", "project"),
        "currency": _choice(p, "currency", "project", CURRENCIES, default="RUB"),
        "stage": _choice(p, "stage", "project", STAGES),
    }


def scorecard(sc: dict) -> dict:
    """Оценка идеи: баллы с ограничением допущений, итог, ограничители вердикта, надёжность."""
    sc = _as_dict(sc, "scorecard")
    criteria = {}
    weighted = Decimal(0)
    halves = 0
    for key, _title, weight in CRITERIA:
        item = sc.get(key)
        if item is None:
            raise InputError(f"scorecard.{key}: обязательный критерий")
        path = f"scorecard.{key}"
        item = _as_dict(item, path)
        raw = _number(item, "score", path, required=True, lo=1, hi=10)
        evidence = _choice(item, "evidence", path, tuple(EVIDENCE_HALVES), required=True)
        score = min(raw, ASSUMPTION_SCORE_CAP) if evidence == "assumption" else raw
        criteria[key] = {
            "score": score,
            "raw_score": raw,
            "evidence": evidence,
            "weight": weight / 100,
            "capped": score < raw,
            "note": _text(item, "note", path),
        }
        weighted += Decimal(weight) * Decimal(str(score))
        halves += weight * EVIDENCE_HALVES[evidence]
    someone_pays = sc.get("someone_pays")
    if someone_pays is not None and not isinstance(someone_pays, bool):
        raise InputError("scorecard.someone_pays: должно быть true или false")

    total = _round_half_up(weighted / 100, 1)
    if total > VERDICT_GO_ABOVE:
        verdict_raw = "go"
    elif total >= VERDICT_CHECK_FROM:
        verdict_raw = "check"
    else:
        verdict_raw = "shelve"
    gates = []
    if criteria["pain"]["evidence"] == "assumption":
        gates.append("pain_unproven")
    if criteria["pain"]["score"] < PAIN_LOW_BELOW:
        gates.append("pain_low")
    if someone_pays is False:
        gates.append("nobody_pays")
    verdict = min(verdict_raw, "check", key=VERDICT_RANK.get) if gates else verdict_raw

    confidence = halves / 200
    if confidence >= CONFIDENCE_HIGH_FROM:
        label = "high"
    elif confidence >= CONFIDENCE_MEDIUM_FROM:
        label = "medium"
    else:
        label = "low"
    return {
        "criteria": criteria,
        "total": float(total),
        "verdict_raw": verdict_raw,
        "gates": gates,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_label": label,
        "someone_pays": someone_pays,
    }


def segments(items: list) -> list:
    """Сегменты: итог = среднее боли, готовности платить и лёгкости продажи, сортировка и выбор."""
    if not isinstance(items, list):
        raise InputError("segments: должен быть списком")
    rows = []
    for index, item in enumerate(items):
        path = f"segments[{index}]"
        item = _as_dict(item, path)
        pain = _number(item, "pain", path, required=True, lo=1, hi=10)
        wtp = _number(item, "wtp", path, required=True, lo=1, hi=10)
        ease = _number(item, "ease", path, required=True, lo=1, hi=10)
        mean = (Decimal(str(pain)) + Decimal(str(wtp)) + Decimal(str(ease))) / 3
        rows.append({
            "name": _text(item, "name", path, required=True),
            "size": _number(item, "size", path, lo=0),
            "size_unit": _text(item, "size_unit", path),
            "pain": pain,
            "wtp": wtp,
            "ease": ease,
            "total": float(_round_half_up(mean, 1)),
        })
    rows.sort(key=lambda row: -row["total"])
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
        row["pick"] = rank == 1
        row["meets_threshold"] = row["total"] > SEGMENT_PICK_ABOVE
    return rows


def _norm_econ(e: dict) -> dict:
    """Проверяет раздел economics и подставляет значения по умолчанию."""
    e = _as_dict(e, "economics")
    p = "economics"
    model = _choice(e, "model", p, MODELS, default="subscription")
    subscription = model == "subscription"
    return {
        "model": model,
        "price": _number(e, "price", p, required=True, gt=0),
        "variable_cost_pct": _number(e, "variable_cost_pct", p, required=True, lo=0, hi=1),
        "churn_month": _number(e, "churn_month", p, required=True, lo=0, hi=1) if subscription else None,
        "repeat_purchases": 0 if subscription else _number(e, "repeat_purchases", p, default=0, lo=0),
        "traffic_month": _number(e, "traffic_month", p, required=True, lo=0),
        "traffic_growth_month": _number(e, "traffic_growth_month", p, default=0, lo=-1),
        "cr_visit_signup": _number(e, "cr_visit_signup", p, required=True, lo=0, hi=1),
        "cr_signup_paid": _number(e, "cr_signup_paid", p, required=True, lo=0, hi=1),
        "marketing_month": _number(e, "marketing_month", p, required=True, lo=0),
        "cac": _number(e, "cac", p, lo=0),
        "fixed_costs_month": _number(e, "fixed_costs_month", p, default=0, lo=0),
        "cash_now": _number(e, "cash_now", p, lo=0),
        "start_customers": _number(e, "start_customers", p, default=0, lo=0),
        "goal_mrr": _number(e, "goal_mrr", p, gt=0),
        "horizon_months": _integer(e, "horizon_months", p, default=DEFAULT_HORIZON, lo=1, hi=MAX_HORIZON),
    }


def unit_economics(e: dict, factors: dict | None = None) -> dict:
    """Юнит-экономика: LTV от маржи, CAC, окупаемость и статусы по порогам."""
    e = _norm_econ(e)
    k = _factors(factors)
    subscription = e["model"] == "subscription"
    warnings = []
    price = e["price"] * k["price"]
    gross_margin = 1 - e["variable_cost_pct"]
    margin = price * gross_margin
    new_paid = e["traffic_month"] * e["cr_visit_signup"] * e["cr_signup_paid"] * k["acq"]

    if subscription:
        churn = min(1.0, e["churn_month"] * k["churn"])
        if churn > 0:
            lifetime = 1 / churn
        else:
            lifetime = LIFETIME_CAP_MONTHS
            warnings.append(f"Отток равен нулю: срок жизни клиента принят равным {LIFETIME_CAP_MONTHS} месяцам.")
        ltv = margin * lifetime
        ltv_revenue = price * lifetime
        annual_churn = 1 - (1 - churn) ** 12
        churn_status = _band(churn, CHURN_BANDS, CHURN_TOP)
    else:
        churn = lifetime = annual_churn = churn_status = None
        ltv_revenue = price * (1 + e["repeat_purchases"])
        ltv = ltv_revenue * gross_margin

    if new_paid <= 0:
        warnings.append("Новых платящих нет (трафик или конверсия равны нулю): CAC не определён.")
    explicit = e["cac"]
    cac = None
    if explicit is not None and explicit > 0:
        cac = explicit / k["acq"]
    elif explicit == 0 or (explicit is None and e["marketing_month"] == 0):
        reason = "CAC задан нулём" if explicit == 0 else "Бюджет на привлечение равен нулю"
        warnings.append(f"{reason}: привлечение бесплатное — CAC по деньгам 0, оцените своё время.")
    elif new_paid > 0:
        cac = e["marketing_month"] / new_paid
    if margin <= 0:
        warnings.append("Переменные затраты равны цене: валовая маржа нулевая, привлечение не окупится.")

    ltv_cac = ltv / cac if cac is not None else None
    payback_months = payback_purchases = payback_status = None
    if cac is not None and margin > 0:
        if subscription:
            payback_months = cac / margin
        else:
            payback_purchases = cac / margin
    if subscription and cac is not None:
        if payback_months is None or round(payback_months, 9) > round(lifetime, 9):
            payback_status = "never"
            if payback_months is not None:
                warnings.append(
                    f"Окупаемость дольше жизни клиента: CAC возвращается за {fmt_num(payback_months, 1)} мес., "
                    f"клиент живёт {fmt_num(lifetime, 1)} мес.")
        else:
            payback_status = _band(payback_months, PAYBACK_BANDS, PAYBACK_TOP)

    return {
        "model": e["model"],
        "price": price,
        "gross_margin": gross_margin,
        "margin": margin,
        "repeat_purchases": e["repeat_purchases"],
        "churn_month": churn,
        "lifetime_months": lifetime,
        "annual_churn": annual_churn,
        "ltv": ltv,
        "ltv_revenue": ltv_revenue,
        "new_paid": new_paid,
        "cac": cac,
        "ltv_cac": ltv_cac,
        "payback_months": payback_months,
        "payback_purchases": payback_purchases,
        "ltv_cac_status": _band(ltv_cac, LTV_CAC_BANDS, LTV_CAC_TOP) if ltv_cac is not None else None,
        "churn_status": churn_status,
        "payback_status": payback_status,
        "warnings": warnings,
    }


def forecast(e: dict, factors: dict | None = None) -> dict:
    """Помесячный прогноз выручки, затрат и денег; итоги: потолок, выход в ноль, запас денег."""
    e = _norm_econ(e)
    k = _factors(factors)
    subscription = e["model"] == "subscription"
    price = e["price"] * k["price"]
    churn = min(1.0, e["churn_month"] * k["churn"]) if subscription else None
    customers = e["start_customers"]
    cash_known = e["cash_now"] is not None
    cash = e["cash_now"] if cash_known else 0
    cumulative = lowest = 0
    months = []
    for m in range(1, e["horizon_months"] + 1):
        traffic = e["traffic_month"] * (1 + e["traffic_growth_month"]) ** (m - 1)
        new = traffic * e["cr_visit_signup"] * e["cr_signup_paid"] * k["acq"]
        if subscription:
            customers = customers * (1 - churn) + new
            mrr = customers * price
            revenue = mrr
        else:
            customers = customers + new
            mrr = None
            revenue = new * price * (1 + e["repeat_purchases"])
        costs = revenue * e["variable_cost_pct"] + e["marketing_month"] + e["fixed_costs_month"]
        profit = revenue - costs
        cash = cash + profit
        cumulative += profit
        lowest = min(lowest, cumulative)
        months.append({
            "m": m, "traffic": traffic, "new": new, "customers": customers, "mrr": mrr,
            "revenue": revenue, "costs": costs, "profit": profit, "cash": cash,
        })

    steady = None
    if subscription and e["traffic_growth_month"] == 0 and churn > 0:
        steady = months[0]["new"] / churn
    target = e["goal_mrr"]
    metric = "mrr" if subscription else "revenue"
    goal_month = None
    if target is not None:
        goal_month = _first_month(months, lambda row: _ge(row[metric], target))
    breakeven_month = _first_month(months, lambda row: _ge(row["profit"], 0))
    cash_needed = max(0.0, -lowest)
    if cash_known:
        cash_out_month = _first_month(months, lambda row: not _ge(row["cash"], 0))
        default_alive = cash_out_month is None or (breakeven_month is not None and breakeven_month < cash_out_month)
    else:
        cash_out_month = None
        default_alive = True if _ge(0, cash_needed) else None

    def at(month, key):
        return months[month - 1][key] if len(months) >= month else None

    return {
        "months": months,
        "steady_customers": steady,
        "goal_month": goal_month,
        "breakeven_month": breakeven_month,
        "cash_known": cash_known,
        "cash_needed": cash_needed,
        "cash_out_month": cash_out_month,
        "default_alive": default_alive,
        "mrr_m6": at(6, "mrr"),
        "mrr_m12": at(12, "mrr"),
        "mrr_m36": at(36, "mrr"),
        "customers_m12": at(12, "customers"),
    }


def scenarios(e: dict) -> dict:
    """Три сценария: базовый, пессимистичный, оптимистичный; множители входов и LTV/CAC в каждом."""
    e = _norm_econ(e)
    result = {}
    for key, _label, k in SCENARIOS:
        unit = unit_economics(e, k)
        result[key] = {**forecast(e, k), "factors": dict(k), "cac": unit["cac"], "ltv_cac": unit["ltv_cac"]}
    return result


def sensitivity(e: dict) -> list:
    """Чувствительность: каждый параметр ±20 %; LTV/CAC и MRR на 12-м месяце, по убыванию размаха."""
    e = _norm_econ(e)
    found = []
    for index, param in enumerate(SENSITIVITY_PARAMS):
        value = e.get(param)
        if value is None:
            continue
        runs = []
        for sign in (-1, 1):
            changed = value * (1 + sign * SENSITIVITY_STEP)
            if param in RATE_PARAMS:
                changed = min(changed, 1.0)
            variant = {**e, param: changed, "horizon_months": 12}
            runs.append((changed, unit_economics(variant)["ltv_cac"], forecast(variant)["mrr_m12"]))
        (low_value, low_ltv_cac, low_mrr), (high_value, high_ltv_cac, high_mrr) = runs
        found.append((index, {
            "param": param,
            "value": value,
            "low_value": low_value,
            "high_value": high_value,
            "ltv_cac_low": low_ltv_cac,
            "ltv_cac_high": high_ltv_cac,
            "mrr_m12_low": low_mrr,
            "mrr_m12_high": high_mrr,
            "swing": _swing(low_ltv_cac, high_ltv_cac),
            "mrr_swing": _swing(low_mrr, high_mrr),
        }))
    found.sort(key=lambda pair: (-(pair[1]["swing"] or 0.0), pair[0]))
    return [row for _, row in found]


def _swing(low, high):
    return None if low is None or high is None else round(abs(high - low), 9)


def goal(e: dict, base_forecast: dict | None) -> dict:
    """Расчёт от цели: сколько клиентов, притока, трафика и бюджета нужно; достижима ли цель."""
    e = _norm_econ(e)
    target = e["goal_mrr"]
    if target is None:
        return {}
    base = base_forecast if base_forecast is not None else forecast(e)
    subscription = e["model"] == "subscription"
    if subscription:
        customers_needed = target / e["price"]
        new_needed = customers_needed * e["churn_month"]
    else:
        customers_needed = None
        new_needed = target / (e["price"] * (1 + e["repeat_purchases"]))
    conversion = e["cr_visit_signup"] * e["cr_signup_paid"]
    cac = unit_economics(e)["cac"]
    steady = base["steady_customers"]
    ceiling = steady * e["price"] if steady is not None else None
    metric = "mrr" if subscription else "revenue"
    goal_month = _first_month(base["months"], lambda row: _ge(row[metric], target))
    reachable = _ge(ceiling, target) if ceiling is not None else goal_month is not None
    return {
        "goal_mrr": target,
        "customers_needed": customers_needed,
        "new_per_month_needed": new_needed,
        "traffic_needed": new_needed / conversion if conversion > 0 else None,
        "marketing_needed": new_needed * cac if cac is not None else None,
        "goal_month_base": goal_month,
        "mrr_ceiling": ceiling,
        "reachable": reachable,
    }


def market(m: dict, base_forecast: dict | None = None) -> dict:
    """Рынок снизу вверх (TAM, SAM, SOM), выручка по годам и сверка со взглядом сверху вниз."""
    m = _as_dict(m, "market")
    p = "market"
    tam_count = _number(m, "tam_count", p, lo=0)
    sam_count = _number(m, "sam_count", p, lo=0)
    price_year = _number(m, "price_year", p, gt=0)
    tam_price = _number(m, "tam_price_year", p, gt=0)
    sam_price = _number(m, "sam_price_year", p, gt=0)
    share_year3 = _number(m, "som_share_year3", p, default=SOM_SHARE_DEFAULT, lo=0, hi=1)
    tam_price = price_year if tam_price is None else tam_price
    sam_price = price_year if sam_price is None else sam_price
    for count, price, name in ((tam_count, tam_price, "tam"), (sam_count, sam_price, "sam")):
        if count is not None and price is None:
            raise InputError(f"market.price_year: нужен чек в год (price_year или {name}_price_year)")
    tam = tam_count * tam_price if tam_count is not None else None
    sam = sam_count * sam_price if sam_count is not None else None
    som_share = sam * share_year3 if sam is not None else None

    months = base_forecast["months"] if base_forecast else []
    som_capacity = sum(row["revenue"] for row in months[24:36]) if len(months) >= 36 else None
    revenue_by_year = []
    for year in (1, 2, 3):
        if len(months) >= 12 * year:
            revenue = sum(row["revenue"] for row in months[12 * (year - 1):12 * year])
            revenue_by_year.append({
                "year": year,
                "revenue": revenue,
                "share_of_sam": revenue / sam if sam else None,
            })
    if som_capacity is not None and (som_share is None or som_capacity < som_share):
        som, basis = som_capacity, "capacity"
    elif som_share is not None:
        som, basis = som_share, "share"
    else:
        som, basis = None, None

    result = {
        "unit": _text(m, "unit", p),
        "tam_count": tam_count,
        "tam_price_year": tam_price if tam_count is not None else None,
        "tam": tam,
        "sam_count": sam_count,
        "sam_price_year": sam_price if sam_count is not None else None,
        "sam": sam,
        "som_share_year3": share_year3,
        "som_share": som_share,
        "som_capacity": som_capacity,
        "som": som,
        "som_basis": basis,
        "revenue_by_year": revenue_by_year,
    }
    top_down = m.get("top_down")
    if top_down is not None:
        top_down = _as_dict(top_down, "market.top_down")
        check = {"source": _text(top_down, "source", "market.top_down")}
        for name, bottom_up in (("tam", tam), ("sam", sam)):
            reference = _number(top_down, name, "market.top_down", lo=0)
            if bottom_up is not None and reference is not None:
                check[name] = _gap(bottom_up, reference)
        result["top_down"] = check
    return result


def _gap(bottom_up, top_down):
    mean = (bottom_up + top_down) / 2
    gap = abs(bottom_up - top_down) / mean if mean > 0 else 0.0
    if round(gap, 9) <= GAP_OK_UPTO:
        status = "ok"
    elif round(gap, 9) <= GAP_WARN_UPTO:
        status = "warn"
    else:
        status = "bad"
    return {"bottom_up": bottom_up, "top_down": top_down, "gap": gap, "status": status}


def _goal_warning(g, e, currency):
    target = fmt_money(g["goal_mrr"], currency)
    if g["mrr_ceiling"] is not None:
        return (f"Цель недостижима: потолок MRR {fmt_money(g['mrr_ceiling'], currency)} "
                f"при текущих притоке и оттоке, а нужно {target}.")
    return f"Цель недостижима за {e['horizon_months']} мес. прогноза: {target} в месяц не достигается."


def _market_warnings(mk):
    found = []
    for name, label in (("tam", "TAM"), ("sam", "SAM")):
        item = (mk.get("top_down") or {}).get(name)
        if item and item["status"] == "bad":
            found.append(f"Оценки {label} снизу вверх и сверху вниз расходятся на {fmt_pct(item['gap'])}: "
                         f"больше {fmt_pct(GAP_WARN_UPTO)} — рынок определён неверно.")
    return found


def _economics_block(e, currency):
    unit = unit_economics(e)
    warnings = list(unit.pop("warnings"))
    scen = scenarios(e)
    visits = e["traffic_month"]
    signups = visits * e["cr_visit_signup"]
    parts = {
        "unit_economics": unit,
        "funnel": {
            "visits": visits,
            "signups": signups,
            "paid": signups * e["cr_signup_paid"],
            "cr_visit_signup": e["cr_visit_signup"],
            "cr_signup_paid": e["cr_signup_paid"],
        },
        "forecast": scen,
        "sensitivity": sensitivity(e),
    }
    goal_result = goal(e, scen["base"]) if e["goal_mrr"] is not None else None
    if goal_result is not None:
        parts["goal"] = goal_result
        if goal_result["reachable"] is False:
            warnings.append(_goal_warning(goal_result, e, currency))
    return parts, warnings, scen["base"]


def compute(data: dict) -> dict:
    """Весь calc.json: считает те разделы, что есть во входе."""
    if not isinstance(data, dict):
        raise InputError("Корень JSON должен быть объектом")
    result = {}
    warnings = []
    project = _project(data["project"]) if data.get("project") is not None else None
    currency = project["currency"] if project else "RUB"
    if project:
        result["project"] = project
    if data.get("scorecard") is not None:
        result["scorecard"] = scorecard(data["scorecard"])
    if data.get("segments") is not None:
        result["segments"] = segments(data["segments"])
    economics_parts, base = {}, None
    if data.get("economics") is not None:
        economics_parts, economics_warnings, base = _economics_block(_norm_econ(data["economics"]), currency)
        warnings.extend(economics_warnings)
    if data.get("market") is not None:
        market_result = market(data["market"], base)
        result["market"] = market_result
        warnings.extend(_market_warnings(market_result))
    result.update(economics_parts)
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


def _cell(value):
    if value is None:
        return DASH
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _table(headers, rows):
    lines = [
        "| " + " | ".join(_cell(h) for h in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return "\n".join(lines)


def _score_text(x):
    return fmt_num(x, 0 if float(x).is_integer() else 1)


def _mult(x):
    return "×" + fmt_num(x, 2).rstrip("0").rstrip(",")


def _month_text(month, none_text):
    return none_text if month is None else fmt_num(month, 0)


def _md_scorecard(sc):
    rows = []
    for key, title, _weight in CRITERIA:
        item = sc["criteria"][key]
        score = _score_text(item["score"])
        if item["capped"]:
            score += f" (указано {_score_text(item['raw_score'])}, ограничено допущением)"
        rows.append([title, fmt_pct(item["weight"]), score, EVIDENCE_LABELS[item["evidence"]], item.get("note") or ""])
    verdict = f"- **Вердикт:** {VERDICT_LABELS[sc['verdict']]}"
    if sc["verdict"] != sc["verdict_raw"]:
        verdict += f" (по сумме баллов — «{VERDICT_LABELS[sc['verdict_raw']]}»)"
    lines = [
        "### Оценка идеи",
        "",
        _table(["Критерий", "Вес", "Балл", "Доказательство", "Комментарий"], rows),
        "",
        f"- **Итог:** {fmt_num(sc['total'], 1)} из 10",
        verdict,
    ]
    if sc["gates"]:
        reasons = "; ".join(GATE_LABELS[g] for g in sc["gates"])
        lines.append(f"- **Ограничители:** {reasons}. Вердикт не выше «{VERDICT_LABELS['check']}».")
    lines.append(f"- **Надёжность:** {CONFIDENCE_LABELS[sc['confidence_label']]} ({fmt_pct(sc['confidence'])})")
    return "\n".join(lines)


def _md_segments(rows):
    table = []
    for row in rows:
        size = DASH
        if row.get("size") is not None:
            size = fmt_num(row["size"], 0) + (" " + row["size_unit"] if row.get("size_unit") else "")
        if row["pick"]:
            verdict = "Начинать с него" if row["meets_threshold"] else f"Лучший из списка, но итог не выше {SEGMENT_PICK_ABOVE}"
        else:
            verdict = f"Выше {SEGMENT_PICK_ABOVE}" if row["meets_threshold"] else DASH
        table.append([row["rank"], row["name"], size, _score_text(row["pain"]), _score_text(row["wtp"]),
                      _score_text(row["ease"]), fmt_num(row["total"], 1), verdict])
    headers = ["№", "Сегмент", "Размер", "Боль", "Готовность платить", "Лёгкость продажи", "Итог", "Вывод"]
    return "\n".join(["### Сегменты", "", _table(headers, table)])


def _md_market(mk, currency):
    unit = mk.get("unit") or "клиентов"
    rows = []
    if mk.get("tam") is not None:
        how = f"{fmt_num(mk['tam_count'], 0)} {unit} × {fmt_money(mk['tam_price_year'], currency)} в год"
        rows.append(["TAM — весь рынок", fmt_money(mk["tam"], currency) + " в год", how])
    if mk.get("sam") is not None:
        how = f"{fmt_num(mk['sam_count'], 0)} {unit} × {fmt_money(mk['sam_price_year'], currency)} в год"
        rows.append(["SAM — доступный рынок", fmt_money(mk["sam"], currency) + " в год", how])
    if mk.get("som_share") is not None:
        rows.append(["SOM по доле рынка", fmt_money(mk["som_share"], currency) + " в год",
                     f"{_pct_auto(mk['som_share_year3'])} SAM за 3 года"])
    if mk.get("som_capacity") is not None:
        rows.append(["SOM по мощности воронки", fmt_money(mk["som_capacity"], currency) + " в год",
                     "выручка базового прогноза за месяцы 25–36"])
    if mk.get("som") is not None:
        basis = {"share": "доля рынка", "capacity": "мощность воронки"}[mk["som_basis"]]
        rows.append(["SOM — итог", fmt_money(mk["som"], currency) + " в год", f"меньшее из двух; сработало: {basis}"])
    blocks = ["### Рынок", ""]
    if rows:
        blocks.append(_table(["Показатель", "Значение", "Как посчитано"], rows))
    if mk.get("revenue_by_year"):
        years = [[f"{y['year']}-й", fmt_money(y["revenue"], currency),
                  DASH if y["share_of_sam"] is None else _pct_auto(y["share_of_sam"])]
                 for y in mk["revenue_by_year"]]
        blocks += ["", _table(["Год", "Выручка по базовому прогнозу", "Доля SAM"], years)]
    top_down = mk.get("top_down")
    if top_down:
        checks = [[name.upper(), fmt_money(top_down[name]["bottom_up"], currency),
                   fmt_money(top_down[name]["top_down"], currency), fmt_pct(top_down[name]["gap"]),
                   GAP_LABELS[top_down[name]["status"]]]
                  for name in ("tam", "sam") if name in top_down]
        if checks:
            blocks += ["", _table(["Показатель", "Снизу вверх", "Сверху вниз", "Расхождение", "Оценка"], checks)]
        if top_down.get("source"):
            blocks += ["", f"Источник данных сверху вниз: {top_down['source']}"]
    return "\n".join(blocks)


def _md_unit_economics(ue, funnel, currency):
    subscription = ue["model"] == "subscription"
    rows = [
        ["Цена", fmt_money(ue["price"], currency) + (" в месяц" if subscription else " за покупку"), ""],
        ["Валовая маржа", fmt_pct(ue["gross_margin"]), ""],
        ["Маржа с " + ("клиента в месяц" if subscription else "покупки"), fmt_money(ue["margin"], currency), ""],
    ]
    if subscription:
        rows.append(["Срок жизни клиента", fmt_num(ue["lifetime_months"], 1) + " мес.", ""])
    else:
        rows.append(["Повторных покупок на клиента", fmt_num(ue["repeat_purchases"], 1), ""])
    rows.append(["LTV (от маржи)", fmt_money(ue["ltv"], currency), ""])
    rows.append(["CAC", fmt_money(ue["cac"], currency), ""])
    if ue["ltv_cac"] is not None:
        label, action = LTV_CAC_LABELS[ue["ltv_cac_status"]]
        rows.append(["LTV / CAC", fmt_num(ue["ltv_cac"], 2), f"{label}. {action}"])
    else:
        rows.append(["LTV / CAC", DASH, ""])
    if subscription:
        payback = fmt_num(ue["payback_months"], 1) + " мес." if ue["payback_months"] is not None else DASH
        rows.append(["Окупаемость CAC", payback, PAYBACK_LABELS.get(ue["payback_status"], "")])
        rows.append(["Отток в месяц", _pct_auto(ue["churn_month"]), CHURN_LABELS[ue["churn_status"]]])
        rows.append(["Отток в год", fmt_pct(ue["annual_churn"]), ""])
    elif ue["payback_purchases"] is not None:
        rows.append(["Покупок до окупаемости CAC", fmt_num(ue["payback_purchases"], 2), ""])
    rows.append(["Новых платящих в первый месяц" if subscription else "Новых покупателей в первый месяц",
                 fmt_num(ue["new_paid"], 1), ""])
    funnel_rows = [
        ["Посетители", fmt_num(funnel["visits"], 0), ""],
        ["Регистрации", fmt_num(funnel["signups"], 0 if funnel["signups"] >= 100 else 1), _pct_auto(funnel["cr_visit_signup"])],
        ["Платящие", fmt_num(funnel["paid"], 1), _pct_auto(funnel["cr_signup_paid"])],
    ]
    return "\n".join([
        "### Юнит-экономика", "",
        _table(["Показатель", "Значение", "Оценка"], rows), "",
        _table(["Воронка в месяц", "Число", "Конверсия"], funnel_rows),
    ])


def _month_row(row, subscription, currency):
    return [row["m"], fmt_num(row["traffic"], 0), fmt_num(row["new"], 1), fmt_num(row["customers"], 1),
            fmt_money(row["mrr"] if subscription else row["revenue"], currency),
            fmt_money(row["costs"], currency), fmt_money(row["profit"], currency), fmt_money(row["cash"], currency)]


def _md_forecast(fc, ue, currency, has_goal):
    subscription = ue["model"] == "subscription"
    metric = "MRR" if subscription else "Выручка в месяц"
    order = ("pessimistic", "base", "optimistic")
    labels = {key: label for key, label, _ in SCENARIOS}

    def revenue_at(scenario, month):
        months = fc[scenario]["months"]
        if len(months) < month:
            return DASH
        return fmt_money(months[month - 1]["mrr" if subscription else "revenue"], currency)

    rows = [["Привлечение"] + [_mult(fc[s]["factors"]["acq"]) for s in order]]
    if subscription:
        rows.append(["Отток"] + [_mult(fc[s]["factors"]["churn"]) for s in order])
    rows.append(["Цена"] + [_mult(fc[s]["factors"]["price"]) for s in order])
    for month in (6, 12, 36):
        rows.append([f"{metric} через {month} мес."] + [revenue_at(s, month) for s in order])
    rows.append(["Клиентов через 12 мес." if subscription else "Покупателей через 12 мес."]
                + [fmt_num(fc[s]["customers_m12"], 1) for s in order])
    if subscription:
        rows.append(["Потолок числа клиентов"] + [fmt_num(fc[s]["steady_customers"], 1) for s in order])
    rows.append(["LTV / CAC"] + [fmt_num(fc[s]["ltv_cac"], 2) for s in order])
    rows.append(["Выход в ноль, месяц"] + [_month_text(fc[s]["breakeven_month"], "не наступает") for s in order])
    def cash_needed_text(scenario):
        money = fmt_money(fc[scenario]["cash_needed"], currency)
        return money if fc[scenario]["breakeven_month"] is not None else f"больше {money} (выхода в ноль нет)"

    rows.append(["Нужно денег до выхода в ноль"] + [cash_needed_text(s) for s in order])
    if fc["base"]["cash_known"]:
        rows.append(["Деньги кончатся, месяц"] + [_month_text(fc[s]["cash_out_month"], "не кончатся") for s in order])
        rows.append(["Хватит денег до выхода в ноль"] + [("да" if fc[s]["default_alive"] else "нет") for s in order])
    if has_goal:
        rows.append(["Цель достигнута, месяц"] + [_month_text(fc[s]["goal_month"], "не достигается") for s in order])

    base_months = fc["base"]["months"]
    wanted = sorted({1, 3, 6, 9, 12, 18, 24, 36, len(base_months)} & set(range(1, len(base_months) + 1)))
    monthly = [_month_row(base_months[m - 1], subscription, currency) for m in wanted]
    headers = ["Месяц", "Трафик", "Новые платящие" if subscription else "Новые покупатели",
               "Клиентов" if subscription else "Покупателей всего", metric, "Затраты", "Прибыль",
               "Деньги" if fc["base"]["cash_known"] else "Прибыль нарастающим итогом"]
    return "\n".join([
        "### Прогноз выручки", "",
        _table(["Показатель"] + [labels[s] for s in order], rows), "",
        "Базовый сценарий по месяцам:", "",
        _table(headers, monthly),
    ])


def _param_text(param, value, currency):
    if param in ("price", "marketing_month"):
        return fmt_money(value, currency)
    if param == "traffic_month":
        return fmt_num(value, 0)
    return _pct_auto(value)


def _md_sensitivity(rows, ue, currency):
    metric = "MRR" if ue["model"] == "subscription" else "Выручка"
    table = []
    for row in rows:
        param = row["param"]
        mrr = DASH
        if row["mrr_m12_low"] is not None and row["mrr_m12_high"] is not None:
            mrr = f"{fmt_money(row['mrr_m12_low'], currency)} … {fmt_money(row['mrr_m12_high'], currency)}"
        table.append([
            SENSITIVITY_LABELS[param],
            f"{_param_text(param, row['low_value'], currency)} … {_param_text(param, row['high_value'], currency)}",
            f"{fmt_num(row['ltv_cac_low'], 2)} … {fmt_num(row['ltv_cac_high'], 2)}",
            fmt_num(row["swing"], 2),
            mrr,
        ])
    headers = ["Параметр", "Диапазон (−20 % … +20 %)", "LTV / CAC", "Размах LTV / CAC", f"{metric} на 12-м мес."]
    return "\n".join(["### Чувствительность", "", _table(headers, table)])


def _md_goal(g, ue, currency):
    subscription = ue["model"] == "subscription"
    rows = [["Цель, выручка в месяц", fmt_money(g["goal_mrr"], currency)]]
    if g.get("customers_needed") is not None:
        rows.append(["Нужно клиентов", fmt_num(g["customers_needed"], 0)])
    rows.append(["Нужно новых платящих в месяц", fmt_num(g["new_per_month_needed"], 1)])
    rows.append(["Нужно трафика в месяц", fmt_num(g["traffic_needed"], 0)])
    rows.append(["Нужен бюджет на привлечение в месяц", fmt_money(g["marketing_needed"], currency)])
    if subscription:
        rows.append(["Потолок MRR при текущих притоке и оттоке", fmt_money(g["mrr_ceiling"], currency)])
    rows.append(["Достигается в базовом сценарии, месяц", _month_text(g["goal_month_base"], "не достигается")])
    rows.append(["Достижима", "да" if g["reachable"] else "нет"])
    return "\n".join(["### Цель", "", _table(["Показатель", "Значение"], rows)])


def render_md(result: dict, currency: str = "RUB") -> str:
    """Таблицы Markdown на русском по результату compute()."""
    blocks = []
    if "scorecard" in result:
        blocks.append(_md_scorecard(result["scorecard"]))
    if result.get("segments"):
        blocks.append(_md_segments(result["segments"]))
    if "market" in result:
        blocks.append(_md_market(result["market"], currency))
    unit = result.get("unit_economics")
    if unit:
        blocks.append(_md_unit_economics(unit, result["funnel"], currency))
        blocks.append(_md_forecast(result["forecast"], unit, currency, "goal" in result))
        blocks.append(_md_sensitivity(result["sensitivity"], unit, currency))
        if "goal" in result:
            blocks.append(_md_goal(result["goal"], unit, currency))
    if result.get("warnings"):
        blocks.append("\n".join(["### Предупреждения", ""] + [f"- {w}" for w in result["warnings"]]))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _plain(obj):
    if isinstance(obj, float):
        return round(obj, 6) + 0.0 if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {key: _plain(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(value) for value in obj]
    return obj


def _write(path, text):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv: list[str] | None = None) -> int:
    """CLI: data.json → calc.json (и таблицы Markdown); 2 — ошибка во входе."""
    parser = argparse.ArgumentParser(
        prog="calc.py", description="Расчёты скилла startup-launch-audit: data.json → calc.json и таблицы Markdown.")
    parser.add_argument("data", metavar="DATA_JSON", help="входной файл data.json")
    parser.add_argument("--out", metavar="CALC_JSON", help="куда записать результат (по умолчанию calc.json рядом с входом)")
    parser.add_argument("--md", metavar="TABLES_MD", help="куда записать таблицы Markdown")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    try:
        with open(args.data, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except OSError as exc:
        print(f"{args.data}: не удалось прочитать файл ({exc.strerror or exc})", file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        print(f"{args.data}: файл должен быть в кодировке UTF-8", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{args.data}: некорректный JSON (строка {exc.lineno}, столбец {exc.colno}): {exc.msg}", file=sys.stderr)
        return 2

    try:
        result = _plain(compute(data))
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.data)), "calc.json")
    currency = result.get("project", {}).get("currency", "RUB")
    try:
        _write(out_path, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(f"Записано: {out_path}")
        if args.md:
            _write(args.md, render_md(result, currency))
            print(f"Записано: {args.md}")
    except OSError as exc:
        print(f"Не удалось записать файл: {exc.strerror or exc}", file=sys.stderr)
        return 1
    for warning in result["warnings"]:
        print(f"Предупреждение: {warning}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
