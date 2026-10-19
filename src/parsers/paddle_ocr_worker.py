import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

import gc

logger = logging.getLogger(__name__)

# Tentar importar paddleocr defensivamente
HAS_PADDLE = False
try:
    from paddleocr import PaddleOCR, PPStructure
    HAS_PADDLE = True
except Exception:
    HAS_PADDLE = False

class PaddleOCRWorker:
    """
    Worker especializado para páginas escaneadas e tabelas complexas
    utilizando PaddleOCR e PP-Structure para reconstrução de tabelas em Markdown.
    """
    def __init__(self, lang: str = "pt", use_gpu: bool = False):
        self.lang = lang
        self.use_gpu = use_gpu
        self._ocr = None
        self._table_engine = None

    def _get_ocr(self):
        if self._ocr is None and HAS_PADDLE:
            try:
                self._ocr = PaddleOCR(use_angle_cls=True, lang=self.lang, use_gpu=self.use_gpu, show_log=False)
            except Exception as e:
                logger.error(f"Erro ao inicializar PaddleOCR: {e}")
        return self._ocr

    def _get_table_engine(self):
        if self._table_engine is None and HAS_PADDLE:
            try:
                self._table_engine = PPStructure(table=True, ocr=True, show_log=False)
            except Exception as e:
                logger.error(f"Erro ao inicializar PPStructure: {e}")
        return self._table_engine

    def extrair_texto_imagem(self, image_bytes_ou_path) -> str:
        """Extrai texto contínuo de imagem de página escaneada."""
        if not HAS_PADDLE:
            logger.info("PaddleOCR não disponível no ambiente atual. Utilizando fallback textual.")
            return ""

        ocr = self._get_ocr()
        if not ocr:
            return ""

        try:
            result = ocr.ocr(image_bytes_ou_path, cls=True)
            linhas = []
            if result and result[0]:
                for line in result[0]:
                    text = line[1][0]
                    linhas.append(text)
            return "\n".join(linhas)
        except Exception as e:
            logger.error(f"Erro ao executar OCR na imagem: {e}")
            return ""
        finally:
            gc.collect()

    def _fallback_tabela_from_text(self, text: str) -> str:
        """Reconstrói uma tabela Markdown a partir de linhas com indícios de itens/lotes/valores."""
        if not text:
            return ""
        linhas = [l.strip() for l in text.split("\n") if l.strip()]
        itens = []
        for l in linhas:
            l_lower = l.lower()
            if any(k in l_lower for k in ["item", "lote", "qtd", "quantidade", "valor", "r$", "unidade", "descrição", "especificação"]):
                itens.append(l)

        if itens:
            header = "| Item / Especificação / Valor Identificado |"
            sep = "| --- |"
            rows = [f"| {it.replace('|', '/')} |" for it in itens]
            return f"\n{header}\n{sep}\n" + "\n".join(rows) + "\n"
        return ""

    def extrair_tabelas_markdown(self, image_bytes_ou_path, fallback_text: str = "") -> str:
        """Utiliza PP-Structure para converter tabelas da página escaneada em Markdown."""
        tabelas_md = []
        try:
            if HAS_PADDLE:
                engine = self._get_table_engine()
                if engine:
                    result = engine(image_bytes_ou_path)
                    for item in result:
                        if item.get("type") == "table":
                            res = item.get("res", {})
                            html = res.get("html", "")
                            if html:
                                tabelas_md.append(f"\n```html\n{html}\n```\n")
        except Exception as e:
            logger.error(f"Erro ao reconstruir tabelas com PP-Structure: {e}")
        finally:
            gc.collect()

        if tabelas_md:
            return "\n".join(tabelas_md)

        # Fallback inteligente se PPStructure não produziu tabela direta
        if fallback_text:
            return self._fallback_tabela_from_text(fallback_text)

        return ""

