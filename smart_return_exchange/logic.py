"""Business rules: constants, eligibility, smart assessment, codes, timeline.

This file has no Flask code, so it is easy to read and test on its own.
"""
from datetime import date, datetime

# ---------------------------------------------------------------- constants
RETURN_REASONS = [
    "Damaged Product", "Defective Product", "Wrong Product Received",
    "Missing Parts", "Product Not as Described", "Size Issue",
    "Quality Issue", "Other",
]
PRODUCT_CONDITIONS = ["Unopened", "Opened but unused", "Used", "Damaged"]
REFUND_METHODS = ["Original Payment Method", "Bank Transfer", "Store Credit"]
RETURN_TYPES = ["Full Return", "Partial Return"]
PRODUCT_CATEGORIES = ["Electronics", "Accessories", "Bags", "Footwear",
                      "Clothing", "Home & Kitchen", "Other"]

STATUSES = [
    "REQUESTED", "UNDER REVIEW", "INFO REQUESTED", "APPROVED",
    "PICKUP SCHEDULED", "PICKED UP", "QUALITY CHECK", "REFUND INITIATED",
    "EXCHANGE PROCESSING", "COMPLETED", "REJECTED",
]
FLOW_RETURN = ["REQUESTED", "UNDER REVIEW", "APPROVED", "PICKUP SCHEDULED",
               "PICKED UP", "QUALITY CHECK", "REFUND INITIATED", "COMPLETED"]
FLOW_EXCHANGE = ["REQUESTED", "UNDER REVIEW", "APPROVED", "PICKUP SCHEDULED",
                 "PICKED UP", "QUALITY CHECK", "EXCHANGE PROCESSING", "COMPLETED"]

STATUS_GROUPS = {
    "pending": ["REQUESTED", "UNDER REVIEW", "INFO REQUESTED"],
    "approved": ["APPROVED", "PICKUP SCHEDULED", "PICKED UP", "QUALITY CHECK",
                 "REFUND INITIATED", "EXCHANGE PROCESSING"],
    "rejected": ["REJECTED"],
    "completed": ["COMPLETED"],
}

STATUS_ICONS = {
    "REQUESTED": "fa-file-circle-plus", "UNDER REVIEW": "fa-magnifying-glass",
    "INFO REQUESTED": "fa-circle-question", "APPROVED": "fa-circle-check",
    "PICKUP SCHEDULED": "fa-calendar-check", "PICKED UP": "fa-truck-ramp-box",
    "QUALITY CHECK": "fa-clipboard-check", "REFUND INITIATED": "fa-money-bill-transfer",
    "EXCHANGE PROCESSING": "fa-arrows-rotate", "COMPLETED": "fa-flag-checkered",
    "REJECTED": "fa-circle-xmark",
}

REFUND_STATUSES = ["Pending", "Processing", "Completed"]
EXCHANGE_STATUSES = ["Pending", "Approved", "Replacement Prepared",
                     "Shipped", "Delivered", "Completed"]

# Admin actions: which statuses they are allowed from, and the resulting status.
ACTIONS = {
    "start_review":    {"from": {"REQUESTED"}, "to": "UNDER REVIEW", "types": {"return", "exchange"}},
    "approve":         {"from": {"UNDER REVIEW", "INFO REQUESTED"}, "to": "APPROVED", "types": {"return", "exchange"}},
    "reject":          {"from": {"UNDER REVIEW", "INFO REQUESTED", "QUALITY CHECK"}, "to": "REJECTED", "types": {"return", "exchange"}},
    "request_info":    {"from": {"UNDER REVIEW"}, "to": "INFO REQUESTED", "types": {"return", "exchange"}},
    "schedule_pickup": {"from": {"APPROVED"}, "to": "PICKUP SCHEDULED", "types": {"return", "exchange"}},
    "mark_picked_up":  {"from": {"PICKUP SCHEDULED"}, "to": "PICKED UP", "types": {"return", "exchange"}},
    "quality_check":   {"from": {"PICKED UP"}, "to": "QUALITY CHECK", "types": {"return", "exchange"}},
    "initiate_refund": {"from": {"QUALITY CHECK"}, "to": "REFUND INITIATED", "types": {"return"}},
    "process_exchange": {"from": {"QUALITY CHECK"}, "to": "EXCHANGE PROCESSING", "types": {"exchange"}},
    "mark_completed":  {"from": {"REFUND INITIATED", "EXCHANGE PROCESSING"}, "to": "COMPLETED", "types": {"return", "exchange"}},
}


def available_actions(rtype, status):
    return [a for a, c in ACTIONS.items() if status in c["from"] and rtype in c["types"]]


# ------------------------------------------------------------- eligibility
def parse_date(value):
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def check_eligibility(purchase_date, window_days, today=None):
    """Return dict: eligible, days_since (purchase), days_left (in window)."""
    today = today or date.today()
    days_since = (today - parse_date(purchase_date)).days
    days_left = int(window_days) - days_since
    return {"eligible": days_left >= 0, "days_since": days_since, "days_left": days_left}


# -------------------------------------------------- smart return assessment
DAMAGE_REASONS = {"Damaged Product", "Defective Product",
                  "Wrong Product Received", "Missing Parts"}
SIZE_CATEGORIES = {"Footwear", "Clothing", "Bags"}


def smart_assess(reason, condition, days_since, window_days, has_image, category):
    """Simple RULE-BASED scoring (no AI, no external API).

    Returns (label, notes). It is only decision support - the admin decides.
    """
    score = 0
    notes = []

    # 1. reason
    if reason in DAMAGE_REASONS:
        score += 3
        notes.append(f"Reason '{reason}' is usually a valid claim")
    elif reason == "Product Not as Described":
        score += 2
        notes.append("'Not as described' claims need a quick comparison with the listing")
    elif reason in ("Quality Issue", "Size Issue"):
        score += 1
        notes.append(f"'{reason}' is subjective and may need checking")
    else:
        notes.append("Reason 'Other' needs a manual look")

    # 2. category vs reason
    if reason == "Size Issue" and category not in SIZE_CATEGORIES:
        score -= 2
        notes.append(f"Size issues are unusual for the '{category}' category")

    # 3. product condition
    if condition == "Unopened":
        score += 2
        notes.append("Product is unopened")
    elif condition == "Opened but unused":
        score += 1
        notes.append("Product is opened but unused")
    elif condition == "Used":
        score -= 1
        notes.append("Product has been used")
    elif condition == "Damaged":
        if reason in DAMAGE_REASONS:
            score += 1
            notes.append("Damaged condition matches the reason given")
        else:
            score -= 2
            notes.append("Damaged condition does not match the reason given")

    # 4. days since purchase
    if days_since > window_days:
        score -= 3
        notes.append("Purchase is outside the return window")
    elif days_since <= window_days / 2:
        score += 1
        notes.append("Requested early in the return window")
    else:
        notes.append("Requested late in the return window")

    # 5. evidence
    if has_image:
        score += 2
        notes.append("Evidence image uploaded")
    elif reason in DAMAGE_REASONS or condition == "Damaged":
        score -= 2
        notes.append("No evidence image for a damage-type claim")
    else:
        notes.append("No evidence image uploaded")

    if score >= 5:
        label = "Likely Eligible"
    elif score >= 2:
        label = "Needs Manual Review"
    else:
        label = "Likely Not Eligible"
    return label, " | ".join(notes)


def priority_for(reason):
    if reason in DAMAGE_REASONS:
        return "High"
    if reason in ("Product Not as Described", "Quality Issue"):
        return "Medium"
    return "Low"


# ------------------------------------------------------------------- codes
def next_code(db, table, column, prefix):
    """RET-2026-0001 style codes. table/column come from our own code only."""
    year = date.today().year
    row = db.execute(
        f"SELECT {column} FROM {table} WHERE {column} LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{prefix}-{year}-%",)).fetchone()
    number = int(row[0].split("-")[-1]) + 1 if row else 1
    return f"{prefix}-{year}-{number:04d}"


# ---------------------------------------------------------------- timeline
def build_timeline(rtype, status, history):
    """Turn status_history rows into steps for the visual timeline."""
    flow = FLOW_RETURN if rtype == "return" else FLOW_EXCHANGE
    first = {}
    for h in history:
        first.setdefault(h["status"], h["created_at"])

    steps = []
    if status == "REJECTED":
        for s in [x for x in flow if x in first] + ["REJECTED"]:
            steps.append({"label": s, "icon": STATUS_ICONS[s], "date": first.get(s),
                          "state": "rejected" if s == "REJECTED" else "done"})
        return steps

    current = "UNDER REVIEW" if status == "INFO REQUESTED" else status
    idx = flow.index(current)
    for i, s in enumerate(flow):
        if i < idx or (i == idx and s == "COMPLETED"):
            state = "done"
        elif i == idx:
            state = "active"
        else:
            state = "pending"
        steps.append({"label": s, "icon": STATUS_ICONS[s], "date": first.get(s), "state": state})
    return steps
