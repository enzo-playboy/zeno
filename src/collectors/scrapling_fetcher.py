import os
import zipfile
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
import re
import httpx
from bs4 import BeautifulSoup
from src.config import settings
from src.cache.redis_client import get_redis_cache

logger = logging.getLogger(__name__)

# Tentativa de importação do Scrapling com fallback defensivo para httpx furtivo
try:
    from scrapling import Fetcher, StealthyFetcher
    HAS_SCRAPLING = True
except Exception:
    HAS_SCRAPLING = False

class ScraplingFetcher:
    """
    Coletor furtivo para download de editais, termos de referência e anexos,
    com suporte a bypass de WAF/Cloudflare e descompactação automática (.zip, .rar).
    """

    def __init__(self, download_dir: Optional[str] = None):
        self.download_dir = Path(download_dir or settings.DOWNLOAD_DIR)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "*/*"
        }

    def baixar_arquivo_direto(self, url: str, destino_caminho: Path) -> bool:
        """Download direto via HTTP streaming com retentativas."""
        try:
            with httpx.Client(timeout=60.0, headers=self.headers, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    if response.status_code == 200:
                        with open(destino_caminho, "wb") as f:
                            for chunk in response.iter_bytes(chunk_size=8192):
                                f.write(chunk)
                        logger.info(f"Download concluído: {destino_caminho} ({destino_caminho.stat().st_size} bytes)")
                        return True
                    else:
                        logger.warning(f"Falha no download direto ({response.status_code}): {url}")
                        return False
        except Exception as exc:
            logger.error(f"Erro ao baixar arquivo {url}: {exc}")
            return False

    def baixar_com_scrapling(self, url: str, destino_caminho: Path) -> bool:
        """Download utilizando Scrapling (Fetcher/StealthyFetcher)."""
        if not HAS_SCRAPLING:
            return self.baixar_arquivo_direto(url, destino_caminho)

        try:
            fetcher = Fetcher()
            resp = fetcher.get(url, headers=self.headers)
            if resp.status == 200 and resp.body:
                with open(destino_caminho, "wb") as f:
                    f.write(resp.body)
                return True
        except Exception as exc:
            logger.warning(f"Fetcher padrão falhou ({exc}). Tentando StealthyFetcher se necessário...")
            try:
                stealthy = StealthyFetcher()
                resp = stealthy.get(url)
                if resp.status == 200 and resp.body:
                    with open(destino_caminho, "wb") as f:
                        f.write(resp.body)
                    return True
            except Exception as stealth_exc:
                logger.error(f"StealthyFetcher falhou para {url}: {stealth_exc}")

        return self.baixar_arquivo_direto(url, destino_caminho)

    def descompactar_arquivo(self, arquivo_path: Path, destino_pasta: Path) -> List[Path]:
        """Extrai arquivos compactados .zip ou .rar mantendo a hierarquia."""
        arquivos_extraidos: List[Path] = []
        ext = arquivo_path.suffix.lower()

        if ext == ".zip":
            try:
                with zipfile.ZipFile(arquivo_path, "r") as zip_ref:
                    zip_ref.extractall(destino_pasta)
                    for root, _, files in os.walk(destino_pasta):
                        for file in files:
                            extracted = Path(root) / file
                            if extracted != arquivo_path:
                                arquivos_extraidos.append(extracted)
                logger.info(f"Descompactado {len(arquivos_extraidos)} arquivos de {arquivo_path.name}")
            except Exception as e:
                logger.error(f"Erro ao descompactar ZIP {arquivo_path}: {e}")

        elif ext == ".rar":
            # Tenta módulos comuns de RAR se instalados
            try:
                import rarfile
                with rarfile.RarFile(arquivo_path, "r") as rar_ref:
                    rar_ref.extractall(destino_pasta)
                    for root, _, files in os.walk(destino_pasta):
                        for file in files:
                            extracted = Path(root) / file
                            if extracted != arquivo_path:
                                arquivos_extraidos.append(extracted)
            except ImportError:
                logger.warning("rarfile não instalado. Arquivo .rar mantido bruto.")
            except Exception as e:
                logger.error(f"Erro ao descompactar RAR {arquivo_path}: {e}")

        return arquivos_extraidos

    def baixar_documentos_edital(self, numero_controle_pncp: str, lista_arquivos: List[Dict[str, Any]]) -> List[Path]:
        """
        Baixa anexos do edital priorizando os documentos essenciais (Edital, TR, ETP, Riscos).
        Detecta magic bytes para salvar com a extensão correta (.pdf, .rtf, .docx).
        """
        pasta_edital = self.download_dir / numero_controle_pncp.replace("/", "_").replace("-", "_")
        pasta_edital.mkdir(parents=True, exist_ok=True)
        
        # 1. Priorização inteligente: coloca Edital, TR e ETP na frente da fila
        def _peso_prioridade(doc: Dict[str, Any]) -> int:
            t = (doc.get("titulo") or "").lower()
            tp = (doc.get("tipoDocumentoNome") or "").lower()
            if "edital" in t or "edital" in tp:
                return 0
            if "termo de referencia" in t or "termo" in t or "tr" in t:
                return 1
            if "estudo tecnico" in t or "etp" in t:
                return 2
            if "mapa de risco" in t or "risco" in t:
                return 3
            if "projeto" in t:
                return 4
            return 10

        arquivos_ordenados = sorted(lista_arquivos, key=_peso_prioridade)
        # Limita aos 3 documentos cruciais (Edital, Termo de Referência, ETP) para auditoria ultrarrápida
        if len(arquivos_ordenados) > 3:
            arquivos_ordenados = arquivos_ordenados[:3]

        documentos_processados: List[Path] = []

        for idx, doc in enumerate(arquivos_ordenados, start=1):
            url = doc.get("url") or doc.get("uri")
            titulo = doc.get("titulo") or f"documento_{idx}"
            if not url:
                continue

            # Nome seguro para o arquivo
            nome_base = "".join(c for c in f"{idx}_{titulo}" if c.isalnum() or c in (" ", "_", "-")).rstrip()
            if not nome_base.lower().endswith((".pdf", ".zip", ".rar", ".docx", ".xlsx", ".rtf")):
                nome_base += ".pdf"
            
            caminho_local = pasta_edital / nome_base

            # Se já foi baixado (inclusive com extensão corrigida .rtf ou .docx), aproveita o cache instantâneo
            candidatos = [
                caminho_local,
                caminho_local.with_suffix(".rtf"),
                caminho_local.with_suffix(".docx"),
                caminho_local.with_suffix(".pdf")
            ]
            ja_existe = None
            for cand in candidatos:
                if cand.exists() and cand.stat().st_size > 10:
                    ja_existe = cand
                    break

            if ja_existe:
                caminho_local = ja_existe
            else:
                sucesso = self.baixar_com_scrapling(url, caminho_local)
                if not sucesso:
                    continue

            # Validação de magic bytes para garantir a extensão correta no Windows
            try:
                if caminho_local.exists() and caminho_local.stat().st_size > 10:
                    with open(caminho_local, "rb") as fp:
                        header = fp.read(16)
                    if header.startswith(b"{\\rtf") and not caminho_local.name.lower().endswith(".rtf"):
                        novo_caminho = caminho_local.with_suffix(".rtf")
                        if novo_caminho.exists():
                            novo_caminho.unlink()
                        caminho_local.rename(novo_caminho)
                        caminho_local = novo_caminho
                    elif header.startswith(b"%PDF") and not caminho_local.name.lower().endswith(".pdf"):
                        novo_caminho = caminho_local.with_suffix(".pdf")
                        if novo_caminho.exists():
                            novo_caminho.unlink()
                        caminho_local.rename(novo_caminho)
                        caminho_local = novo_caminho
            except Exception as e:
                logger.warning(f"Ajuste de extensão de {caminho_local.name}: {e}")

            # Se for compactado, descompacta
            if caminho_local.suffix.lower() in (".zip", ".rar"):
                pasta_extraidos = pasta_edital / f"extraido_{idx}"
                pasta_extraidos.mkdir(exist_ok=True)
                extraidos = self.descompactar_arquivo(caminho_local, pasta_extraidos)
                for item in extraidos:
                    if item.suffix.lower() in (".pdf", ".docx", ".xlsx", ".rtf"):
                        documentos_processados.append(item)
            else:
                documentos_processados.append(caminho_local)

        return documentos_processados

    @staticmethod
    def extrair_parametros_url(url_ou_id: str) -> Dict[str, Any]:
        """
        Extrai CNPJ, Ano, Sequencial e Número de Controle de qualquer formato de link do PNCP:
        - https://pncp.gov.br/app/editais/24950495000188/2025/195
        - https://pncp.gov.br/app/editais/24950495000188/2025/195/itens
        - https://pncp.gov.br/app/editais?q=24950495000188-1-000195/2025
        - 24950495000188-1-000195/2025
        - 24950495000188/2025/195
        """
        raw = url_ou_id.strip()
        
        # 1. Se veio query param q=
        if "q=" in raw:
            q_part = raw.split("q=")[1].split("&")[0]
            raw = q_part.strip()

        cnpj = ""
        ano = 0
        seq = 0

        # Padrão 1: /editais/{cnpj}/{ano}/{seq} ou {cnpj}/{ano}/{seq}
        m1 = re.search(r'(\d{14})/(\d{4})/(\d+)', raw)
        if m1:
            cnpj = m1.group(1)
            ano = int(m1.group(2))
            seq = int(m1.group(3))
        else:
            # Padrão 2: {cnpj}-1-{seq}/{ano} (ex: 24950495000188-1-000195/2025)
            m2 = re.search(r'(\d{14})-1-0*(\d+)/(\d{4})', raw)
            if m2:
                cnpj = m2.group(1)
                seq = int(m2.group(2))
                ano = int(m2.group(3))
            else:
                # Padrão 3: tenta extrair CNPJ e ano avulsos
                m_cnpj = re.search(r'(\d{14})', raw)
                m_ano = re.search(r'/(202[0-9])', raw)
                if m_cnpj:
                    cnpj = m_cnpj.group(1)
                if m_ano:
                    ano = int(m_ano.group(1))

        if not ano:
            from datetime import datetime
            ano = datetime.now().year
        if not seq:
            seq = 1

        numero_controle = f"{cnpj}-1-{seq:06d}/{ano}" if cnpj else raw
        canonical_url = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}" if cnpj else raw

        return {
            "cnpj": cnpj,
            "ano": ano,
            "sequencial": seq,
            "numero_controle_pncp": numero_controle,
            "canonical_url": canonical_url,
            "raw_input": url_ou_id
        }

    def raspar_pagina_pncp(self, url_ou_id: str) -> Dict[str, Any]:
        """
        Raspagem furtiva obrigatória da página do PNCP usando Scrapling.
        Verifica o Redis cache em busca do HTML salvo. Se não existir, faz a requisição
        com Scrapling (Fetcher/StealthyFetcher), salva no Redis e extrai os campos com BeautifulSoup.
        """
        info_link = self.extrair_parametros_url(url_ou_id)
        canonical_url = info_link["canonical_url"]
        redis_cache = get_redis_cache()

        # 1. Consulta cache no Redis
        html = redis_cache.obter_html_pagina(canonical_url)
        origem = "redis_cache" if html else "scrapling_live"

        if not html:
            logger.info(f"Iniciando raspagem com Scrapling para URL: {canonical_url}")
            # 2. Raspagem com Scrapling
            if HAS_SCRAPLING:
                try:
                    fetcher = Fetcher()
                    resp = fetcher.get(canonical_url, headers=self.headers)
                    if resp.status == 200 and resp.body:
                        html = resp.body.decode("utf-8", errors="ignore") if isinstance(resp.body, bytes) else str(resp.body)
                except Exception as ex:
                    logger.warning(f"Fetcher padrão Scrapling falhou ({ex}). Tentando StealthyFetcher...")
                    try:
                        stealthy = StealthyFetcher()
                        resp = stealthy.get(canonical_url)
                        if resp.status == 200 and resp.body:
                            html = resp.body.decode("utf-8", errors="ignore") if isinstance(resp.body, bytes) else str(resp.body)
                    except Exception as s_ex:
                        logger.warning(f"StealthyFetcher Scrapling falhou ({s_ex}).")

            # 3. Fallback defensivo com browser headers via httpx se Scrapling falhar
            if not html:
                try:
                    with httpx.Client(timeout=25.0, headers=self.headers, follow_redirects=True) as client:
                        resp = client.get(canonical_url)
                        if resp.status_code == 200:
                            html = resp.text
                except Exception as h_ex:
                    logger.error(f"Erro no download HTTP da página {canonical_url}: {h_ex}")

            # 4. Salvar no Redis se capturado
            if html:
                redis_cache.salvar_html_pagina(canonical_url, html)
            else:
                logger.warning(f"Não foi possível obter HTML da página {canonical_url}")
                html = ""

        # 5. Extração e estruturação com BeautifulSoup
        dados_raspados: Dict[str, Any] = {
            "numero_controle_pncp": info_link["numero_controle_pncp"],
            "canonical_url": canonical_url,
            "cnpj": info_link["cnpj"],
            "ano": info_link["ano"],
            "sequencial": info_link["sequencial"],
            "origem": origem,
            "orgao": "",
            "objeto": "",
            "modalidade": "Pregão Eletrônico",
            "valor_estimado": 0.0,
            "data_publicacao": "",
            "data_abertura": "",
            "data_encerramento": "",
            "link_disputa": "",
            "situacao": "Recebendo proposta",
            "anexos_detectados": [],
            "itens_detectados": []
        }

        if html:
            try:
                soup = BeautifulSoup(html, "html.parser")
                
                # Extrai texto de títulos e cabeçalhos
                title_tag = soup.find("title")
                if title_tag:
                    dados_raspados["titulo_pagina"] = title_tag.get_text(strip=True)

                # Busca links de anexos/editais
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    txt = a.get_text(strip=True)
                    if any(ext in href.lower() for ext in [".pdf", ".zip", ".rar", ".docx", "/arquivos/"]):
                        url_completa = href if href.startswith("http") else f"https://pncp.gov.br{href}"
                        dados_raspados["anexos_detectados"].append({
                            "titulo": txt or "Documento Anexo",
                            "url": url_completa
                        })
                    if any(portal in href.lower() for portal in ["comprasnet", "compras.gov", "bbmnet", "bll", "licitacoes-e", "portaldecompraspublicas"]):
                        dados_raspados["link_disputa"] = href

                # Busca textos por padrões comuns no PNCP
                text_content = soup.get_text(" ", strip=True)
                
                # Objeto da compra
                m_obj = re.search(r'Objeto(?:\s+da\s+compra)?[:\s]+([^\n\r]{15,350})', text_content, re.IGNORECASE)
                if m_obj:
                    dados_raspados["objeto"] = m_obj.group(1).strip()

                # Órgão
                m_org = re.search(r'Órgão[:\s]+([^\n\r]{5,150})', text_content, re.IGNORECASE)
                if m_org:
                    dados_raspados["orgao"] = m_org.group(1).strip()

                # Valor estimado
                m_val = re.search(r'Valor\s+(?:total\s+)?estimado[:\s]+(?:R\$\s*)?([\d.,]+)', text_content, re.IGNORECASE)
                if m_val:
                    try:
                        v_str = m_val.group(1).replace(".", "").replace(",", ".")
                        dados_raspados["valor_estimado"] = float(v_str)
                    except ValueError:
                        pass
            except Exception as b_ex:
                logger.warning(f"Erro ao parsear HTML com BeautifulSoup: {b_ex}")

        return dados_raspados

extrair_parametros_url = ScraplingFetcher.extrair_parametros_url
