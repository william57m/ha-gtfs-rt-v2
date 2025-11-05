"""Data structures for transit stop information."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any


@dataclass
class StopDetails:
    """Data class to store stop arrival information."""

    arrival_time: datetime
    position: Optional[Any] = None
    is_real_time: bool = True
