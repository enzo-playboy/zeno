import os
import zipfile
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
import httpx
from src.config import settings

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
        Baixa todos os anexos de um edital e descompacta arquivos comprimidos.
        Retorna a lista de arquivos prontos para auditoria (PDFs prioritários).
        """
        pasta_edital = self.download_dir / numero_controle_pncp.replace("/", "_").replace("-", "_")
        pasta_edital.mkdir(parents=True, exist_ok=True)
        
        documentos_processados: List[Path] = []

        for idx, doc in enumerate(lista_arquivos, start=1):
            url = doc.get("url") or doc.get("uri")
            titulo = doc.get("titulo") or f"documento_{idx}"
            if not url:
                continue

            # Nome seguro para o arquivo
            nome_arquivo = "".join(c for c in f"{idx}_{titulo}" if c.isalnum() or c in (" ", "_", "-")).rstrip()
            if not nome_arquivo.lower().endswith((".pdf", ".zip", ".rar", ".docx", ".xlsx")):
                nome_arquivo += ".pdf"
            
            caminho_local = pasta_edital / nome_arquivo

            # Se já foi baixado, aproveita
            if not caminho_local.exists() or caminho_local.stat().st_size == 0:
                sucesso = self.baixar_com_scrapling(url, caminho_local)
                if not sucesso:
                    continue

            # Se for compactado, descompacta
            if caminho_local.suffix.lower() in (".zip", ".rar"):
                pasta_extraidos = pasta_edital / f"extraido_{idx}"
                pasta_extraidos.mkdir(exist_ok=True)
                extraidos = self.descompactar_arquivo(caminho_local, pasta_extraidos)
                for item in extraidos:
                    if item.suffix.lower() in (".pdf", ".docx", ".xlsx"):
                        documentos_processados.append(item)
            else:
                documentos_processados.append(caminho_local)

        return documentos_processados
