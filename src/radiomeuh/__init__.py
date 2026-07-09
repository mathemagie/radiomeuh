"""Radio Meuh — stream the independent French radio from your terminal or menu bar."""

__version__ = "1.0.0"

from .core import STREAMS, MetadataReader, TrackStore, default_db_path, find_player

__all__ = [
    "STREAMS",
    "MetadataReader",
    "TrackStore",
    "default_db_path",
    "find_player",
    "__version__",
]
