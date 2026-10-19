"""
Módulo de Licenciamento, HWID e Validação do Zeno Radar.
"""

from src.licensing.hwid import get_hwid
from src.licensing.validator import LicenseValidator, ClientLicenseConfig

__all__ = ["get_hwid", "LicenseValidator", "ClientLicenseConfig"]
