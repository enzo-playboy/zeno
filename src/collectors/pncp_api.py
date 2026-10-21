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
    BASE_URL_SEARCH = "https://pncp.gov.br/api/search"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://pncp.gov.br/app/editais"
        }

    def buscar_editais_search(
        self,
        termo: str = "",
        uf: Optional[str] = None,
        status: str = "recebendo_proposta",
        pagina: int = 1,
        tam_pagina: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Consulta direta ao endpoint de busca indexada pública do PNCP (/api/search/).
        Mecanismo de alta performance idêntico ao utilizado pelo actor Apify da Brasil Dados.
        Permite busca em tempo real por palavra-chave (ex: 'armas', 'software') por UF ou nacional.
        """
        url = f"{self.BASE_URL_SEARCH}/"
        params = {
            "tipos_documento": "edital",
            "status": status,
            "pagina": pagina,
            "tam_pagina": min(tam_pagina, 50),
            "ordenacao": "-data"
        }
        if termo and termo.strip():
            params["q"] = termo.strip()
        if uf and uf.strip():
            params["ufs"] = uf.strip().upper()

        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                items = data.get("items", []) or []
                logger.info(f"PNCP Search retornou {len(items)} editais para q='{termo}' uf={uf} (Total indexado: {data.get('total')})")
                return items
        except Exception as e:
            logger.error(f"Erro ao consultar PNCP /api/search/: {e}")
            return []

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
        # PNCP exige que dataFinal seja maior ou igual à data atual (propostas em aberto)
        data_fim = (hoje + timedelta(days=max(30, dias_retroativos))).strftime("%Y%m%d")
        
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
                filtros_pos = [p.lower().strip() for p in (palavras_chave or []) if p.strip()]
                filtros_neg = [n.lower().strip() for n in (palavras_negativas or settings.negative_keywords_list) if n.strip()]

                editais_filtrados = []
                for item in itens:
                    objeto = (item.get("objetoCompra") or "").lower()
                    info_comp = (item.get("informacaoComplementar") or "").lower()
                    texto_completo = f"{objeto} {info_comp}"

                    # Checar negativas primeiro
                    tem_negativa = any(neg in texto_completo for neg in filtros_neg)
                    if tem_negativa:
                        continue

                    # Se houver lista positiva, verificar match estrito
                    if filtros_pos:
                        tem_positiva = any(pos in texto_completo for pos in filtros_pos)
                        if not tem_positiva:
                            continue
                    
                    editais_filtrados.append(item)

                return editais_filtrados

        except httpx.HTTPError as exc:
            logger.error(f"Erro ao consultar API do PNCP ({url}): {exc}")
            return []

    def obter_arquivos_edital(self, cnpj: str, ano: int, sequencial: int, timeout: float = 8.0) -> List[Dict[str, Any]]:
        """
        Recupera a lista de anexos e documentos oficiais do edital no PNCP.
        """
        cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
        url = f"{self.BASE_URL_PNCP}/orgaos/{cnpj_limpo}/compras/{ano}/{sequencial}/arquivos"
        try:
            with httpx.Client(timeout=timeout, headers=self.headers) as client:
                response = client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"Arquivos não localizados no PNCP para {cnpj_limpo}/{ano}/{sequencial} (Status: {response.status_code})")
                return []
        except Exception as exc:
            logger.warning(f"Aviso ao obter arquivos do edital {cnpj_limpo}/{ano}/{sequencial}: {exc}")
            return []

    def obter_itens_edital(self, cnpj: str, ano: int, sequencial: int, pagina: int = 1, timeout: float = 8.0) -> List[Dict[str, Any]]:
        """
        Recupera a lista de lotes e itens registrados oficialmente no PNCP.
        """
        cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
        url = f"{self.BASE_URL_PNCP}/orgaos/{cnpj_limpo}/compras/{ano}/{sequencial}/itens"
        params = {"pagina": pagina, "tamanhoPagina": 100}
        try:
            with httpx.Client(timeout=timeout, headers=self.headers) as client:
                response = client.get(url, params=params)
                if response.status_code == 200:
                    res_data = response.json()
                    return res_data if isinstance(res_data, list) else res_data.get("data", [])
                return []
        except Exception as exc:
            logger.warning(f"Aviso ao obter itens do edital {cnpj_limpo}/{ano}/{sequencial}: {exc}")
            return []

    def obter_compra_detalhes(self, cnpj: str, ano: int, sequencial: int, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
        """
        Recupera os detalhes completos da compra/edital no endpoint oficial do PNCP:
        /orgaos/{cnpj}/compras/{ano}/{sequencial}
        """
        cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
        url = f"{self.BASE_URL_PNCP}/orgaos/{cnpj_limpo}/compras/{ano}/{sequencial}"
        try:
            with httpx.Client(timeout=timeout, headers=self.headers) as client:
                response = client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"Detalhes da compra não localizados para {cnpj_limpo}/{ano}/{sequencial} (Status: {response.status_code})")
                return None
        except Exception as exc:
            logger.warning(f"Aviso ao obter detalhes da compra {cnpj_limpo}/{ano}/{sequencial}: {exc}")
            return None

    def consultar_por_numero_controle(self, numero_controle: str) -> Optional[Dict[str, Any]]:
        """
        Consulta um edital específico pelo número de controle PNCP usando o endpoint de busca pública.
        """
        num_limpo = numero_controle.strip()
        items = self.buscar_editais_search(termo=num_limpo, tam_pagina=5)
        for it in items:
            it_num = it.get("numeroControlePNCP") or it.get("numero_controle_pncp") or ""
            if num_limpo in it_num or it_num in num_limpo:
                return it
        return items[0] if items else None


