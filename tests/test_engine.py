import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.database.models import EditalAuditado, ItemLote
from src.parsers.pdf_router import PDFPageContent
from src.intelligence.pageindex_tree import PageIndexTree
from src.intelligence.react_auditor import AuditorReActAgent

def test_pageindex_and_auditor_contract():
    print("Iniciando teste de conformidade de contrato...")

    # Simular páginas extraídas do edital
    paginas = [
        PDFPageContent(
            page_number=1,
            text="PREFEITURA MUNICIPAL DE CUIABÁ - MT\nEDITAL DE PREGÃO ELETRÔNICO Nº 012/2026\nDO OBJETO: Aquisição de equipamentos de informática e nobreaks.",
            is_scanned=False
        ),
        PDFPageContent(
            page_number=14,
            text="DA HABILITAÇÃO E QUALIFICAÇÃO TÉCNICA\nAtestado de capacidade técnica comprovando fornecimento mínimo de 50% do quantitativo licitado registrado em conselho.",
            is_scanned=False
        ),
        PDFPageContent(
            page_number=31,
            text="DAS OBRIGAÇÕES DA CONTRATADA E PRAZOS\nO prazo de entrega será de 10 dias corridos contados da emissão da Nota de Empenho.",
            is_scanned=False
        )
    ]

    # Construir árvore PageIndex
    tree_engine = PageIndexTree()
    arvore = tree_engine.construir_arvore(paginas)
    assert len(arvore.subsecoes) >= 2, "A árvore deve identificar seções"

    # Executar auditoria ReAct
    auditor = AuditorReActAgent()
    edital_auditado = auditor.auditar_edital(
        raw_pncp_item={
            "numeroControlePNCP": "00000000000100-1-000123/2026",
            "orgaoEntidade": {"razaoSocial": "Prefeitura Municipal de Cuiabá - MT"},
            "modalidadeNome": "Pregão Eletrônico",
            "dataEncerramentoProposta": "2026-11-05T09:00:00",
            "linkSistemaOrigem": "Portal de Compras Públicas",
            "objetoCompra": "Registro de preços para aquisição de equipamentos de informática e nobreaks.",
            "valorTotalEstimado": 485000.00
        },
        arvore_docs=arvore,
        texto_completo="--- [Pág. 1] ---\n" + paginas[0].text + "\n\n--- [Pág. 14] ---\n" + paginas[1].text + "\n\n--- [Pág. 31] ---\n" + paginas[2].text
    )

    print("\n--- JSON GERADO PELO CONTRATO DE SAÍDA ---")
    print(edital_auditado.model_dump_json(indent=2))

    assert edital_auditado.numero_controle_pncp == "00000000000100-1-000123/2026"
    assert any("[Pág. 14]" in ex for ex in edital_auditado.exigencias_habilitacao_criticas), "Deve citar página 14 no atestado"
    assert any("10 dias" in r for r in edital_auditado.armadilhas_e_riscos), "Deve detectar armadilha de prazo curto na página 31"
    print("\n✔ Todos os testes de contrato e citação de páginas [Pág. X] foram aprovados!")

if __name__ == "__main__":
    test_pageindex_and_auditor_contract()
