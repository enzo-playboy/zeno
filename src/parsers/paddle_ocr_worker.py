import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

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

    def extrair_tabelas_markdown(self, image_bytes_ou_path) -> str:
        """Utiliza PP-Structure para converter tabelas da página escaneada em Markdown."""
        if not HAS_PADDLE:
            return ""

        engine = self._get_table_engine()
        if not engine:
            return ""

        try:
            result = engine(image_bytes_ou_path)
            tabelas_md = []
            for item in result:
                if item.get("type") == "table":
                    res = item.get("res", {})
                    html = res.get("html", "")
                    if html:
                        # Converte HTML da tabela para Markdown básico
                        tabelas_md.append(f"\n```html\n{html}\n```\n")
            return "\n".join(tabelas_md)
        except Exception as e:
            logger.error(f"Erro ao reconstruir tabelas com PP-Structure: {e}")
            return ""
