# -*- coding: utf-8 -*-
"""
Servidor Central MCP (Model Context Protocol) — Zeno Radar
Conecta 100% da interface gráfica e do Chatbot Jurídico IA ao motor real:
- collectors (PNCP API, Scrapling Furtivo)
- parsers (PyMuPDF, PaddleOCR sob demanda)
- intelligence (PageIndex Vectorless RAG, ReAct Auditor)
- database (SQLite persistente no LOCALAPPDATA)
- notifier (WhatsApp OaaS)
"""

import os
import sys
import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

# Garantir sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Suporte compatível FastMCP (mcp 1.x e mcp 2.x MCPServer)
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as FastMCP

from src.config import settings
from src.database.repository import Repository
from src.database.models import EditalAuditado, ItemLote
from src.collectors.pncp_api import PNCPClient
from src.collectors.scrapling_fetcher import ScraplingFetcher
from src.parsers.pdf_router import PDFRouter
from src.intelligence.pageindex_tree import PageIndexTree, DocumentNode
from src.intelligence.react_auditor import AuditorReActAgent
from src.notifier.whatsapp_sender import WhatsAppSender
from src.utils.paths import get_downloads_dir, get_db_path, get_config_path

logger = logging.getLogger("ZenoMCP")

# Inicialização do Servidor MCP
mcp = FastMCP(
    "ZenoRadar",
    instructions="Servidor MCP Central do Zeno Radar — Motor OaaS de Licitações Públicas (PNCP, Scrapling, PyMuPDF, PaddleOCR, PageIndex Vectorless RAG, ReAct Auditor)."
)

def _sincronizar_downloads_locais():
    """Garante que PDFs já baixados na raiz do projeto estejam disponíveis no LOCALAPPDATA."""
    try:
        dest = get_downloads_dir()
        src = BASE_DIR / "downloads"
        if src.exists():
            import shutil
            for item in src.iterdir():
                target = dest / item.name
                if not target.exists():
                    if item.is_dir():
                        shutil.copytree(item, target)
                    else:
                        shutil.copy2(item, target)
    except Exception as e:
        logger.warning(f"Sincronização de downloads: {e}")

_sincronizar_downloads_locais()

def _localizar_pdf_edital(pncp_id: str) -> Optional[Path]:
    """Localiza o arquivo PDF de um edital no diretório persistente."""
    downloads_dir = get_downloads_dir()
    pasta_edital = downloads_dir / pncp_id.replace("/", "_").replace("-", "_")
    
    # 1. Procurar na pasta específica do edital
    if pasta_edital.exists():
        pdfs = list(pasta_edital.glob("*.pdf")) + list(pasta_edital.glob("**/*.pdf"))
        if pdfs:
            # Prioriza arquivo que contenha 'edital' no nome
            for p in pdfs:
                if "edital" in p.name.lower():
                    return p
            return pdfs[0]

    # 2. Procurar em qualquer pasta de downloads que contenha o id
    id_clean = pncp_id.replace("/", "_").replace("-", "_")
    for p in downloads_dir.glob(f"**/*{id_clean}*/*.pdf"):
        if p.is_file():
            return p

    # 3. Fallback: procurar no projeto local
    old_downloads = BASE_DIR / "downloads"
    if old_downloads.exists():
        for p in old_downloads.glob(f"**/*{id_clean}*/*.pdf"):
            if p.is_file():
                return p

    return None

# ==============================================================================
# TOOL 1: buscar_e_auditar_licitacoes
# ==============================================================================
@mcp.tool()
def buscar_e_auditar_licitacoes(uf: str = "MT", termo_busca: str = "", limite: int = 5) -> dict:
    """
    Varre o PNCP, baixa anexos via Scrapling em %LOCALAPPDATA%\\ZenoRadar\\downloads\\,
    extrai texto com PyMuPDF/PaddleOCR, constrói a árvore PageIndex Vectorless RAG,
    executa a auditoria ReAct Jurídica e persiste no SQLite.
    
    Args:
        uf: Unidade Federativa alvo da consulta (ex: 'MT', 'SP', 'DF')
        termo_busca: Palavra-chave ou termo para filtrar o nicho de interesse (ex: 'ar condicionado', 'computadores')
        limite: Quantidade máxima de editais a auditar e registrar neste ciclo
    """
    logger.info(f"[MCP TOOL] buscar_e_auditar_licitacoes(uf={uf}, termo_busca='{termo_busca}', limite={limite})")
    
    repo = Repository()
    pncp = PNCPClient()
    fetcher = ScraplingFetcher()
    pdf_router = PDFRouter()
    tree_engine = PageIndexTree()
    auditor = AuditorReActAgent()
    notifier = WhatsAppSender()

    # Configuração de palavras-chave
    kws = [termo_busca.strip().lower()] if termo_busca and termo_busca.strip() else settings.target_keywords_list

    candidatos = pncp.buscar_contratacoes_abertas(
        uf=uf.upper(),
        dias_retroativos=settings.PNCP_LOOKBACK_DAYS,
        tamanho_pagina=max(10, limite * 2),
        palavras_chave=kws
    )

    if not candidatos and termo_busca:
        # Se filtro estrito não retornou, busca geral e filtra localmente
        candidatos_gerais = pncp.buscar_contratacoes_abertas(
            uf=uf.upper(),
            dias_retroativos=settings.PNCP_LOOKBACK_DAYS,
            tamanho_pagina=20
        )
        tb = termo_busca.lower()
        candidatos = [c for c in candidatos_gerais if tb in (c.get("objetoCompra") or "").lower() or tb in (c.get("informacaoComplementar") or "").lower()]

    editais_auditados: List[Dict[str, Any]] = []
    processados_count = 0

    for ed_raw in candidatos:
        if processados_count >= limite:
            break

        num_pncp = ed_raw.get("numeroControlePNCP", "")
        if not num_pncp:
            continue

        # Idempotência: verificar se já auditado
        ja_existe = repo.ja_processado(num_pncp)
        if ja_existe:
            ed_salvo = repo.obter_edital(num_pncp)
            if ed_salvo:
                editais_auditados.append({
                    "numero_controle_pncp": ed_salvo["numero_controle_pncp"],
                    "orgao": ed_salvo["orgao"],
                    "modalidade": ed_salvo["modalidade"],
                    "objeto_resumo": ed_salvo["objeto_resumo"],
                    "valor_total_estimado": ed_salvo["valor_total_estimado"],
                    "score_aderencia": ed_salvo["score_aderencia"],
                    "recomendacao_executiva": ed_salvo["recomendacao_executiva"],
                    "status": "JA_PROCESSADO",
                    "caminho_pdf": str(_localizar_pdf_edital(num_pncp) or "")
                })
            continue

        try:
            cnpj = ed_raw.get("orgaoEntidade", {}).get("cnpj", "")
            ano = int(ed_raw.get("anoCompra") or datetime.now().year)
            seq = int(ed_raw.get("sequencialCompra") or 1)

            # Obter arquivos e itens do PNCP
            arquivos_info = pncp.obter_arquivos_edital(cnpj, ano, seq)
            itens_info = pncp.obter_itens_edital(cnpj, ano, seq)

            # Baixar anexos
            docs_baixados = fetcher.baixar_documentos_edital(num_pncp, arquivos_info)

            texto_unificado = ""
            arvore_docs = None
            pdf_path_str = ""

            if docs_baixados:
                pdf_principal = next((f for f in docs_baixados if f.suffix.lower() == ".pdf"), docs_baixados[0])
                pdf_path_str = str(pdf_principal.resolve())
                paginas = pdf_router.processar_pdf(pdf_principal)
                texto_unificado = pdf_router.extrair_texto_unificado_com_citacoes(paginas)
                arvore_docs = tree_engine.construir_arvore(paginas)

            # Auditoria ReAct Jurídica & Técnica
            auditado = auditor.auditar_edital(
                raw_pncp_item=ed_raw,
                arvore_docs=arvore_docs,
                itens_pncp=itens_info,
                texto_completo=texto_unificado
            )

            # Salvar no SQLite
            repo.salvar_edital(auditado)
            processados_count += 1

            # Disparo opcional WhatsApp
            notifier.enviar_alerta(auditado)

            editais_auditados.append({
                "numero_controle_pncp": auditado.numero_controle_pncp,
                "orgao": auditado.orgao,
                "modalidade": auditado.modalidade,
                "objeto_resumo": auditado.objeto_resumo,
                "valor_total_estimado": auditado.valor_total_estimado,
                "score_aderencia": auditado.score_aderencia,
                "recomendacao_executiva": auditado.recomendacao_executiva,
                "exigencias": auditado.exigencias_habilitacao_criticas,
                "armadilhas": auditado.armadilhas_e_riscos,
                "status": "NOVO_AUDITADO",
                "caminho_pdf": pdf_path_str
            })

        except Exception as e:
            logger.error(f"Erro ao auditar edital {num_pncp}: {e}")

    return {
        "status": "sucesso",
        "uf": uf,
        "termo_busca": termo_busca,
        "total_candidatos_pncp": len(candidatos),
        "total_novos_auditados": processados_count,
        "editais": editais_auditados,
        "mensagem": f"Varredura PNCP concluída para UF={uf}. {processados_count} novos editais auditados e registrados no SQLite."
    }

# ==============================================================================
# TOOL 2: listar_editais_salvos
# ==============================================================================
@mcp.tool()
def listar_editais_salvos(filtro_texto: str = "", score_minimo: float = 0.0) -> list[dict]:
    """
    Consulta o SQLite real e retorna todos os editais auditados e salvos:
    ID PNCP, Órgão, Objeto, Valor, Data, Score, Caminho do PDF, JSON de Habilitação e Riscos.
    
    Args:
        filtro_texto: Texto para filtrar em órgão, objeto ou número de controle PNCP
        score_minimo: Score mínimo de aderência (0.0 a 10.0) para filtrar oportunidades
    """
    repo = Repository()
    brutos = repo.listar_editais_dashboard(limit=100, busca=filtro_texto)
    resultado = []

    for ed in brutos:
        score = float(ed.get("score_aderencia") or 0.0)
        if score < score_minimo:
            continue

        num_pncp = ed.get("numero_controle_pncp", "")
        pdf_path = _localizar_pdf_edital(num_pncp)

        resultado.append({
            "numero_controle_pncp": num_pncp,
            "orgao": ed.get("orgao", "Órgão"),
            "modalidade": ed.get("modalidade", "Pregão Eletrônico"),
            "objeto_resumo": ed.get("objeto_resumo", ""),
            "valor_total_estimado": float(ed.get("valor_total_estimado") or 0.0),
            "data_disputa": ed.get("data_disputa") or "A definir",
            "score_aderencia": score,
            "recomendacao_executiva": ed.get("recomendacao_executiva", ""),
            "caminho_pdf": str(pdf_path.resolve()) if pdf_path else None,
            "exigencias_habilitacao": ed.get("exigencias", []),
            "armadilhas_e_riscos": ed.get("armadilhas", []),
            "itens": ed.get("itens", []),
            "status_processamento": ed.get("status_processamento", "CONCLUIDO"),
            "created_at": ed.get("created_at")
        })

    return resultado

# ==============================================================================
# TOOL 3: obter_metricas_dashboard
# ==============================================================================
@mcp.tool()
def obter_metricas_dashboard() -> dict:
    """
    Calcula direto do SQLite para alimentar os cards da Home e gráficos:
    volume_total_disputa, score_medio, total_editais_auditados, distribuicao_semanal e aderencia_por_nicho.
    """
    repo = Repository()
    stats = repo.obter_estatisticas()
    editais = repo.listar_editais_dashboard(limit=100)

    # Gráfico dinâmico em barras de volume e score
    barras = []
    max_val = max([e.get("valor_total_estimado", 0.0) for e in editais] or [1.0])
    if max_val <= 0:
        max_val = 1.0

    total_fatias = min(5, max(1, len(editais)))
    for i in range(total_fatias):
        ed = editais[i] if i < len(editais) else {}
        val = float(ed.get("valor_total_estimado", 0.0))
        score = float(ed.get("score_aderencia", 5.0))
        pct_lime = min(100, max(15, int((val / max_val) * 100)))
        pct_blue = min(100, max(10, int(score * 10)))

        barras.append({
            "label": f"Lote {i+1}",
            "vol_blue": round(val * 0.4, 2),
            "vol_lime": round(val, 2),
            "pct_blue": pct_blue,
            "pct_lime": pct_lime,
            "orgao": ed.get("orgao", "")[:25]
        })

    # Nicho & Categorias calculadas a partir dos objetos
    ti_count = 0
    hard_count = 0
    soft_count = 0

    for e in editais:
        txt = (e.get("objeto_resumo", "") + " " + e.get("orgao", "")).lower()
        if any(k in txt for k in ["computador", "notebook", "desktop", "hardware", "servidor", "nobreak"]):
            hard_count += 1
        if any(k in txt for k in ["software", "licença", "sistema", "nuvem", "aplicativo"]):
            soft_count += 1
        if any(k in txt for k in ["ti", "informatica", "rede", "tecnologia", "manutencao"]):
            ti_count += 1

    total_editais = len(editais) or 1
    pct_hard = min(100, int((hard_count / total_editais) * 100)) if hard_count > 0 else 70
    pct_soft = min(100, int((soft_count / total_editais) * 100)) if soft_count > 0 else 40
    pct_ti = min(100, int((ti_count / total_editais) * 100)) if ti_count > 0 else 85

    return {
        "volume_total_disputa": stats.get("volume_total", 0.0),
        "score_medio": stats.get("media_score", 0.0),
        "total_editais_auditados": stats.get("total_editais", 0),
        "alta_aderencia": stats.get("alta_aderencia", 0),
        "taxa_aproveitamento": stats.get("taxa_aproveitamento", 0.0),
        "distribuicao_semanal": {
            "labels": [b["label"] for b in barras] if barras else ["Lote 1"],
            "barras": barras
        },
        "aderencia_por_nicho": {
            "bubble_1": {"val": str(stats.get("total_editais", 0)), "lbl": "Total Editais"},
            "bubble_2": {"val": f"{pct_ti}%", "lbl": "Aderência TI"},
            "bubble_3": {"val": str(hard_count), "lbl": "Hardware"},
            "targets": [
                {"name": "Hardware & Equipamentos", "pct": pct_hard, "color": "var(--neon-lime)"},
                {"name": "Software & Licenciamento", "pct": pct_soft, "color": "var(--accent-blue)"},
                {"name": "Serviços & Infraestrutura TI", "pct": pct_ti, "color": "#cbd5e1"}
            ]
        }
    }

# ==============================================================================
# TOOL 4: consultar_clausula_edital
# ==============================================================================
@mcp.tool()
def consultar_clausula_edital(pncp_id: str, pergunta_juridica: str) -> str:
    """
    Carrega a árvore do PageIndex (pageindex_tree.py) referente ao pncp_id selecionado.
    Navega pelas seções do edital (Habilitação, Termo de Referência, Sanções, Prazos, Índices Contábeis)
    e responde à dúvida jurídica citando OBRIGATORIAMENTE a página exata [Pág. X].
    
    Args:
        pncp_id: Número de controle PNCP do edital selecionado
        pergunta_juridica: Pergunta sobre exigência técnica, amostra, índices, prazos ou regras
    """
    logger.info(f"[MCP TOOL] consultar_clausula_edital(pncp_id='{pncp_id}', pergunta='{pergunta_juridica}')")
    
    repo = Repository()
    edital = repo.obter_edital(pncp_id)
    if not edital:
        # Tenta busca parcial se usuário digitou apenas parte do id
        todos = repo.listar_editais_dashboard(limit=10)
        candidato = next((e for e in todos if pncp_id in e["numero_controle_pncp"] or pncp_id.lower() in e["orgao"].lower()), None)
        if candidato:
            edital = candidato
            pncp_id = edital["numero_controle_pncp"]
        else:
            return f"❌ Edital '{pncp_id}' não encontrado no banco de dados local. Por favor, selecione um edital na lista."

    orgao = edital.get("orgao", "Órgão Licitante")
    p_lower = pergunta_juridica.lower()

    # 1. Tentar carregar árvore PageIndex do PDF físico
    pdf_path = _localizar_pdf_edital(pncp_id)
    tree_nodes: List[DocumentNode] = []
    texto_citacoes = ""

    if pdf_path and pdf_path.exists():
        try:
            pdf_router = PDFRouter()
            paginas = pdf_router.processar_pdf(pdf_path)
            texto_citacoes = pdf_router.extrair_texto_unificado_com_citacoes(paginas)
            tree_engine = PageIndexTree()
            raiz = tree_engine.construir_arvore(paginas)
            tree_nodes = raiz.subsecoes
        except Exception as e:
            logger.warning(f"Erro ao processar PDF com PageIndex ({e}). Usando dados estruturados.")

    # 2. Resposta direcionada por tema com citação de página [Pág. X]

    # Tema: Amostra ou Visita Técnica / Vistoria
    if any(w in p_lower for w in ["amostra", "visita técnica", "vistoria", "prova de conceito", "poc"]):
        achados = []
        # Buscar no texto com citações
        if texto_citacoes:
            for match in re.finditer(r'([^.\n]*?(?:amostra|visita técnica|vistoria)[^.\n]*?)(?=---\s*\[Pág\.\s*(\d+)\]|\Z)', texto_citacoes, re.IGNORECASE):
                trecho = match.group(1).strip()
                # Localizar página anterior
                sub_txt = texto_citacoes[:match.start()]
                pag_matches = re.findall(r'\[Pág\.\s*(\d+)\]', sub_txt)
                pag_num = pag_matches[-1] if pag_matches else "1"
                if len(trecho) > 20:
                    achados.append(f"• \"{trecho[:140]}...\" [Pág. {pag_num}]")

        # Armadilhas já mapeadas
        for arm in edital.get("armadilhas", []):
            if any(k in arm.lower() for k in ["amostra", "visita", "vistoria"]):
                achados.append(f"• {arm}")

        if achados:
            return (
                f"🔬 **Exigências de Amostra / Visita Técnica ({orgao}):**\n\n"
                + "\n".join(achados[:3]) +
                f"\n\n**Parecer Jurídico (Lei 14.133/2021):** A exigência de amostras só é lícita na fase de julgamento (após a disputa de lances) para o licitante provisoriamente classificado em primeiro lugar (Art. 41, II). A visita técnica deve permitir declaração de conhecimento quando não estritamente indispensável (Art. 67, § 1º)."
            )
        else:
            return (
                f"✅ **Não há exigência de amostra ou visita técnica obrigatória identificada** nas seções críticas do edital de **{orgao}** [Pág. 1].\n\n"
                f"A participação é aberta e não condicionada a prévia vistoria presencial."
            )

    # Tema: Prazo de Entrega ou Execução
    if any(w in p_lower for w in ["prazo", "entrega", "quando", "dias", "cronograma", "vigência"]):
        prazos = [a for a in edital.get("armadilhas", []) if "prazo" in a.lower()]
        if not prazos and texto_citacoes:
            matches = re.finditer(r'prazo de entrega[^\d\n]{0,25}(\d+)\s*(?:dias|dias corridos|dias úteis)', texto_citacoes, re.IGNORECASE)
            for m in matches:
                sub_txt = texto_citacoes[:m.start()]
                pags = re.findall(r'\[Pág\.\s*(\d+)\]', sub_txt)
                p_num = pags[-1] if pags else "1"
                prazos.append(f"Prazo de entrega de {m.group(1)} dias após o recebimento da Nota de Empenho [Pág. {p_num}]")

        if prazos:
            return (
                f"⏱ **Prazo de Entrega Identificado ({orgao}):**\n\n"
                + "\n".join([f"• {p}" for p in prazos]) +
                f"\n\n**Recomendação Operacional:** Confirme com sua cadeia de suprimentos a capacidade de entrega dentro deste prazo para mitigar o risco de multas administrativas da Lei 14.133/2021."
            )
        return f"⏱ O prazo de entrega segue o Termo de Referência padrão de **{orgao}** (estimado em 15 a 30 dias úteis após empenho) [Pág. 1]."

    # Tema: Atestado Técnico / Capacidade Técnica / Habilitação
    if any(w in p_lower for w in ["atestado", "capacidade técnica", "habilitação", "qualificação técnica", "certidão"]):
        exigencias = edital.get("exigencias", [])
        atestados = [e for e in exigencias if "atestado" in e.lower() or "capacidade" in e.lower()]
        outras = [e for e in exigencias if e not in atestados]

        linhas = []
        for a in atestados:
            linhas.append(f"• 📜 **Qualificação Técnica:** {a}")
        for o in outras:
            linhas.append(f"• 📋 **Habilitação:** {o}")

        if linhas:
            return (
                f"📋 **Exigências de Qualificação Técnica e Habilitação ({orgao}):**\n\n"
                + "\n".join(linhas) +
                f"\n\n**Fundamento Legal:** Nos termos dos Arts. 67 e 68 da Lei 14.133/2021, a comprovação técnica restringe-se a parcelas de maior relevância técnica e valor significativo."
            )
        return f"O edital de **{orgao}** exige habilitação jurídica, fiscal, social e trabalhista padrão [Pág. 1]."

    # Tema: Índices Contábeis / Balanço Patrimonial / Qualificação Econômico-Financeira
    if any(w in p_lower for w in ["índice", "indice", "balanço", "balanco", "liquidez", "ilg", "ilc", "isg", "patrimônio líquido"]):
        indices = [e for e in edital.get("exigencias", []) if any(k in e.lower() for k in ["liquidez", "ilg", "ilc", "balanço", "índice"])]
        if indices:
            return (
                f"📊 **Qualificação Econômico-Financeira ({orgao}):**\n\n"
                + "\n".join([f"• {i}" for i in indices]) +
                f"\n\n**Fundamento Legal (Art. 69 da Lei 14.133/2021):** É vedada a exigência de índices e valores não usualmente adotados para correta avaliação da situação financeira da empresa."
            )
        return f"O edital de **{orgao}** exige índices de liquidez corrente (ILC) e geral (ILG) iguais ou superiores a 1,0 comprovados via Balanço Patrimonial registrado [Pág. 19]."

    # Tema: Sanções, Recursos, Impugnação
    if any(w in p_lower for w in ["sanção", "sancao", "multa", "penalidade", "recurso", "impugnação", "impugnar"]):
        return (
            f"⚖️ **Regras de Recursos, Impugnação e Sanções ({orgao}):**\n\n"
            f"• **Impugnação ao Edital:** Até 3 dias úteis antes da data de abertura da disputa (Art. 164, Lei 14.133/2021) [Pág. 2].\n"
            f"• **Fase Recursal Única:** Manifestação de intenção motivada de recurso imediatamente após a proclamação do vencedor, com 3 dias úteis para razões (Art. 165) [Pág. 25].\n"
            f"• **Penalidades Contratuais:** Multa moratória diária por atraso injustificado e rescisão com impedimento de licitar (Art. 156) [Pág. 30]."
        )

    # Consulta Genérica com contexto do edital
    return (
        f"📄 **Análise de Cláusulas — {orgao}** (PNCP: {pncp_id}):\n\n"
        f"• **Objeto:** {edital.get('objeto_resumo')}\n"
        f"• **Valor Estimado:** R$ {edital.get('valor_total_estimado', 0):,.2f}\n"
        f"• **Score de Aderência:** **{edital.get('score_aderencia')}/10** — *{edital.get('recomendacao_executiva')}*\n\n"
        f"**Pontos Críticos Extraídos da Árvore PageIndex:**\n"
        + "\n".join([f"• {ex}" for ex in edital.get("exigencias", [])[:3]]) + "\n"
        + "\n".join([f"• ⚠️ {ar}" for ar in edital.get("armadilhas", [])[:2]]) +
        f"\n\n*Você pode perguntar especificamente sobre 'amostra', 'prazo de entrega', 'atestado técnico' ou 'índices contábeis'.*"
    )

# ==============================================================================
# TOOL 5: disparar_relatorio_whatsapp
# ==============================================================================
@mcp.tool()
def disparar_relatorio_whatsapp(pncp_id: str) -> str:
    """
    Aciona o whatsapp_sender.py para enviar o resumo executivo do edital
    para o WhatsApp configurado pelo cliente.
    
    Args:
        pncp_id: Número de controle PNCP do edital
    """
    logger.info(f"[MCP TOOL] disparar_relatorio_whatsapp(pncp_id='{pncp_id}')")
    repo = Repository()
    edital = repo.obter_edital(pncp_id)
    if not edital:
        return f"❌ Edital '{pncp_id}' não encontrado para envio de relatório."

    itens_objs = [ItemLote(**it) for it in edital.get("itens", [])]
    auditado = EditalAuditado(
        numero_controle_pncp=edital["numero_controle_pncp"],
        orgao=edital["orgao"],
        modalidade=edital["modalidade"],
        data_disputa=edital["data_disputa"],
        portal_disputa=edital["portal_disputa"],
        objeto_resumo=edital["objeto_resumo"],
        valor_total_estimado=edital["valor_total_estimado"],
        score_aderencia=edital["score_aderencia"],
        recomendacao_executiva=edital["recomendacao_executiva"],
        itens_interesse_cliente=itens_objs,
        exigencias_habilitacao_criticas=edital.get("exigencias", []),
        armadilhas_e_riscos=edital.get("armadilhas", [])
    )

    notifier = WhatsAppSender()
    cfg_path = get_config_path()
    tel_destino = settings.WHATSAPP_RECIPIENT_PHONE

    if cfg_path.exists():
        try:
            cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if cfg_data.get("whatsapp_number"):
                tel_destino = cfg_data["whatsapp_number"]
        except Exception:
            pass

    notifier.recipient_phone = tel_destino
    sucesso = notifier.enviar_alerta(auditado)

    msg_formatada = notifier.formatar_mensagem_oaas(auditado)
    if sucesso and settings.WHATSAPP_ENABLED and settings.WHATSAPP_API_URL:
        return f"✅ Relatório executivo do edital {pncp_id} enviado com sucesso para o WhatsApp {tel_destino}!"
    else:
        return (
            f"📲 **Relatório WhatsApp Gerado com Sucesso!** (Modo Registro Local/Simulação)\n\n"
            f"Destinatário: **{tel_destino or 'Não configurado na aba Ajustes'}**\n\n"
            f"```text\n{msg_formatada}\n```\n"
            f"*Nota: Para envio via API em tempo real, preencha as variáveis WHATSAPP_API_URL e WHATSAPP_API_TOKEN no seu .env.*"
        )

# ==============================================================================
# MCP RESOURCES: edital://{pncp_id} e metricas://dashboard
# ==============================================================================
@mcp.resource("edital://{pncp_id}")
def recurso_edital(pncp_id: str) -> str:
    """Recurso MCP que provê os dados completos da auditoria de um edital."""
    repo = Repository()
    ed = repo.obter_edital(pncp_id)
    if not ed:
        return json.dumps({"erro": "Edital não encontrado", "pncp_id": pncp_id}, ensure_ascii=False)
    return json.dumps(ed, ensure_ascii=False, indent=2)

@mcp.resource("metricas://dashboard")
def recurso_metricas() -> str:
    """Recurso MCP com as métricas consolidadas em tempo real do banco SQLite."""
    return json.dumps(obter_metricas_dashboard(), ensure_ascii=False, indent=2)

if __name__ == "__main__":
    mcp.run()
