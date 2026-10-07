"""Constants for the accounts app.

`GST_STATE_CHOICES` now lives in `apps.core.constants`, which is the single
source of truth for tax data shared across apps. It is re-exported here so
existing `from .constants import GST_STATE_CHOICES` imports keep working.
"""

from apps.core.constants import GST_STATE_CHOICES  # noqa: F401  (re-export)