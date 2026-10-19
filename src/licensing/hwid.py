import hashlib
import logging
import os
import subprocess
import sys
import uuid

logger = logging.getLogger(__name__)

def get_raw_hardware_id() -> str:
    """
    Obtém o identificador único de hardware da máquina Windows.
    Prioridades:
    1. PowerShell Get-CimInstance Win32_ComputerSystemProduct UUID (Padrão Windows 10/11)
    2. wmic csproduct get uuid (Compatibilidade Windows Legado)
    3. Windows Registry MachineGuid (SOFTWARE\\Microsoft\\Cryptography)
    4. Fallback determinístico baseado no MAC da máquina (uuid.getnode())
    """
    if sys.platform == "win32":
        # 1. PowerShell CIM Instance (Recomendado para Windows 11 onde wmic foi removido)
        try:
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            raw_uuid = res.stdout.strip()
            if raw_uuid and len(raw_uuid) > 10 and "error" not in raw_uuid.lower():
                return raw_uuid
        except Exception as e:
            logger.debug(f"Falha ao obter UUID via PowerShell: {e}")

        # 2. wmic (Windows legado)
        try:
            cmd = ["wmic", "csproduct", "get", "uuid"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip() and "UUID" not in line.upper()]
            if lines and len(lines[0]) > 10:
                return lines[0]
        except Exception as e:
            logger.debug(f"Falha ao obter UUID via wmic: {e}")

        # 3. Registro do Windows: MachineGuid
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                if guid:
                    return str(guid).strip()
        except Exception as e:
            logger.debug(f"Falha ao obter MachineGuid do Registro: {e}")

    # 4. Fallback universal determinístico por nó de rede
    node = uuid.getnode()
    return f"FALLBACK-NODE-{node}"

def get_hwid() -> str:
    """
    Calcula o hash SHA-256 do identificador físico de hardware.
    Retorna uma string hexadecimal de 64 caracteres.
    """
    raw_id = get_raw_hardware_id().strip().upper()
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()

if __name__ == "__main__":
    hwid = get_hwid()
    print(f"Raw HW ID: {get_raw_hardware_id()}")
    print(f"SHA-256 HWID: {hwid}")
