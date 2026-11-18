"""ContextBridge: local-first project context memory for AI coding agents."""

import logging

__version__ = "0.2.0"

# Library convention: no output unless a handler (the `.context/cb.log` file) is attached.
logging.getLogger("contextbridge").addHandler(logging.NullHandler())
