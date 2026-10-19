import os
import sys
import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def get_app_dir() -> Path:
    """
    Retorna o diretório persistente do usuário para dados do Zeno Radar.
    No Windows: %LOCALAPPDATA%\\ZenoRadar
    No Linux/Mac: ~/.zenoradar
    Nunca utiliza o diretório volátil _MEIxxxx do PyInstaller.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = os.path.expanduser("~")
        app_dir = Path(base) / "ZenoRadar"
    else:
        app_dir = Path(os.path.expanduser("~")) / ".zenoradar"

    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir

def get_db_path() -> Path:
    """Caminho do banco de dados SQLite persistente."""
    app_dir = get_app_dir()
    db_file = app_dir / "zeno_radar.db"

    # Migração automática: se existir bid_engine.db na raiz do projeto e ainda não no LOCALAPPDATA, copia
    if not db_file.exists():
        old_db = Path(__file__).resolve().parent.parent.parent / "bid_engine.db"
        if old_db.exists():
            try:
                shutil.copy2(old_db, db_file)
                logger.info(f"Banco de dados migrado com sucesso de {old_db} para {db_file}")
            except Exception as e:
                logger.warning(f"Não foi possível migrar banco antigo: {e}")

    return db_file

def get_downloads_dir() -> Path:
    """Diretório persistente para downloads de anexos e PDFs de editais."""
    d = get_app_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_logs_dir() -> Path:
    """Diretório persistente de logs da aplicação."""
    l = get_app_dir() / "logs"
    l.mkdir(parents=True, exist_ok=True)
    return l

def get_config_path() -> Path:
    """Arquivo de configuração customizada do cliente (JSON)."""
    return get_app_dir() / "config_cliente.json"

def get_license_path() -> Path:
    """
    Caminho do arquivo de licença persistente.
    Procura primeiro em %LOCALAPPDATA%\\ZenoRadar\\license.key,
    depois na pasta do executável, depois na raiz do projeto.
    """
    local_key = get_app_dir() / "license.key"
    if local_key.exists():
        return local_key

    # Se estiver rodando como .exe, verifica na mesma pasta do binário
    if getattr(sys, 'frozen', False):
        exe_key = Path(sys.executable).parent / "license.key"
        if exe_key.exists():
            try:
                shutil.copy2(exe_key, local_key)
            except Exception:
                pass
            return exe_key

    # Raiz do projeto em desenvolvimento
    dev_key = Path(__file__).resolve().parent.parent.parent / "license.key"
    if dev_key.exists():
        return dev_key

    return local_key

def ensure_directories():
    """Garante que todas as pastas persistentes existam."""
    get_app_dir()
    get_downloads_dir()
    get_logs_dir()
