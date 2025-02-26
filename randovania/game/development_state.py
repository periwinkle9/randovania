from __future__ import annotations

from enum import Enum

import randovania


class DevelopmentState(Enum):
    STABLE = "stable"
    STAGING = "staging"
    SOURCE = "source"

    @property
    def is_stable(self) -> bool:
        return self == DevelopmentState.STABLE

    def can_view(self) -> bool:
        if self.is_stable:
            return True

        if self == DevelopmentState.STAGING:
            return randovania.is_dev_version()

        return not randovania.is_frozen()
