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

        # 1. Tratamento transparente de arquivos RTF (muito comuns em prefeituras no PNCP)
        try:
            with open(pdf_path, "rb") as fp:
                magic = fp.read(16)
        except Exception:
            magic = b""

        if magic.startswith(b"{\\rtf") or pdf_path.suffix.lower() == ".rtf":
            logger.info(f"Processando documento RTF {pdf_path.name}...")
            try:
                with open(pdf_path, "r", encoding="latin1", errors="ignore") as f:
                    raw_rtf = f.read()

                # Extração rápida baseada em pilha de controle RTF (0.5s sem backtracking)
                IGNORED_DESTINATIONS = {
                    'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'shp', 'themedata',
                    'colorschememapping', 'latentstyles', 'datastore', 'xmlopen', 'generator',
                    'xmlnsprefix', 'panose', 'operator', 'creatim', 'revtim'
                }
                out = []
                group_stack = []
                i = 0
                n = len(raw_rtf)

                while i < n:
                    c = raw_rtf[i]
                    if c == '{':
                        i += 1
                        is_ignoring = group_stack[-1] if group_stack else False
                        if not is_ignoring and i < n and raw_rtf[i] == '\\':
                            j = i + 1
                            if j < n and raw_rtf[j] == '*':
                                j += 1
                            if j < n and raw_rtf[j] == '\\':
                                j += 1
                            cmd_start = j
                            while j < n and raw_rtf[j].isalpha():
                                j += 1
                            cmd = raw_rtf[cmd_start:j].lower()
                            if cmd in IGNORED_DESTINATIONS:
                                group_stack.append(True)
                                continue
                        group_stack.append(is_ignoring)
                    elif c == '}':
                        if group_stack:
                            group_stack.pop()
                        i += 1
                    elif c == '\\':
                        i += 1
                        if i >= n:
                            break
                        ch2 = raw_rtf[i]
                        if ch2 in ('\\', '{', '}'):
                            if not (group_stack and group_stack[-1]):
                                out.append(ch2)
                            i += 1
                        elif ch2 == "'":
                            hex_str = raw_rtf[i+1:i+3]
                            i += 3
                            if not (group_stack and group_stack[-1]):
                                try:
                                    b = bytes([int(hex_str, 16)])
                                    out.append(b.decode('cp1252', errors='replace'))
                                except Exception:
                                    pass
                        elif ch2.isalpha() or ch2 == '*':
                            cmd_start = i
                            while i < n and raw_rtf[i].isalpha():
                                i += 1
                            cmd = raw_rtf[cmd_start:i].lower()
                            while i < n and (raw_rtf[i].isdigit() or raw_rtf[i] == '-'):
                                i += 1
                            if i < n and raw_rtf[i] == ' ':
                                i += 1
                            if not (group_stack and group_stack[-1]):
                                if cmd in ('par', 'line'):
                                    out.append('\n')
                                elif cmd == 'tab':
                                    out.append('\t')
                        else:
                            i += 1
                    else:
                        if not (group_stack and group_stack[-1]):
                            if c not in ('\r', '\n'):
                                out.append(c)
                        i += 1

                import re
                clean = ''.join(out)
                clean = re.sub(r'[ \t]+', ' ', clean)
                clean = re.sub(r'\n{3,}', '\n\n', clean).strip()

                # 2. Localiza onde começa o texto útil do edital
                markers = ["EDITAL", "PREGÃO", "MUNICÍPIO", "MUNICIPIO", "PREFEITURA", "CHAMAMENTO", "CREDENCIAMENTO", "TERMO DE REFERÊNCIA", "TERMO DE REFERENCIA", "AVISO"]
                first_pos = len(clean)
                for marker in markers:
                    p = clean.find(marker)
                    if p != -1 and p < first_pos:
                        first_pos = p
                if first_pos < len(clean):
                    clean = clean[first_pos:]

                # Segmenta em blocos de ~2500 caracteres como páginas virtuais
                chunk_size = 2500
                for i in range(0, max(len(clean), 1), chunk_size):
                    chunk = clean[i:i+chunk_size]
                    if chunk.strip():
                        paginas.append(PDFPageContent(
                            page_number=(i // chunk_size) + 1,
                            text=chunk.strip(),
                            is_scanned=False,
                            tables_md=""
                        ))
                return paginas
            except Exception as ex_rtf:
                logger.error(f"Erro ao processar RTF {pdf_path.name}: {ex_rtf}")

        # 2. Tratamento transparente de arquivos DOCX
        if (magic.startswith(b"PK\x03\x04") and pdf_path.suffix.lower() == ".docx") or pdf_path.suffix.lower() == ".docx":
            logger.info(f"Processando documento DOCX {pdf_path.name}...")
            try:
                import zipfile
                import xml.etree.ElementTree as ET
                with zipfile.ZipFile(pdf_path) as docx_zip:
                    if "word/document.xml" in docx_zip.namelist():
                        xml_content = docx_zip.read("word/document.xml")
                        tree = ET.fromstring(xml_content)
                        texts = [node.text for node in tree.iter() if node.tag.endswith("t") and node.text]
                        full_docx = " ".join(texts)
                        chunk_size = 2500
                        for i in range(0, max(len(full_docx), 1), chunk_size):
                            chunk = full_docx[i:i+chunk_size]
                            if chunk.strip():
                                paginas.append(PDFPageContent(
                                    page_number=(i // chunk_size) + 1,
                                    text=chunk.strip(),
                                    is_scanned=False,
                                    tables_md=""
                                ))
                        return paginas
            except Exception as ex_docx:
                logger.error(f"Erro ao processar DOCX {pdf_path.name}: {ex_docx}")

        # 3. Processamento padrão PyMuPDF para arquivos PDF reais
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
                    del pix
                    import gc
                    gc.collect()

                    ocr_text = self.ocr_worker.extrair_texto_imagem(img_bytes)
                    tables_md = self.ocr_worker.extrair_tabelas_markdown(img_bytes, fallback_text=ocr_text or raw_text)
                    if not tables_md and (ocr_text or raw_text):
                        tables_md = self.ocr_worker._fallback_tabela_from_text(ocr_text or raw_text)

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
