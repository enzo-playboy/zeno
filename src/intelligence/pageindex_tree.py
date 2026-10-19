import re
import logging
from typing import List, Dict, Any, Optional
from src.parsers.pdf_router import PDFPageContent

logger = logging.getLogger(__name__)

class DocumentNode:
    """Nó da árvore hierárquica do PageIndex (Vectorless RAG)."""
    def __init__(self, titulo: str, nivel: int, pagina_inicio: int, texto: str = ""):
        self.titulo = titulo
        self.nivel = nivel
        self.pagina_inicio = pagina_inicio
        self.pagina_fim = pagina_inicio
        self.texto = texto
        self.subsecoes: List['DocumentNode'] = []
        self.tabelas: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "titulo": self.titulo,
            "nivel": self.nivel,
            "pagina_inicio": self.pagina_inicio,
            "pagina_fim": self.pagina_fim,
            "subsecoes": [s.to_dict() for s in self.subsecoes],
            "tamanho_texto": len(self.texto),
            "qtd_tabelas": len(self.tabelas)
        }

class PageIndexTree:
    """
    Construtor e navegador de árvore hierárquica (Vectorless RAG).
    Permite busca semântica em nível de seção/cláusula sem quebrar
    tabelas de preços e atestados em chunks arbitrários.
    """

    # Padrões comuns de títulos em editais brasileiros
    PADROES_SECOES = [
        r'^(?:CL[ÁA]USULA|[0-9]{1,2}\.|\bANEXO\b|\bTERMO DE REFER[ÊE]NCIA\b|\bCAP[ÍI]TULO\b|\bDO OBJETO\b|\bDA HABILITA[ÇC][ÃA]O\b|\bDAS OBRIGA[ÇC][ÕO]ES\b|\bDAS SAN[ÇC][ÕO]ES\b|\bDOS RECURSOS\b|\bDA PROPOSTA\b|\bDO PAGAMENTO\b)',
    ]

    def __init__(self):
        self.regex_secoes = [re.compile(p, re.IGNORECASE) for p in self.PADROES_SECOES]

    def construir_arvore(self, paginas: List[PDFPageContent]) -> DocumentNode:
        """Cria a árvore estruturada a partir da lista de páginas do PDF."""
        raiz = DocumentNode(titulo="Documento Raiz", nivel=0, pagina_inicio=1)
        no_atual = raiz

        for p in paginas:
            linhas = p.text.split("\n")
            for linha in linhas:
                linha_clean = linha.strip()
                if not linha_clean:
                    continue

                # Detectar se a linha parece um cabeçalho ou cláusula
                e_cabecalho = False
                for reg in self.regex_secoes:
                    if reg.match(linha_clean) and len(linha_clean) < 120:
                        e_cabecalho = True
                        break

                if e_cabecalho:
                    novo_no = DocumentNode(
                        titulo=linha_clean,
                        nivel=1,
                        pagina_inicio=p.page_number,
                        texto=f"[Pág. {p.page_number}] {linha_clean}\n"
                    )
                    raiz.subsecoes.append(novo_no)
                    no_atual = novo_no
                else:
                    no_atual.texto += f"\n{linha_clean}"
                    no_atual.pagina_fim = p.page_number

            if p.tables_md:
                no_atual.tabelas.append(p.tables_md)

        logger.info(f"Árvore PageIndex construída com {len(raiz.subsecoes)} seções principais.")
        return raiz

    def buscar_secoes_por_tema(self, raiz: DocumentNode, palavras_tema: List[str]) -> List[DocumentNode]:
        """Retorna as seções que contêm as palavras-tema (ex: habilitação, prazos, itens)."""
        resultados = []
        palavras_lower = [w.lower() for w in palavras_tema]

        for sec in raiz.subsecoes:
            titulo_lower = sec.titulo.lower()
            if any(p in titulo_lower for p in palavras_lower):
                resultados.append(sec)
            elif any(p in sec.texto.lower()[:500] for p in palavras_lower):
                resultados.append(sec)

        return resultados

    def exportar_sumario(self, raiz: DocumentNode) -> str:
        """Exporta um sumário em Markdown da estrutura do edital."""
        linhas = ["# Sumário Inteligente do Edital (PageIndex Tree)"]
        for s in raiz.subsecoes:
            linhas.append(f"- **{s.titulo}** (Páginas {s.pagina_inicio} a {s.pagina_fim})")
        return "\n".join(linhas)
