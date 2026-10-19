import os
from pathlib import Path
from typing import List
try:
    from pydantic_settings import BaseSettings
except (ImportError, ModuleNotFoundError):
    try:
        from pydantic import BaseSettings
    except (ImportError, ModuleNotFoundError):
        try:
            from pydantic import BaseModel as BaseSettings
        except (ImportError, ModuleNotFoundError):
            class BaseSettings:
                def __init__(self, **kwargs):
                    for k, v in self.__class__.__dict__.items():
                        if not k.startswith("_") and not callable(v):
                            env_val = os.getenv(k)
                            if env_val is not None:
                                if isinstance(v, bool):
                                    setattr(self, k, env_val.lower() in ("1", "true", "yes"))
                                elif isinstance(v, int):
                                    try:
                                        setattr(self, k, int(env_val))
                                    except ValueError:
                                        pass
                                else:
                                    setattr(self, k, env_val)
                            else:
                                setattr(self, k, v)
                    for k, v in kwargs.items():
                        setattr(self, k, v)

try:
    from pydantic import Field
except (ImportError, ModuleNotFoundError):
    def Field(default=None, default_factory=None, **kwargs):
        if default_factory is not None:
            return default_factory()
        return default

from src.utils.paths import get_db_path, get_downloads_dir

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # Região e Filtros
    TARGET_UF: str = "MT"
    TARGET_KEYWORDS: str = "informatica,computador,notebook,nobreak,servidor,ti,software"
    NEGATIVE_KEYWORDS: str = "obras,pavimentacao,limpeza,combustivel,merenda,reforma"
    
    # Parâmetros PNCP
    PNCP_LOOKBACK_DAYS: int = 7
    PNCP_PAGE_SIZE: int = 10
    
    # Banco de dados (Persistente no LOCALAPPDATA)
    DATABASE_URL: str = f"sqlite:///{get_db_path()}"
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    
    # LLM Settings
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "openai"
    USE_CLOUD_PROXY: bool = False
    AI_PROXY_URL: str = ""
    LICENSE_KEY: str = ""
    
    # WhatsApp / Notifier
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_API_URL: str = ""
    WHATSAPP_API_TOKEN: str = ""
    WHATSAPP_RECIPIENT_PHONE: str = ""
    
    # TinyFish & Coleta externa
    TINYFISH_API_KEY: str = ""
    
    # Armazenamento de arquivos (Persistente no LOCALAPPDATA)
    DOWNLOAD_DIR: str = str(get_downloads_dir())
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"
    }
    
    @property
    def target_keywords_list(self) -> List[str]:
        return [k.strip().lower() for k in self.TARGET_KEYWORDS.split(",") if k.strip()]
        
    @property
    def negative_keywords_list(self) -> List[str]:
        return [k.strip().lower() for k in self.NEGATIVE_KEYWORDS.split(",") if k.strip()]

settings = Settings()

# Garantir criação do diretório de downloads
os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
