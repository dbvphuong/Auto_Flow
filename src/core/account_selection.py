from data.models import Account


ACCOUNT_FEATURE_BY_TASK_TYPE = {
    "image": "is_image",
    "video": "is_video",
}


def account_enabled_for_task(account, task_type):
    """Return whether the account was explicitly enabled for this flow."""
    feature = ACCOUNT_FEATURE_BY_TASK_TYPE.get(task_type)
    return bool(feature and getattr(account, feature, False))


def enabled_accounts_query(db, task_type):
    """Build the ordered account query for an image or video flow.

    ``is_active`` is deliberately not part of this query. That field is a
    displayed account/cookie status; the Image and Video checkboxes are the
    user's routing controls.
    """
    feature = ACCOUNT_FEATURE_BY_TASK_TYPE.get(task_type)
    if feature is None:
        raise ValueError(f"Unsupported account task type: {task_type}")
    return (
        db.query(Account)
        .filter(getattr(Account, feature).is_(True))
        .order_by(Account.position.asc())
    )
