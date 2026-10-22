# -*- coding: utf-8 -*-
"""
Cliente e Gerenciador de Cache Redis — Zeno Radar
Gerencia buffer de páginas raspadas com Scrapling, cache de requisições do PNCP,
locks distribuídos e persistência de auditorias completas de licitações.
"""

import json
import logging
from typing import Any, Optional, Union
import hashlib

logger = logging.getLogger("RedisCache")

try:
    import redis
    HAS_REDIS_PKG = True
except ImportError:
    redis = None
    HAS_REDIS_PKG = False

from src.config import settings


class RedisCache:
    """
    Cliente singleton de Cache Redis com fallback defensivo em memória.
    Permite cache ultrarrápido de HTMLs raspados via Scrapling, dados estruturados
    do PNCP e auditorias completas com expiração automática (TTL).
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(RedisCache, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, redis_url: Optional[str] = None):
        if getattr(self, "_initialized", False):
            return

        self.redis_url = redis_url or settings.REDIS_URL
        self.ttl_default = getattr(settings, "REDIS_TTL_DEFAULT", 604800)  # 7 dias
        self.client: Optional[Any] = None
        self._memory_fallback: dict = {}
        self._connected = False
        self._last_connect_attempt = 0.0
        self._reconnect_cooldown = 15.0  # tenta reconectar no máximo a cada 15s

        self._conectar()
        self._initialized = True

    def _conectar(self) -> bool:
        """Tenta conectar ao Redis com timeout reduzido para não travar a aplicação."""
        if not HAS_REDIS_PKG:
            logger.warning("Pacote 'redis' não está instalado. Operando com fallback em memória.")
            return False

        import time
        now = time.time()
        self._last_connect_attempt = now

        try:
            self.client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.6,
                socket_timeout=1.0,
                retry_on_timeout=False
            )
            # Teste rápido de ping
            if self.client.ping():
                self._connected = True
                logger.info(f"Conexão com Redis estabelecida com sucesso ({self.redis_url}).")
                return True
        except Exception as e:
            logger.warning(f"Não foi possível conectar ao Redis ({self.redis_url}): {e}. Ativando buffer de contingência em memória.")
            self._connected = False
            self.client = None

        return False

    def is_connected(self) -> bool:
        """Verifica se o Redis está online e respondendo."""
        if self._connected and self.client:
            try:
                return bool(self.client.ping())
            except Exception:
                self._connected = False
                return False
        
        # Só tenta reconectar se passou o período de cooldown
        import time
        if time.time() - self._last_connect_attempt > self._reconnect_cooldown:
            return self._conectar()
        return False

    def _format_key(self, prefix: str, raw_key: str) -> str:
        """Padroniza chaves no Redis (ex: zeno:scan:hash ou zeno:edital:id)."""
        clean_key = str(raw_key).strip().replace(" ", "_")
        if len(clean_key) > 120:
            h = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()[:16]
            clean_key = f"{clean_key[:80]}_{h}"
        return f"zeno:{prefix}:{clean_key}"

    def get(self, key: str) -> Optional[str]:
        """Obtém uma string do Redis ou fallback."""
        if self.is_connected() and self.client:
            try:
                val = self.client.get(key)
                if val is not None:
                    return val
            except Exception as e:
                logger.warning(f"Erro ao ler chave '{key}' do Redis: {e}")

        return self._memory_fallback.get(key)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Armazena uma string com TTL no Redis ou fallback."""
        ttl_final = ttl if ttl is not None else self.ttl_default

        if self.is_connected() and self.client:
            try:
                if ttl_final > 0:
                    self.client.setex(key, ttl_final, value)
                else:
                    self.client.set(key, value)
                return True
            except Exception as e:
                logger.warning(f"Erro ao salvar chave '{key}' no Redis: {e}")

        self._memory_fallback[key] = value
        return True

    def get_json(self, key: str) -> Optional[Any]:
        """Obtém e desserializa JSON armazenado no Redis."""
        raw = self.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception as e:
            logger.error(f"Erro ao decodificar JSON da chave '{key}': {e}")
            return None

    def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Serializa e salva um objeto Python como JSON com TTL."""
        try:
            dumped = json.dumps(value, ensure_ascii=False, default=str)
            return self.set(key, dumped, ttl=ttl)
        except Exception as e:
            logger.error(f"Erro ao codificar JSON para chave '{key}': {e}")
            return False

    def delete(self, key: str) -> bool:
        """Remove uma chave."""
        if self.is_connected() and self.client:
            try:
                self.client.delete(key)
            except Exception as e:
                logger.warning(f"Erro ao deletar '{key}' do Redis: {e}")

        self._memory_fallback.pop(key, None)
        return True

    def exists(self, key: str) -> bool:
        """Verifica se uma chave existe."""
        if self.is_connected() and self.client:
            try:
                return bool(self.client.exists(key))
            except Exception:
                pass
        return key in self._memory_fallback

    # =========================================================================
    # MÉTODOS ESPECÍFICOS PARA O FLUXO DE SCAN POR LINK
    # =========================================================================

    def salvar_html_pagina(self, url: str, html: str, ttl: Optional[int] = 86400 * 3) -> bool:
        """Salva no Redis o HTML bruto raspado via Scrapling (TTL padrão: 3 dias)."""
        key = self._format_key("html", url)
        logger.info(f"Gravando HTML raspado no Redis ({len(html)} bytes) para '{url[:60]}...'")
        return self.set(key, html, ttl=ttl)

    def obter_html_pagina(self, url: str) -> Optional[str]:
        """Recupera do Redis o HTML bruto previamente raspado."""
        key = self._format_key("html", url)
        html = self.get(key)
        if html:
            logger.info(f"Cache HIT no Redis para HTML de '{url[:60]}...' ({len(html)} bytes)")
        return html

    def salvar_auditoria_scan(self, id_ou_url: str, audit_data: dict, ttl: Optional[int] = None) -> bool:
        """Salva a auditoria completa (leis, brechas, pontos de atenção) no Redis."""
        key = self._format_key("audit", id_ou_url)
        logger.info(f"Gravando auditoria estruturada no Redis para chave '{key}'")
        return self.set_json(key, audit_data, ttl=ttl)

    def obter_auditoria_scan(self, id_ou_url: str) -> Optional[dict]:
        """Recupera a auditoria completa do Redis."""
        key = self._format_key("audit", id_ou_url)
        res = self.get_json(key)
        if res:
            logger.info(f"Cache HIT no Redis para auditoria de '{id_ou_url[:60]}'")
        return res

    def salvar_metadados_pncp(self, id_ou_url: str, meta: dict, ttl: Optional[int] = None) -> bool:
        """Salva o pacote de metadados coletados da API e Scrapling."""
        key = self._format_key("meta", id_ou_url)
        return self.set_json(key, meta, ttl=ttl)

    def obter_metadados_pncp(self, id_ou_url: str) -> Optional[dict]:
        """Obtém o pacote de metadados da API e Scrapling."""
        key = self._format_key("meta", id_ou_url)
        return self.get_json(key)

    def salvar_busca_editais(self, uf: str, termo: str, editais: list, ttl: Optional[int] = 3600 * 6) -> bool:
        """Salva no Redis o resultado de uma busca por UF e termo (TTL padrão: 6 horas)."""
        clean_termo = termo.strip().lower() if termo else "todos"
        key = self._format_key("search", f"{uf.upper()}:{clean_termo}")
        return self.set_json(key, editais, ttl=ttl)

    def obter_busca_editais(self, uf: str, termo: str) -> Optional[list]:
        """Recupera do Redis o resultado de uma busca recente por UF e termo."""
        clean_termo = termo.strip().lower() if termo else "todos"
        key = self._format_key("search", f"{uf.upper()}:{clean_termo}")
        return self.get_json(key)

    def salvar_edital_cache(self, pncp_id: str, edital_data: dict, ttl: Optional[int] = None) -> bool:
        """Salva edital individual no Redis para acesso instantâneo."""
        key = self._format_key("edital", pncp_id)
        return self.set_json(key, edital_data, ttl=ttl)

    def obter_edital_cache(self, pncp_id: str) -> Optional[dict]:
        """Obtém edital individual do Redis."""
        key = self._format_key("edital", pncp_id)
        return self.get_json(key)


def get_redis_cache() -> RedisCache:
    """Retorna a instância singleton do RedisCache."""
    return RedisCache()
