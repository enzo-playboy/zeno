import argparse
import logging
import sys
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

# Garantir que a raiz do projeto esteja no sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Garantir UTF-8 no Windows Console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.config import settings
from src.database.repository import Repository
from src.collectors.pncp_api import PNCPClient
from src.collectors.apify_client import ApifyClient
from src.collectors.scrapling_fetcher import ScraplingFetcher
from src.parsers.pdf_router import PDFRouter
from src.intelligence.pageindex_tree import PageIndexTree
from src.intelligence.react_auditor import AuditorReActAgent
from src.notifier.whatsapp_sender import WhatsAppSender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("BidEngine")
console = Console(highlight=False)

from src.licensing.validator import ClientLicenseConfig

class BidEngineOrchestrator:
    """
    Orquestrador Central do Motor OaaS de Licitações Públicas.
    Coordena: Consulta Apify / PNCP -> Download Furtivo -> Parser Híbrido -> PageIndex Vectorless RAG -> Auditoria ReAct -> WhatsApp Delivery.
    """
    def __init__(self, license_config: Optional[ClientLicenseConfig] = None):
        self.license_config = license_config
        self.repo = Repository()
        self.apify = ApifyClient()
        self.pncp = PNCPClient()
        self.fetcher = ScraplingFetcher()
        self.pdf_router = PDFRouter()
        self.tree_engine = PageIndexTree()
        self.auditor = AuditorReActAgent(
            license_key=license_config.license_key if license_config else settings.LICENSE_KEY
        )
        self.notifier = WhatsAppSender()

    def processar_ciclo(
        self,
        uf: Optional[str] = None,
        dias_retroativos: int = 7,
        max_editais: int = 5
    ) -> int:
        target_uf = uf or (self.license_config.uf_target if self.license_config else settings.TARGET_UF)
        client_name = self.license_config.client_name if self.license_config else "Operação Padrão"

        console.print(Panel.fit(
            f"[bold green]Iniciando Ciclo do Motor OaaS de Licitações ({client_name})[/bold green]\n"
            f"Alvo: [cyan]UF={target_uf}[/cyan] | Janela: [yellow]{dias_retroativos} dias[/yellow] | Limite: [magenta]{max_editais} editais[/magenta]",
            title="🎯 ZENO LIC - BID ENGINE"
        ))

        # 1. Consulta prioritária via Apify (ou fallback PNCP)
        if getattr(settings, "CONSULTA_PROVIDER", "apify") == "apify" or self.apify.is_configured():
            logger.info("Executando consulta de editais via Apify...")
            editais_candidatos = self.apify.consultar_editais(
                uf=target_uf,
                dias_retroativos=dias_retroativos,
                tam_pagina=max(10, max_editais)
            )
        else:
            logger.info("Executando consulta direta via PNCP...")
            editais_candidatos = self.pncp.buscar_contratacoes_abertas(
                uf=target_uf,
                dias_retroativos=dias_retroativos,
                tamanho_pagina=max(10, max_editais)
            )

        if not editais_candidatos:
            console.print("[yellow]Nenhuma nova contratação encontrada para os filtros especificados.[/yellow]")
            return 0

        table = Table(title="Editais Coletados no PNCP", show_header=True, header_style="bold magenta")
        table.add_column("Controle PNCP", style="dim", width=26)
        table.add_column("Órgão", width=30)
        table.add_column("Objeto Resumido", width=40)
        table.add_column("Status Idempotência", width=20)

        processados_neste_ciclo = 0

        for edital_raw in editais_candidatos[:max_editais]:
            num_pncp = edital_raw.get("numeroControlePNCP") or "N/A"
            orgao = (edital_raw.get("orgaoEntidade", {}).get("razaoSocial") or f"Órgão {target_uf}")[:30]
            objeto = (edital_raw.get("objetoCompra") or "")[:40]

            # Verificação de Idempotência
            if self.repo.ja_processado(num_pncp):
                table.add_row(num_pncp, orgao, objeto, "[yellow]JÁ PROCESSADO[/yellow]")
                continue

            table.add_row(num_pncp, orgao, objeto, "[green]NOVO - AUDITAR[/green]")
            processados_neste_ciclo += 1

            # Executa pipeline completo para este edital
            self._executar_pipeline_edital(edital_raw)

        console.print(table)
        console.print(f"[bold green]Ciclo finalizado! Total de editais auditados e registrados: {processados_neste_ciclo}[/bold green]\n")
        return processados_neste_ciclo

    def _executar_pipeline_edital(self, edital_raw: dict):
        num_pncp = edital_raw.get("numeroControlePNCP", "")
        cnpj = edital_raw.get("orgaoEntidade", {}).get("cnpj", "")
        ano = int(edital_raw.get("anoCompra") or 2026)
        seq = int(edital_raw.get("sequencialCompra") or 1)

        console.print(f"\n[bold cyan]▶ Processando Edital {num_pncp}...[/bold cyan]")

        # 2. Obter arquivos e itens oficiais do PNCP
        arquivos_info = self.pncp.obter_arquivos_edital(cnpj, ano, seq)
        itens_info = self.pncp.obter_itens_edital(cnpj, ano, seq)

        # 3. Baixar anexos prioritários (PDF)
        documentos_baixados = self.fetcher.baixar_documentos_edital(num_pncp, arquivos_info)
        console.print(f"  [dim]• Anexos baixados: {len(documentos_baixados)} arquivos[/dim]")

        # 4. Roteamento de PDF (PyMuPDF + OCR sob demanda)
        texto_unificado = ""
        arvore_docs = None

        if documentos_baixados:
            pdf_principal = next((f for f in documentos_baixados if f.suffix.lower() == ".pdf"), documentos_baixados[0])
            paginas_conteudo = self.pdf_router.processar_pdf(pdf_principal)
            texto_unificado = self.pdf_router.extrair_texto_unificado_com_citacoes(paginas_conteudo)

            # 5. Construção da Árvore Vectorless RAG (PageIndex)
            arvore_docs = self.tree_engine.construir_arvore(paginas_conteudo)
            console.print(f"  [dim]• Árvore PageIndex estruturada com {len(arvore_docs.subsecoes)} seções[/dim]")

        # 6. Auditoria ReAct Jurídica & Técnica
        auditado = self.auditor.auditar_edital(
            raw_pncp_item=edital_raw,
            arvore_docs=arvore_docs,
            itens_pncp=itens_info,
            texto_completo=texto_unificado
        )

        # 7. Salvar no Repositório com Idempotência
        self.repo.salvar_edital(auditado)
        console.print(f"  [bold green]✔ Auditoria salva no banco de dados![/bold green] Score: [bold]{auditado.score_aderencia}/10[/bold]")

        # 8. Notificação WhatsApp (OaaS Delivery)
        self.notifier.enviar_alerta(auditado)

def run_cycle(config_cliente: Optional[ClientLicenseConfig] = None, max_editais: int = 5) -> int:
    """
    Função limpa de entrada para ser executada pelo Tray App ou por rotinas externas.
    """
    engine = BidEngineOrchestrator(license_config=config_cliente)
    return engine.processar_ciclo(
        uf=config_cliente.uf_target if config_cliente else settings.TARGET_UF,
        dias_retroativos=settings.PNCP_LOOKBACK_DAYS,
        max_editais=max_editais
    )

def main():
    parser = argparse.ArgumentParser(description="Motor OaaS de Licitações Públicas")
    parser.add_argument("--uf", default=settings.TARGET_UF, help="UF alvo (padrão: MT)")
    parser.add_argument("--days", type=int, default=settings.PNCP_LOOKBACK_DAYS, help="Janela de dias retroativos")
    parser.add_argument("--limit", type=int, default=5, help="Quantidade máxima de editais por ciclo")
    parser.add_argument("--cron", type=str, help="Executar em loop contínuo simulando cron")

    args = parser.parse_args()
    engine = BidEngineOrchestrator()

    if args.cron:
        console.print(f"[bold green]Iniciando motor em modo contínuo (intervalo de 60s)...[/bold green]")
        while True:
            try:
                engine.processar_ciclo(uf=args.uf, dias_retroativos=args.days, max_editais=args.limit)
                time.sleep(60)
            except KeyboardInterrupt:
                console.print("[yellow]Motor interrompido pelo usuário.[/yellow]")
                break
    else:
        engine.processar_ciclo(uf=args.uf, dias_retroativos=args.days, max_editais=args.limit)

if __name__ == "__main__":
    main()
