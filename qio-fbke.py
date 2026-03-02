# FIRSTNAME_LASTNAME.py
import math
from decimal import Decimal, ROUND_HALF_UP


def validate_and_fix_prices(prices: dict[str, float]) -> dict:
    """
    Validates and fixes motor insurance pricing rules.
    Args:
    prices: dict with keys like "mtpl", "limited_casco_100", "casco_500"
    Returns:
    {
    "fixed_prices": dict[str, float],
    "issues": list[str]
    }
    """
    fixed = prices.copy()
    issues = []

    epsilon = 0.01
    target_200_100 = 0.85
    target_500_100 = 0.80
    target_500_200 = target_500_100 / target_200_100

    def round_money(value: float) -> float:
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    expected_keys = (
        "mtpl", "limited_casco_100", "limited_casco_200", "limited_casco_500",
        "casco_100", "casco_200", "casco_500",
    )
    for key in expected_keys:
        if key not in fixed:
            issues.append(f"Missing key {key}; checks for this field were skipped.")
            continue
        try:
            numeric_value = float(fixed[key])
        except (TypeError, ValueError):
            issues.append(
                f"Invalid value for {key}; replaced with minimum allowed positive value ({epsilon:.2f})."
            )
            fixed[key] = epsilon
            continue
        if numeric_value <= 0:
            issues.append(
                f"Non-positive value for {key}; replaced with minimum allowed positive value ({epsilon:.2f})."
            )
            fixed[key] = epsilon
        else:
            fixed[key] = round_money(numeric_value)

    def set_price(key: str, new_value: float, reason: str) -> None:
        old_value = fixed[key]
        rounded_new_value = round_money(float(new_value))
        if rounded_new_value != round_money(float(old_value)):
            fixed[key] = rounded_new_value
            issues.append(
                f"Adjusted {key} from {old_value:.2f} to {rounded_new_value:.2f}: {reason}"
            )

    def relative_change(old_value: float, new_value: float) -> float:
        denominator = max(abs(old_value), epsilon)
        return abs(new_value - old_value) / denominator

    def order_error(p100: float, p200: float, p500: float) -> float:
        if p100 <= 0 or p200 <= 0:
            return 1e9
        return (
            abs((p200 / p100) - target_200_100)
            + abs((p500 / p100) - target_500_100)
            + abs((p500 / p200) - target_500_200)
        )

    def clamp(value: float, lower: float, upper: float) -> float | None:
        if lower > upper:
            return None
        return min(max(value, lower), upper)

    def product_label(prefix: str) -> str:
        return {"limited_casco": "Limited Casco", "casco": "Casco"}.get(prefix, prefix)

    def apply_floors(
        p100: float,
        p200: float,
        p500: float,
        floors: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        f100, f200, f500 = floors
        return (
            round_money(max(p100, f100)),
            round_money(max(p200, f200)),
            round_money(max(p500, f500)),
        )

    def choose_candidate(
        current: tuple[float, float, float],
        candidates: list[tuple[str, tuple[float, float, float] | None]],
    ) -> tuple[str, tuple[float, float, float]] | None:
        p100, p200, p500 = current
        best = None
        best_score = float("inf")

        for reason, values in candidates:
            if values is None:
                continue
            n100, n200, n500 = values
            if not (n100 > n200 > n500):
                continue

            changed_count = int(round_money(n100) != round_money(p100))
            changed_count += int(round_money(n200) != round_money(p200))
            changed_count += int(round_money(n500) != round_money(p500))

            change_cost = (
                relative_change(p100, n100)
                + relative_change(p200, n200)
                + relative_change(p500, n500)
            )
            score = change_cost + 0.2 * order_error(n100, n200, n500) + 0.03 * changed_count

            if score < best_score:
                best_score = score
                best = (reason, values)

        return best

    def fallback_repair(
        p100: float,
        p200: float,
        p500: float,
        floors: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        f100, f200, f500 = (
            floor if math.isfinite(floor) else -1e18 for floor in floors
        )
        p100 = round_money(max(p100, f100))
        p200 = round_money(max(p200, f200))
        p500 = round_money(max(p500, f500))

        for _ in range(6):
            changed = False
            if p200 <= p500:
                lowered_p500 = round_money(p200 - epsilon)
                if lowered_p500 >= round_money(f500):
                    p500 = lowered_p500
                else:
                    p200 = round_money(max(f200, p500 + epsilon))
                changed = True

            if p100 <= p200:
                p100 = round_money(max(f100, p200 + epsilon))
                changed = True

            p100 = round_money(max(p100, f100))
            p200 = round_money(max(p200, f200))
            p500 = round_money(max(p500, f500))

            if not changed:
                break

        return p100, p200, p500

    def fix_deductible_group(prefix: str, floors: tuple[float, float, float]) -> None:
        k100 = f"{prefix}_100"
        k200 = f"{prefix}_200"
        k500 = f"{prefix}_500"
        label = product_label(prefix)

        if k100 not in fixed or k200 not in fixed or k500 not in fixed:
            return

        p100, p200, p500 = fixed[k100], fixed[k200], fixed[k500]
        start_values = (p100, p200, p500)

        guide_100 = (p200 / target_200_100 + p500 / target_500_100) / 2
        guide_200 = p100 * target_200_100
        guide_500 = p100 * target_500_100

        new_values = start_values
        reason = ""

        if not (p100 > p200 > p500):
            c200_down = clamp(min(p100 - epsilon, p500 / target_500_200), p500 + epsilon, p100 - epsilon)
            c200_up = clamp(p500 / target_500_200, p500 + epsilon, p100 - epsilon)
            c500_guide = clamp(guide_500, -float("inf"), p200 - epsilon)
            c500_down = clamp(p200 - epsilon, -float("inf"), p200 - epsilon)

            candidates = [
                (
                    f"{label}: swapped 100€ and 500€ deductible prices to restore correct deductible order (100€ > 200€ > 500€).",
                    (p500, p200, p100),
                ),
                (
                    f"{label}: increased 100€ deductible price to keep it above 200€ and preserve deductible order.",
                    (max(guide_100, p200 + epsilon), p200, p500),
                ),
                (
                    f"{label}: decreased 500€ deductible price to keep it below 200€ and preserve deductible order.",
                    None if c500_guide is None else (p100, p200, c500_guide),
                ),
                (
                    f"{label}: decreased 200€ deductible price to restore deductible order with minimal proportional change.",
                    None if c200_down is None else (p100, c200_down, p500),
                ),
                (
                    f"{label}: increased 200€ deductible price to restore deductible order with minimal proportional change.",
                    None if c200_up is None else (p100, c200_up, p500),
                ),
                (
                    f"{label}: adjusted 200€ and 500€ deductible prices to better match expected deductible relationships.",
                    (p100, guide_200, guide_500),
                ),
                (
                    f"{label}: set 500€ deductible price just below 200€ to restore strict order.",
                    None if c500_down is None else (p100, p200, c500_down),
                ),
            ]

            selected = choose_candidate((p100, p200, p500), candidates)
            if selected is not None:
                reason, new_values = selected

        if not (new_values[0] > new_values[1] > new_values[2]):
            new_values = fallback_repair(p100, p200, p500, (-float("inf"), -float("inf"), -float("inf")))
            reason = (
                f"{label}: applied fallback correction to enforce strict deductible order (100€ > 200€ > 500€)."
            )

        case_fixed_values = new_values

        if reason:
            set_price(k100, case_fixed_values[0], reason)
            set_price(k200, case_fixed_values[1], reason)
            set_price(k500, case_fixed_values[2], reason)

        floored_values = apply_floors(*case_fixed_values, floors)
        if floored_values[0] != case_fixed_values[0]:
            set_price(
                k100,
                floored_values[0],
                f"{label}: increased 100€ deductible price to stay above minimum hierarchy requirement.",
            )
        if floored_values[1] != case_fixed_values[1]:
            set_price(
                k200,
                floored_values[1],
                f"{label}: increased 200€ deductible price to stay above minimum hierarchy requirement.",
            )
        if floored_values[2] != case_fixed_values[2]:
            set_price(
                k500,
                floored_values[2],
                f"{label}: increased 500€ deductible price to stay above minimum hierarchy requirement.",
            )

        if not (floored_values[0] > floored_values[1] > floored_values[2]):
            post_floor_values = fallback_repair(*floored_values, floors)
            post_floor_reason = (
                f"{label}: rebalanced deductible prices after applying hierarchy minimum requirements."
            )
            set_price(k100, post_floor_values[0], post_floor_reason)
            set_price(k200, post_floor_values[1], post_floor_reason)
            set_price(k500, post_floor_values[2], post_floor_reason)

    mtpl = fixed.get("mtpl")
    if mtpl is not None:
        limited_floors = (mtpl + epsilon, mtpl + epsilon, mtpl + epsilon)
        fix_deductible_group("limited_casco", limited_floors)

        limited_100 = fixed.get("limited_casco_100")
        limited_200 = fixed.get("limited_casco_200")
        limited_500 = fixed.get("limited_casco_500")
        if limited_100 is not None and limited_200 is not None and limited_500 is not None:
            casco_floors = (
                limited_100 + epsilon,
                limited_200 + epsilon,
                limited_500 + epsilon,
            )
            fix_deductible_group("casco", casco_floors)

    return {"fixed_prices": fixed, "issues": issues}

# --- Local testing only ---
example_prices = {
    "mtpl": 400,
    "limited_casco_100": 850,
    "limited_casco_200": 900,
    "limited_casco_500": 700,
    "casco_100": 780,
    "casco_200": 950,
    "casco_500": 830,
}

if __name__ == "__main__":
    result = validate_and_fix_prices(example_prices)
    print("Fixed prices:", result["fixed_prices"])
    print("Issues found:")
    for issue in result["issues"]:
        print("-", issue)