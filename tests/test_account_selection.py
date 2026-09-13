import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.account_selection import account_enabled_for_task, enabled_accounts_query  # noqa: E402
from data.database import Base  # noqa: E402
from data.models import Account  # noqa: E402


def _session_with_accounts():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        Account(
            email="video-only@example.com", position=2, is_active=False,
            is_image=False, is_video=True,
        ),
        Account(
            email="image-only@example.com", position=1, is_active=False,
            is_image=True, is_video=False,
        ),
        Account(
            email="status-only@example.com", position=0, is_active=True,
            is_image=False, is_video=False,
        ),
    ])
    db.commit()
    return db


def test_image_accounts_follow_image_checkbox_not_display_status():
    db = _session_with_accounts()
    try:
        accounts = enabled_accounts_query(db, "image").all()
        assert [account.email for account in accounts] == ["image-only@example.com"]
        assert account_enabled_for_task(accounts[0], "image") is True
        assert account_enabled_for_task(accounts[0], "video") is False
    finally:
        db.close()


def test_video_accounts_follow_video_checkbox_not_display_status():
    db = _session_with_accounts()
    try:
        accounts = enabled_accounts_query(db, "video").all()
        assert [account.email for account in accounts] == ["video-only@example.com"]
        assert account_enabled_for_task(accounts[0], "video") is True
        assert account_enabled_for_task(accounts[0], "image") is False
    finally:
        db.close()
