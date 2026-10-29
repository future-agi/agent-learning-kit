"""Talk to a FutureAGI platform from Python: the layer `agent-learn`'s platform commands use.

from fi.alk.platform import Client
with Client.from_profile() as client:
    print(client.whoami())
"""

from .client import Client, Identity, reachable
from .errors import EXIT_CODES, PlatformError
from .profiles import Profile

__all__ = ["Client", "EXIT_CODES", "Identity", "PlatformError", "Profile", "reachable"]
