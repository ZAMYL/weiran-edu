"""DS-06A reproducible programming-error-pattern benchmark."""

import sys
from pathlib import Path

# C-01 lives in the repository's existing prototypes package rather than apps/api.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

SYSTEMS = ("A", "B", "C")
DEPENDENCIES = {
    "main": "d2ccd7f79d355a73d28861224602fff2934c31fe",
    "pr_105": "a0d14221f7e12f4d376047b84fbc135a5c84ebc7",
    "pr_107": "ce8fb6acecddeeb80d759b912dcb367da5f9f9cc",
    "pr_108": "b929787284b32162f522c1fbec17ef1891e3ab4f",
}
