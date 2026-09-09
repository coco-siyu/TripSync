"""Deterministic advisory budget ranges for itinerary planning."""

from __future__ import annotations

from collections.abc import Sequence

from src.models import (
    Activity,
    BudgetLevel,
    DailyBudgetEstimate,
    ScheduledActivity,
)


# Approximate admission/experience cost per person, not live pricing.
ACTIVITY_COST_RANGES_EUR = {
    BudgetLevel.FREE: (0, 0),
    BudgetLevel.LOW: (5, 20),
    BudgetLevel.MODERATE: (20, 40),
    BudgetLevel.HIGH: (40, 80),
}

# One general allowance per person covering meals and snacks for the day.
FOOD_ALLOWANCE_RANGES_EUR = {
    BudgetLevel.FREE: (20, 30),
    BudgetLevel.LOW: (30, 50),
    BudgetLevel.MODERATE: (50, 80),
    BudgetLevel.HIGH: (80, 130),
}

# UI budget bands are €50–100, €100–175, and €175+. ``free`` is retained
# only for backward compatibility with older shared-trip settings.
DAILY_BUDGET_UPPER_EUR = {
    BudgetLevel.FREE: 50,
    BudgetLevel.LOW: 100,
    BudgetLevel.MODERATE: 175,
    BudgetLevel.HIGH: None,
}

DAILY_BUDGET_LABELS = {
    BudgetLevel.FREE: "up to €50",
    BudgetLevel.LOW: "€50–100",
    BudgetLevel.MODERATE: "€100–175",
    BudgetLevel.HIGH: "€175+",
}


def activity_cost_range(activity: Activity) -> tuple[int, int]:
    """Map one catalog price level to a stable per-person euro range."""

    return ACTIVITY_COST_RANGES_EUR[activity.budget_level]


def estimate_daily_budget(
    activities: Sequence[ScheduledActivity],
    target_budget_level: BudgetLevel,
) -> DailyBudgetEstimate | None:
    """Estimate one day, returning None only for incomplete legacy cost data."""

    if any(
        activity.estimated_cost_min_eur is None
        or activity.estimated_cost_max_eur is None
        for activity in activities
    ):
        return None
    activity_min = sum(
        activity.estimated_cost_min_eur or 0 for activity in activities
    )
    activity_max = sum(
        activity.estimated_cost_max_eur or 0 for activity in activities
    )
    food_min, food_max = FOOD_ALLOWANCE_RANGES_EUR[target_budget_level]
    total_min = activity_min + food_min
    total_max = activity_max + food_max
    upper = DAILY_BUDGET_UPPER_EUR[target_budget_level]
    return DailyBudgetEstimate(
        activity_min_eur=activity_min,
        activity_max_eur=activity_max,
        food_min_eur=food_min,
        food_max_eur=food_max,
        total_min_eur=total_min,
        total_max_eur=total_max,
        target_budget_level=target_budget_level,
        potentially_over_budget=upper is not None and total_max > upper,
    )


def euro_range(minimum: int, maximum: int) -> str:
    """Format an approximate euro range compactly."""

    return f"€{minimum}" if minimum == maximum else f"€{minimum}–€{maximum}"


def daily_budget_label(level: BudgetLevel) -> str:
    """Return the traveler-facing daily band for one internal budget level."""

    return DAILY_BUDGET_LABELS[level]
