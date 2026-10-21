import logging
import httpx
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from src.config import settings

logger = logging.getLogger("ApifyCollector")


class ApifyClient:
    """
    Cliente oficial de consultas via Apify.
    Responsável por realizar todas as buscas e consultas de editais/licitações
    através de Actors ou Datasets hospedados na plataforma Apify.
    """
    BASE_API_URL = "https://api.apify.com/v2"

    def __init__(
        self,
        api_token: Optional[str] = None,
        actor_id: Optional[str] = None,
        timeout: float = 60.0
    ):
        self.api_token = api_token or getattr(settings, "APIFY_API_TOKEN", "")
        self.actor_id = actor_id or getattr(settings, "APIFY_ACTOR_ID", "brasildados~editais-licitacoes-brasil")
        self.timeout = timeout
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        if self.api_token:
            self.headers["Authorization"] = f"Bearer {self.api_token}"

    def is_configured(self) -> bool:
        """Verifica se as credenciais do Apify estão definidas."""
        return bool(self.api_token)

    def consultar_editais(
        self,
        termo: str = "",
        uf: Optional[str] = None,
        status: str = "recebendo_proposta",
        pagina: int = 1,
        tam_pagina: int = 20,
        dias_retroativos: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Executa a consulta de editais via Actor do Apify.
        Dispara o Actor de forma síncrona ou consulta dataset gerado.
        """
        if not self.is_configured():
            logger.warning("[Apify] APIFY_API_TOKEN não configurado. Utilizando fallback direto...")
            from src.collectors.pncp_api import PNCPClient
            pncp = PNCPClient()
            if termo:
                return pncp.buscar_editais_search(termo=termo, uf=uf, status=status, pagina=pagina, tam_pagina=tam_pagina)
            else:
                return pncp.buscar_contratacoes_abertas(uf=uf or settings.TARGET_UF, dias_retroativos=dias_retroativos, pagina=pagina, tamanho_pagina=tam_pagina)

        # Formata o identificador do actor (troca barra por til se necessário para a URL da API)
        actor_endpoint_id = self.actor_id.replace("/", "~")
        url = f"{self.BASE_API_URL}/acts/{actor_endpoint_id}/run-sync-get-dataset-items"

        params = {
            "token": self.api_token,
            "format": "json"
        }

        # Payload com conformidade estrita ao schema do Actor brasildados/editais-licitacoes-brasil
        palavra_busca = termo.strip() if termo else (settings.target_keywords_list[0] if settings.target_keywords_list else "informatica")
        uf_alvo = (uf or settings.TARGET_UF).upper().strip()
        
        payload: Dict[str, Any] = {}
        if palavra_busca:
            payload["palavraChave"] = palavra_busca
        
        # O actor aceita apenas UFs válidas brasileiras
        ufs_validas = {
            "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
            "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
            "RS", "RO", "RR", "SC", "SP", "SE", "TO"
        }
        if uf_alvo in ufs_validas:
            payload["uf"] = uf_alvo

        payload["maxResultados"] = min(max(1, tam_pagina), 100)

        try:
            logger.info(f"[Apify] Disparando consulta no Actor '{self.actor_id}' (palavraChave='{palavra_busca}', uf='{uf_alvo}')...")
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                resp = client.post(url, params=params, json=payload)
                resp.raise_for_status()
                itens = resp.json()

                if isinstance(itens, dict):
                    # Se vier encapsulado em chave como 'items' ou 'data'
                    itens = itens.get("items") or itens.get("data") or [itens]

                logger.info(f"[Apify] Consulta concluída com sucesso. Itens retornados: {len(itens)}")
                return self._normalizar_itens(itens, fallback_uf=uf_alvo)

        except Exception as exc:
            logger.error(f"[Apify] Falha na consulta via Apify ({self.actor_id}): {exc}. Ativando fallback de segurança...")
            from src.collectors.pncp_api import PNCPClient
            pncp = PNCPClient()
            if termo:
                return pncp.buscar_editais_search(termo=termo, uf=uf, status=status, pagina=pagina, tam_pagina=tam_pagina)
            else:
                return pncp.buscar_contratacoes_abertas(uf=uf or settings.TARGET_UF, dias_retroativos=dias_retroativos, pagina=pagina, tamanho_pagina=tam_pagina)

    def _normalizar_itens(self, itens: List[Dict[str, Any]], fallback_uf: str = "MT") -> List[Dict[str, Any]]:
        """
        Padroniza os registros vindos do dataset do Apify para o formato canônico do Zeno.
        Mapeia os campos oficiais do Actor 'brasildados/editais-licitacoes-brasil'.
        """
        normalizados = []
        for item in itens:
            # Reconhece variações do actor da Brasil Dados e genéricas
            num_pncp = (
                item.get("referenciaOficial") or
                item.get("numeroControlePNCP") or
                item.get("numero_controle_pncp") or
                item.get("referencia") or
                item.get("pncpId") or
                item.get("id") or
                ""
            )
            objeto = (
                item.get("objeto") or
                item.get("objetoCompra") or
                item.get("titulo") or
                item.get("description") or
                item.get("title") or
                ""
            )
            orgao = item.get("orgaoEntidade") or {}
            if isinstance(orgao, str):
                razao_social = orgao
                cnpj_orgao = item.get("orgaoCnpj") or ""
            elif isinstance(orgao, dict) and orgao:
                razao_social = orgao.get("razaoSocial") or item.get("orgaoNome") or "Órgão Público"
                cnpj_orgao = orgao.get("cnpj") or item.get("orgaoCnpj") or ""
            else:
                razao_social = item.get("orgaoNome") or "Órgão Público"
                cnpj_orgao = item.get("orgaoCnpjFormatado") or item.get("orgaoCnpj") or ""

            # Valor estimado
            val_raw = item.get("valorEstimado") or item.get("valorTotalEstimado") or item.get("valor_total_estimado") or 0.0
            try:
                val_float = float(val_raw) if val_raw is not None else 0.0
            except (ValueError, TypeError):
                val_float = 0.0

            normalizado = {
                "numeroControlePNCP": str(num_pncp),
                "objetoCompra": objeto,
                "informacaoComplementar": item.get("informacaoComplementar") or item.get("informacao_complementar") or item.get("situacao") or "",
                "valorTotalEstimado": val_float,
                "modalidadeNome": item.get("modalidade") or item.get("modalidadeNome") or item.get("modalidade_licitacao_nome") or "PREGÃO ELETRÔNICO",
                "orgaoEntidade": {
                    "razaoSocial": razao_social,
                    "cnpj": cnpj_orgao
                },
                "anoCompra": int(item.get("ano") or item.get("anoCompra") or datetime.now().year),
                "sequencialCompra": int(item.get("numeroSequencial") or item.get("sequencialCompra") or item.get("numero_sequencial") or 1),
                "dataAberturaProposta": item.get("propostaAbreEm") or item.get("dataAberturaProposta") or item.get("data_inicio_vigencia") or "",
                "dataEncerramentoProposta": item.get("propostaFechaEm") or item.get("dataEncerramentoProposta") or item.get("data_fim_vigencia") or "",
                "uf": item.get("uf") or fallback_uf,
                "municipioNome": item.get("municipio") or item.get("municipioNome") or item.get("municipio_nome") or "",
                "url_oficial": item.get("urlOficial") or item.get("linkSistemaOrigem") or item.get("url") or "",
                "linkSistemaOrigem": item.get("urlOficial") or item.get("linkSistemaOrigem") or item.get("url") or "",
                "origem_consulta": "apify"
            }
            normalizados.append(normalizado)
        return normalizados
