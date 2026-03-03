# FIRSTNAME_LASTNAME.py
import itertools
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
    issues: list[str] = []

    # -------------------------------------------------------------------------
    # Solution summary (design + strategy)
    #
    # 1) Problem as a directed graph of strict inequalities
    #    We model each price as a node and each business rule "A < B" as a
    #    directed edge A -> B. If an edge is violated, at least one endpoint
    #    must be adjusted. This gives us a clear, uniform way to evaluate all
    #    constraints (Product hierarchy, deductible order).
    #
    # 2) Robust input layer before optimization
    #    Values are sanitized first (non-numeric, NaN/inf, and <= 0 are ignored
    #    and removed from active optimization). This prevents invalid values from
    #    polluting the solver and keeps output finite and stable.
    #
    # 3) Candidate selection with minimal-change principle
    #    We search subsets of nodes by subset size (0, 1, 2, ...). The first
    #    subset size with at least one feasible solution is selected. This
    #    enforces the primary objective: change as few prices as possible.
    #
    # 4) Feasibility via bound propagation (graph-consistent intervals)
    #    For a chosen subset of changed nodes, fixed nodes keep their baseline
    #    value, while changed nodes are allowed to move. We propagate lower and
    #    upper bounds along edges using epsilon strictness (0.01) until bounds
    #    stabilize. If any node gets lower > upper, that subset is infeasible.
    #
    # 5) Pricing heuristics inside feasible space
    #    - MTPL strategy: if MTPL is selected for change, first target market
    #      anchor (REAL_MARKET_MTPL_AVERAGE_PRICE), then let constraints lower it
    #      further only if needed.
    #    - Deductible strategy (100/200/500): prefer relation-driven targets
    #      using the agreed formulas (including blended formulas for 200/500),
    #      with "standard fix first" when 100-level is adjusted.
    #
    # 6) Optimization priorities among feasible candidates (lexicographic)
    #    After minimal number of changed nodes is satisfied, we rank solutions by:
    #      a) preference to modify nodes farther from desired group ratios,
    #      b) ratio penalty (how close solution stays to target 0.85/0.8 links),
    #      c) total relative change from baseline,
    #      d) max single-node movement as final tie-break.
    #
    # 7) Rounding and strict inequality repair
    #    Values are rounded to 2 decimals (money format). If rounding introduces
    #    a strict-inequality conflict, we apply epsilon repair only on nodes that
    #    are allowed to change, choosing the lower-penalty option.
    #
    # 8) Explainability
    #    Each adjustment in issues includes what relation was repaired and which
    #    rule/formula was applied, so the decision path is auditable.
    # -------------------------------------------------------------------------
    
    REAL_MARKET_MTPL_AVERAGE_PRICE = 500

    epsilon = 0.01

    def round_money(value: float) -> float:
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    nodes = [
        "mtpl",
        "limited_casco_100",
        "limited_casco_200",
        "limited_casco_500",
        "casco_100",
        "casco_200",
        "casco_500",
    ]

    # Target graph: u -> v means price[u] < price[v]
    edges = [
        ("mtpl", "limited_casco_100"),
        ("mtpl", "limited_casco_200"),
        ("mtpl", "limited_casco_500"),
        ("mtpl", "casco_100"),
        ("mtpl", "casco_200"),
        ("mtpl", "casco_500"),
        ("limited_casco_500", "limited_casco_200"),
        ("limited_casco_500", "casco_500"),
        ("limited_casco_200", "limited_casco_100"),
        ("limited_casco_200", "casco_200"),
        ("limited_casco_100", "casco_100"),
        ("casco_500", "casco_200"),
        ("casco_200", "casco_100"),
    ]

    for key in nodes:
        if key not in fixed:
            continue

        raw_value = fixed[key]
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            fixed.pop(key, None)
            issues.append(f"Ignored invalid {key}: value '{raw_value}' is not numeric.")
            continue

        if not math.isfinite(numeric_value):
            fixed.pop(key, None)
            issues.append(f"Ignored invalid {key}: value '{raw_value}' is not finite.")
            continue

        if numeric_value <= 0:
            fixed.pop(key, None)
            issues.append(f"Ignored invalid {key}: value '{raw_value}' must be greater than 0.")
            continue

        fixed[key] = round_money(numeric_value)

    active_nodes = [node for node in nodes if node in fixed]
    active_edges = [(u, v) for u, v in edges if u in fixed and v in fixed]

    if not active_edges:
        return {"fixed_prices": fixed, "issues": issues}

    incoming = {node: [] for node in active_nodes}
    outgoing = {node: [] for node in active_nodes}
    indegree = {node: 0 for node in active_nodes}
    for left, right in active_edges:
        outgoing[left].append(right)
        incoming[right].append(left)
        indegree[right] += 1

    queue = [node for node in active_nodes if indegree[node] == 0]
    topo_order = []
    while queue:
        node = queue.pop(0)
        topo_order.append(node)
        for nxt in outgoing[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    baseline = {node: fixed[node] for node in active_nodes}

    ratio_edges = [
        ("limited_casco_500", "limited_casco_200", 0.85),
        ("limited_casco_500", "limited_casco_100", 0.8),
        ("casco_500", "casco_200", 0.85),
        ("casco_500", "casco_100", 0.8),
    ]
    active_ratio_edges = [
        (num, den, target)
        for num, den, target in ratio_edges
        if num in active_nodes and den in active_nodes
    ]

    def ratio_penalty(solution: dict[str, float]) -> float:
        penalty = 0.0
        for num, den, target in active_ratio_edges:
            denominator = max(abs(solution[den]), epsilon)
            actual_ratio = solution[num] / denominator
            penalty += abs(actual_ratio - target) / target
        return penalty

    def changed_node_preference_penalty(changed_nodes: set[str]) -> float:
        penalty = 0.0

        # For nodes inside the same deductible group, prefer changing the node
        # that is farther from the desired relation with the third node (500-level).
        group_specs = [
            ("limited_casco_500", "limited_casco_200", 0.85),
            ("limited_casco_500", "limited_casco_100", 0.8),
            ("casco_500", "casco_200", 0.85),
            ("casco_500", "casco_100", 0.8),
        ]

        for pivot, node, target_ratio in group_specs:
            if pivot not in baseline or node not in baseline:
                continue
            expected_node = baseline[pivot] / target_ratio
            distance = abs(baseline[node] - expected_node) / max(abs(expected_node), epsilon)
            if node in changed_nodes:
                penalty += 1.0 / (distance + 1e-4)

        return penalty

    def preferred_group_target(node: str, values: dict[str, float]) -> float | None:
        if node.startswith("limited_casco_"):
            prefix = "limited_casco"
        elif node.startswith("casco_"):
            prefix = "casco"
        else:
            return None

        n100 = f"{prefix}_100"
        n200 = f"{prefix}_200"
        n500 = f"{prefix}_500"

        estimates = []

        if node == n100:
            if n200 in values:
                estimates.append(values[n200] / 0.85)
            if n500 in values:
                estimates.append(values[n500] / 0.8)
        elif node == n200:
            if n100 in values:
                estimates.append(values[n100] * 0.85)
            if n500 in values:
                estimates.append(values[n500] * 1.0625)
        elif node == n500:
            if n100 in values:
                estimates.append(values[n100] * 0.8)
            if n200 in values:
                estimates.append(values[n200] * (1 / 1.0625))

        if not estimates:
            return None
        return sum(estimates) / len(estimates)

    def apply_standard_fix_first(values: dict[str, float], lower: dict[str, float], upper: dict[str, float], changed_nodes: set[str]) -> None:
        families = ["limited_casco", "casco"]
        for prefix in families:
            n100 = f"{prefix}_100"
            n200 = f"{prefix}_200"
            n500 = f"{prefix}_500"

            if n100 not in values or n200 not in values or n500 not in values:
                continue

            if n100 not in changed_nodes:
                continue

            standard_100 = (values[n200] / 0.85 + values[n500] / 0.8) / 2.0
            values[n100] = min(max(standard_100, lower[n100]), upper[n100])

            standard_200 = (values[n100] * 0.85 + values[n500] * 1.0625) / 2.0
            standard_500 = (values[n100] * 0.8 + values[n500] * (1 / 1.0625)) / 2.0

            if n200 in changed_nodes:
                values[n200] = min(max(standard_200, lower[n200]), upper[n200])
            if n500 in changed_nodes:
                values[n500] = min(max(standard_500, lower[n500]), upper[n500])

    def objective(solution: dict[str, float]) -> float:
        total = 0.0
        for node in active_nodes:
            old_value = baseline[node]
            total += abs(solution[node] - old_value) / max(abs(old_value), epsilon)
        return total

    def violations(solution: dict[str, float]) -> list[tuple[str, str]]:
        return [
            (u, v)
            for u, v in active_edges
            if solution[u] + epsilon > solution[v] + 1e-12
        ]

    initial_violations = violations(baseline)
    if not initial_violations:
        return {"fixed_prices": fixed, "issues": issues}

    candidate_nodes = list(active_nodes)

    def solve_for_subset(changed_nodes: set[str]) -> tuple[dict[str, float], float] | None:
        lower = {}
        upper = {}
        for node in active_nodes:
            if node in changed_nodes:
                lower[node] = -float("inf")
                upper[node] = float("inf")
            else:
                lower[node] = baseline[node]
                upper[node] = baseline[node]

        for _ in range(len(active_nodes) * len(active_edges) + 10):
            bounds_changed = False
            for left, right in active_edges:
                required_lower_right = lower[left] + epsilon
                if required_lower_right > lower[right] + 1e-12:
                    lower[right] = required_lower_right
                    bounds_changed = True

                required_upper_left = upper[right] - epsilon
                if required_upper_left < upper[left] - 1e-12:
                    upper[left] = required_upper_left
                    bounds_changed = True

            if any(lower[node] > upper[node] + 1e-12 for node in active_nodes):
                return None

            if not bounds_changed:
                break

        if any(lower[node] > upper[node] + 1e-12 for node in active_nodes):
            return None

        values = {
            node: min(max(baseline[node], lower[node]), upper[node])
            for node in active_nodes
        }

        # User-requested order: first try the explicit "standard fix" formulas,
        # then use iterative epsilon reconciliation if needed.
        apply_standard_fix_first(values, lower, upper, changed_nodes)

        for _ in range(140):
            values_changed = False

            for node in changed_nodes:
                if node == "mtpl":
                    target = REAL_MARKET_MTPL_AVERAGE_PRICE
                else:
                    preferred = preferred_group_target(node, values)
                    target = baseline[node] if preferred is None else preferred
                new_value = min(max(target, lower[node]), upper[node])
                if abs(new_value - values[node]) > 1e-12:
                    values[node] = new_value
                    values_changed = True

            for node in topo_order:
                if incoming[node]:
                    min_from_parents = max(values[parent] + epsilon for parent in incoming[node])
                    new_value = max(values[node], min_from_parents)
                    new_value = min(max(new_value, lower[node]), upper[node])
                    if new_value > values[node] + 1e-12:
                        values[node] = new_value
                        values_changed = True

            for node in reversed(topo_order):
                if outgoing[node]:
                    max_from_children = min(values[child] - epsilon for child in outgoing[node])
                    new_value = min(values[node], max_from_children)
                    new_value = min(max(new_value, lower[node]), upper[node])
                    if new_value < values[node] - 1e-12:
                        values[node] = new_value
                        values_changed = True

            if any(values[node] < lower[node] - 1e-12 or values[node] > upper[node] + 1e-12 for node in active_nodes):
                return None

            if not values_changed:
                break

        if violations(values):
            return None

        rounded = {node: round_money(values[node]) for node in active_nodes}

        # Rounding repair with changed-node restriction.
        for _ in range(50):
            bad_edges = violations(rounded)
            if not bad_edges:
                return rounded, objective(rounded)

            edge = bad_edges[0]
            left, right = edge
            options = []

            if left in changed_nodes:
                cand = rounded.copy()
                cand[left] = round_money(cand[right] - epsilon)
                options.append(cand)

            if right in changed_nodes:
                cand = rounded.copy()
                cand[right] = round_money(cand[left] + epsilon)
                options.append(cand)

            if not options:
                return None

            rounded = min(options, key=lambda candidate: (ratio_penalty(candidate), objective(candidate)))

        return None

    best_solution = None

    for size in range(len(candidate_nodes) + 1):
        feasible = []
        for combo in itertools.combinations(candidate_nodes, size):
            subset = set(combo)
            solved = solve_for_subset(subset)
            if solved is not None:
                solved_values, solved_cost = solved
                feasible.append((subset, solved_values, solved_cost))

        if feasible:
            _, best_solution, _ = min(
                feasible,
                key=lambda item: (
                    changed_node_preference_penalty(item[0]),
                    ratio_penalty(item[1]),
                    item[2],
                    max(abs(item[1][n] - baseline[n]) for n in item[0]) if item[0] else 0.0,
                ),
            )
            break

    if best_solution is None:
        return {"fixed_prices": fixed, "issues": issues}

    def node_formula_hint(node: str) -> str | None:
        if node == "mtpl":
            return "mtpl = min(REAL_MARKET_MTPL_AVERAGE_PRICE, min(other_prices) - 0.01)"
        if node.endswith("_100") and (node.startswith("limited_casco_") or node.startswith("casco_")):
            return f"{node} = ({node.replace('_100', '_200')} / 0.85 + {node.replace('_100', '_500')} / 0.8) / 2"
        if node.endswith("_200") and (node.startswith("limited_casco_") or node.startswith("casco_")):
            return f"{node} = ({node.replace('_200', '_100')} * 0.85 + {node.replace('_200', '_500')} * 1.0625) / 2"
        if node.endswith("_500") and (node.startswith("limited_casco_") or node.startswith("casco_")):
            return f"{node} = ({node.replace('_500', '_100')} * 0.8 + {node.replace('_500', '_200')} * (1/1.0625)) / 2"
        return None

    def node_relation_context(node: str) -> str:
        violated = [
            f"{left} < {right}"
            for left, right in initial_violations
            if node == left or node == right
        ]
        if violated:
            return ", ".join(sorted(set(violated)))

        related = [
            f"{left} < {right}"
            for left, right in active_edges
            if node == left or node == right
        ]
        return ", ".join(sorted(set(related)))

    for node in active_nodes:
        old_value = baseline[node]
        new_value = round_money(best_solution[node])
        fixed[node] = new_value
        if new_value != round_money(old_value):
            relation_context = node_relation_context(node)
            formula_hint = node_formula_hint(node)
            details = f"Adjusted {node} from {old_value:.2f} to {new_value:.2f}."
            if relation_context:
                details += f" Repaired relations: {relation_context}."
            if formula_hint:
                details += f" Applied rule: {formula_hint}."
            issues.append(
                details
            )

    return {"fixed_prices": fixed, "issues": issues}


# --- Local testing only ---
example_prices = {
    "mtpl": 4000,
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
