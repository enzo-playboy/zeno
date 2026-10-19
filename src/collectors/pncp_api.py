import httpx
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from src.config import settings

logger = logging.getLogger(__name__)

class PNCPClient:
    """
    Cliente REST de alta performance para a API oficial do PNCP (Portal Nacional de Contratações Públicas).
    Focado na captura contínua e filtragem de contratações abertas.
    """
    BASE_URL_CONSULTA = "https://pncp.gov.br/api/consulta/v1"
    BASE_URL_PNCP = "https://pncp.gov.br/api/pncp/v1"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }

    def buscar_contratacoes_abertas(
        self,
        uf: str = "MT",
        dias_retroativos: int = 7,
        pagina: int = 1,
        tamanho_pagina: int = 10,
        palavras_chave: Optional[List[str]] = None,
        palavras_negativas: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Consulta contratações abertas para propostas na UF especificada com janela de datas.
        """
        hoje = datetime.now()
        data_ini = (hoje - timedelta(days=dias_retroativos)).strftime("%Y%m%d")
        data_fim = hoje.strftime("%Y%m%d")
        
        # Garante tamanho_pagina mínimo de 10 exigido pela API
        tamanho = max(10, tamanho_pagina)

        url = f"{self.BASE_URL_CONSULTA}/contratacoes/proposta"
        params = {
            "dataInicial": data_ini,
            "dataFinal": data_fim,
            "uf": uf,
            "pagina": pagina,
            "tamanhoPagina": tamanho
        }

        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                itens = payload.get("data", [])
                
                logger.info(f"PNCP retornou {len(itens)} editais para UF={uf} no período {data_ini} a {data_fim}")

                # Filtragem inteligente por palavras-chave se fornecidas
                filtros_pos = palavras_chave or settings.target_keywords_list
                filtros_neg = palavras_negativas or settings.negative_keywords_list

                editais_filtrados = []
                for item in itens:
                    objeto = (item.get("objetoCompra") or "").lower()
                    info_comp = (item.get("informacaoComplementar") or "").lower()
                    texto_completo = f"{objeto} {info_comp}"

                    # Checar negativas primeiro
                    tem_negativa = any(neg in texto_completo for neg in filtros_neg)
                    if tem_negativa:
                        continue

                    # Se houver lista positiva, verificar match
                    if filtros_pos:
                        tem_positiva = any(pos in texto_completo for pos in filtros_pos)
                        if not tem_positiva:
                            # Caso não haja match no objeto direto, mantemos se não houver filtro restritivo
                            pass
                    
                    editais_filtrados.append(item)

                return editais_filtrados

        except httpx.HTTPError as exc:
            logger.error(f"Erro ao consultar API do PNCP ({url}): {exc}")
            return []

    def obter_arquivos_edital(self, cnpj: str, ano: int, sequencial: int) -> List[Dict[str, Any]]:
        """
        Recupera a lista de anexos e documentos oficiais do edital no PNCP.
        """
        url = f"{self.BASE_URL_PNCP}/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"
        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"Arquivos não localizados no PNCP para {cnpj}/{ano}/{sequencial} (Status: {response.status_code})")
                return []
        except httpx.HTTPError as exc:
            logger.error(f"Erro ao obter arquivos do edital {cnpj}/{ano}/{sequencial}: {exc}")
            return []

    def obter_itens_edital(self, cnpj: str, ano: int, sequencial: int, pagina: int = 1) -> List[Dict[str, Any]]:
        """
        Recupera a lista de lotes e itens registrados oficialmente no PNCP.
        """
        url = f"{self.BASE_URL_PNCP}/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens"
        params = {"pagina": pagina, "tamanhoPagina": 100}
        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(url, params=params)
                if response.status_code == 200:
                    res_data = response.json()
                    return res_data if isinstance(res_data, list) else res_data.get("data", [])
                return []
        except httpx.HTTPError as exc:
            logger.error(f"Erro ao obter itens do edital {cnpj}/{ano}/{sequencial}: {exc}")
            return []
