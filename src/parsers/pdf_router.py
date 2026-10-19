import pymupdf as fitz
import io
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.parsers.paddle_ocr_worker import PaddleOCRWorker

logger = logging.getLogger(__name__)

class PDFPageContent:
    def __init__(self, page_number: int, text: str, is_scanned: bool = False, tables_md: str = ""):
        self.page_number = page_number
        self.text = text
        self.is_scanned = is_scanned
        self.tables_md = tables_md

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "is_scanned": self.is_scanned,
            "tables_md": self.tables_md
        }

class PDFRouter:
    """
    Roteador de documentos PDF página a página.
    Se a página possui texto vetorial nativo (PyMuPDF >= 50 caracteres), extrai em milissegundos.
    Se a página é escaneada (< 50 caracteres), renderiza imagem da página e aciona PaddleOCR.
    """
    def __init__(self, min_char_threshold: int = 50):
        self.min_char_threshold = min_char_threshold
        self.ocr_worker = PaddleOCRWorker()

    def processar_pdf(self, pdf_path: Path) -> List[PDFPageContent]:
        paginas: List[PDFPageContent] = []
        if not pdf_path.exists():
            logger.error(f"Arquivo PDF não encontrado: {pdf_path}")
            return paginas

        try:
            doc = fitz.open(str(pdf_path))
            total_pages = len(doc)
            logger.info(f"Processando PDF {pdf_path.name} com {total_pages} páginas...")

            for page_idx in range(total_pages):
                page_num = page_idx + 1
                page = doc[page_idx]
                raw_text = page.get_text("text").strip()

                # Verifica se há texto nativo suficiente
                if len(raw_text) >= self.min_char_threshold:
                    # Extração instantânea nativa
                    # Também tentamos extrair tabelas nativas do PyMuPDF se disponíveis
                    tables_text = ""
                    try:
                        tabs = page.find_tables()
                        if tabs and len(tabs.tables) > 0:
                            for tab in tabs:
                                df = tab.extract()
                                if df:
                                    header = df[0]
                                    rows = df[1:]
                                    md_header = "| " + " | ".join(str(h or "") for h in header) + " |"
                                    md_sep = "| " + " | ".join("---" for _ in header) + " |"
                                    md_rows = ["| " + " | ".join(str(c or "") for c in r) + " |" for r in rows]
                                    tables_text += f"\n{md_header}\n{md_sep}\n" + "\n".join(md_rows) + "\n"
                    except Exception:
                        pass

                    paginas.append(PDFPageContent(
                        page_number=page_num,
                        text=raw_text,
                        is_scanned=False,
                        tables_md=tables_text
                    ))
                else:
                    # Página escaneada ou baseada em imagem
                    logger.info(f"Página {page_num} com texto esparso ({len(raw_text)} chars). Acionando OCR...")
                    # Renderiza imagem da página em DPI 200 para qualidade de OCR
                    pix = page.get_pixmap(dpi=200)
                    img_bytes = pix.tobytes("png")

                    ocr_text = self.ocr_worker.extrair_texto_imagem(img_bytes)
                    tables_md = self.ocr_worker.extrair_tabelas_markdown(img_bytes)

                    final_text = ocr_text if ocr_text else raw_text
                    paginas.append(PDFPageContent(
                        page_number=page_num,
                        text=final_text,
                        is_scanned=True,
                        tables_md=tables_md
                    ))

            doc.close()
            return paginas

        except Exception as e:
            logger.error(f"Erro ao processar PDF {pdf_path}: {e}")
            return paginas

    def extrair_texto_unificado_com_citacoes(self, paginas: List[PDFPageContent]) -> str:
        """Gera um texto unificado estruturado com marcadores de página explícitos [Pág. X]."""
        blocos = []
        for p in paginas:
            bloco = f"--- [Pág. {p.page_number}] ---\n{p.text}"
            if p.tables_md:
                bloco += f"\n[Tabelas Identificadas na Pág. {p.page_number}]:\n{p.tables_md}"
            blocos.append(bloco)
        return "\n\n".join(blocos)
