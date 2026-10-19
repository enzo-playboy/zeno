import sys
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

def get_target_executable_path() -> Optional[str]:
    """Retorna o caminho do executável para inicialização."""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return None

def register_startup(app_name: str = "ZenoRadarOaaS", custom_path: Optional[str] = None) -> bool:
    """
    Adiciona o executável ao registro do Windows para inicialização automática no boot.
    Usa HKEY_CURRENT_USER para não exigir permissão de Administrador (UAC).
    """
    if sys.platform != "win32":
        logger.info("Registro de inicialização suportado apenas no Windows.")
        return False

    exe_path = custom_path or get_target_executable_path()
    if not exe_path:
        logger.info("Modo de desenvolvimento: startup automático só é ativado no executável compilado.")
        return False

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            cmd = f'"{exe_path}"'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            logger.info(f"Aplicativo registrado com sucesso no Windows Startup: {cmd}")
            return True
    except Exception as e:
        logger.error(f"Erro ao registrar no Windows Startup: {e}")
        return False

def unregister_startup(app_name: str = "ZenoRadarOaaS") -> bool:
    """Remove o aplicativo da inicialização automática do Windows."""
    if sys.platform != "win32":
        return False

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, app_name)
            logger.info(f"Registro '{app_name}' removido do Windows Startup.")
            return True
    except FileNotFoundError:
        return True
    except Exception as e:
        logger.error(f"Erro ao remover do Windows Startup: {e}")
        return False

def is_startup_registered(app_name: str = "ZenoRadarOaaS") -> bool:
    """Verifica se o aplicativo está configurado para iniciar com o Windows."""
    if sys.platform != "win32":
        return False

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, app_name)
            return bool(val)
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.error(f"Erro ao consultar Windows Startup: {e}")
        return False
