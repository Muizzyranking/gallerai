import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.platform import PlatformSettings
from app.models.user import User

logger = logging.getLogger(__name__)

KEY_ENFORCE_PRICING = "enforce_pricing"
KEY_MAINTENANCE_MODE = "maintainance_mode"
KEY_MAX_UPLOAD_MB = "max_upload_mb"

DEFAULTS: dict[str, object] = {
    KEY_ENFORCE_PRICING: False,
    KEY_MAINTENANCE_MODE: False,
    KEY_MAX_UPLOAD_MB: 20,
}


def get_setting(key: str, db: Session) -> object:
    """
    Returns the platform settings for a key
    returns the default value if not set in the database
    """
    record = db.query(PlatformSettings).filter(PlatformSettings.key == key).first()
    if not record:
        return DEFAULTS.get(key)
    return json.loads(record.value)


def set_setting(
    key: str, value: object, db: Session, admin: User, description: str | None = None
) -> PlatformSettings:
    """
    creates or updates the platform settings for a key
    Audits the change with the user id of the updater and the timestamp of the update
    """
    record = db.query(PlatformSettings).filter(PlatformSettings.key == key).first()
    now = datetime.now(timezone.utc)
    if record:
        record.value = json.dumps(value)
        record.updated_by = admin.id
        record.updated_at = now
        if description is not None:
            record.description = description
    else:
        record = PlatformSettings(
            key=key,
            value=json.dumps(value),
            description=description,
            updated_by=admin.id,
            updated_at=now,
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    logger.info(f"Platform setting updated - key: {key} by admin: {admin.id}")
    return record


def get_all_settings(db: Session) -> dict[str, object]:
    """
    Return all platform settings as a flat dict.
    Merges stored values over defaults so all known keys are present.
    """
    records = db.query(PlatformSettings).all()
    result = dict(DEFAULTS)
    for record in records:
        result[record.key] = json.loads(record.value)
    return result


def is_enforce_pricing(db: Session) -> bool:
    """Convenience accessor for the enforce_pricing flag."""
    return bool(get_setting(KEY_ENFORCE_PRICING, db))
