"""Evidence-holder runtime: the transport consumer (``HolderService``), in-process holders for single-node use
(``EmbeddedHolders``) and the standalone process (``process.run_holder`` / ``process.main``)."""
from .embedded import EmbeddedHolders
from .service import HolderService

__all__ = ["EmbeddedHolders", "HolderService"]
