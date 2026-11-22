"""ADL Lite — local minimal package for Maat governance development."""

__version__ = "0.9.0-alpha.maat-dev"

from .crdt import CRDTState, StatusOrder, merge_event_chains  # noqa: F401
from .exceptions import (  # noqa: F401
    ADLConsensusError,
    ADLError,
    ADLOntologyError,
    ADLValidationError,
)
from .models import (  # noqa: F401
    ADLFrontMatter,
    DiscoveryStatus,
    Event,
    EventChain,
    EventType,
)
