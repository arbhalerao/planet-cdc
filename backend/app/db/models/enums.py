import enum


class WorkflowStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    completed = "completed"
    completed_with_errors = "completed_with_errors"
    failed = "failed"


class WorkflowItemStatus(str, enum.Enum):
    discovered = "discovered"
    queued = "queued"
    fetching = "fetching"
    scoring = "scoring"
    processed = "processed"
    fetch_failed = "fetch_failed"
    score_failed = "score_failed"
    failed = "failed"
    skipped = "skipped"


class ModelRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class ReviewStatus(str, enum.Enum):
    new = "new"
    reviewed = "reviewed"
    item_of_interest = "item_of_interest"
    dismissed = "dismissed"
    false_positive = "false_positive"
    needs_follow_up = "needs_follow_up"


class Severity(str, enum.Enum):
    green = "green"
    yellow = "yellow"
    red = "red"


class CompatibilityLevel(str, enum.Enum):
    full = "full"
    partial = "partial"
    incompatible = "incompatible"


class TimeMode(str, enum.Enum):
    historical = "historical"
    fixed_future = "fixed_future"
