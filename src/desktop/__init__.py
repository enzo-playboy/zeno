"""
Módulo Desktop do Zeno Radar (System Tray e Windows Startup).
"""

from src.desktop.startup_manager import register_startup, unregister_startup, is_startup_registered

__all__ = ["register_startup", "unregister_startup", "is_startup_registered"]
