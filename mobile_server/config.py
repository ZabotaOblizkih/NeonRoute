"""
Configuration settings for NeonRoute Mobile Server Gateway.
"""

import os
from typing import List


class MobileServerConfig:
    """Config for Mobile Server Gateway."""
    HOST: str = os.getenv("NEONROUTE_MOBILE_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("NEONROUTE_MOBILE_PORT", "8000"))
    TIMEOUT: float = float(os.getenv("NEONROUTE_MOBILE_TIMEOUT", "180.0"))
    CORS_ORIGINS: List[str] = ["*"]


settings = MobileServerConfig()
