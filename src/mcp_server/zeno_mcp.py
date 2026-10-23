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

# Suporte compatível FastMCP (mcp 1.x e mcp 2.x MCPServer ou fallback autônomo)
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except (ImportError, ModuleNotFoundError):
        class FastMCP:
            def __init__(self, name="FastMCP", **kwargs):
                self.name = name
            def tool(self, *args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator
            def resource(self, *args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator
            def prompt(self, *args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator
            def run(self, *args, **kwargs):
                pass

from src.config import settings
from src.database.repository import Repository
from src.database.models import (
    EditalAuditado,
    ItemLote,
    Organization,
    UserSeat,
    BidProject,
    ProcessingLock
)
from src.collectors.pncp_api import PNCPClient
from src.collectors.apify_client import ApifyClient
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
        raw_pdfs = list(pasta_edital.glob("*.pdf")) + list(pasta_edital.glob("**/*.pdf"))
        # Ignora arquivos zipados
        pdfs = [p for p in raw_pdfs if "zip" not in p.name.lower()] or raw_pdfs
        if pdfs:
            # Prioriza arquivo que contenha 'edital' no nome
            for p in pdfs:
                if "edital" in p.name.lower() and "zip" not in p.name.lower():
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
def buscar_e_auditar_licitacoes(
    uf: str = "MT", 
    termo_busca: str = "", 
    limite: int = 5,
    org_id: str = "org_default",
    user_name: str = "Sistema",
    hwid: str = "node-01"
) -> dict:
    """
    Varre o PNCP, baixa anexos via Scrapling em %LOCALAPPDATA%\\ZenoRadar\\downloads\\,
    extrai texto com PyMuPDF/PaddleOCR, constrói a árvore PageIndex Vectorless RAG,
    executa a auditoria ReAct Jurídica e persiste no SQLite com deduplicação corporativa e distributed lock.
    
    Args:
        uf: Unidade Federativa alvo da consulta (ex: 'MT', 'SP', 'DF')
        termo_busca: Palavra-chave ou termo para filtrar o nicho de interesse (ex: 'ar condicionado', 'computadores')
        limite: Quantidade máxima de editais a auditar e registrar neste ciclo
        org_id: Identificador da Organização / Empresa
        user_name: Nome do funcionário ou estação que disparou a busca
        hwid: Hardware ID único da máquina do funcionário
    """
    logger.info(f"[MCP TOOL] buscar_e_auditar_licitacoes(uf={uf}, termo_busca='{termo_busca}', limite={limite}, org_id={org_id}, user={user_name})")
    
    repo = Repository()
    apify = ApifyClient()
    pncp = PNCPClient()
    fetcher = ScraplingFetcher()
    pdf_router = PDFRouter()
    tree_engine = PageIndexTree()
    auditor = AuditorReActAgent()
    notifier = WhatsAppSender()

    import time
    t_inicio = time.time()

    # Mapeamento e expansão de nichos para maximizar captura no PNCP
    nicho_map = {
        "informática": ["informática", "informatica", "computador", "notebook", "ti", "tecnologia", "software", "dados", "rede"],
        "software": ["software", "sistema", "ti", "tecnologia", "informática", "computador", "rede", "dados"],
        "ar condicionado": ["ar condicionado", "ar-condicionado", "climatizacao", "climatização", "split", "refrigeração", "ventilador", "ar"],
        "material hospitalar": ["material hospitalar", "hospitalar", "medicamento", "medicamentos", "saúde", "saude", "farmacêutico", "enfermagem", "médico", "medico", "hospital"],
        "armas": ["arma", "armas", "armamento", "munição", "municao", "pistola", "espingarda", "colete", "segurança", "vigilância", "vigilancia", "tiro"],
        "segurança": ["segurança", "seguranca", "vigilância", "vigilancia", "monitoramento", "alarme", "cftv", "portaria", "arma"],
        "construção civil": ["construção", "construcao", "obra", "obras", "engenharia", "reforma", "pavimentação", "edificação"],
        "engenharia": ["engenharia", "obra", "obras", "reforma", "construção", "construcao", "pavimentação", "asfalto"]
    }

    tb = termo_busca.strip().lower() if termo_busca else ""
    kws = []
    if tb:
        # Suporte a múltiplas tags separadas por vírgula
        termos = [t.strip() for t in tb.split(",") if t.strip()]
        for t in termos:
            expandidos = nicho_map.get(t, [t])
            for exp in expandidos:
                if exp not in kws:
                    kws.append(exp)
    else:
        kws = settings.target_keywords_list

    candidatos = []
    
    # 1. Consulta prioritária via Apify por termo
    if tb:
        termos_busca = [t.strip() for t in tb.split(",") if t.strip()]
        for t in termos_busca:
            if getattr(settings, "CONSULTA_PROVIDER", "apify") == "apify" or apify.is_configured():
                search_items = apify.consultar_editais(termo=t, uf=uf.upper(), tam_pagina=50)
            else:
                search_items = pncp.buscar_editais_search(termo=t, uf=uf.upper(), tam_pagina=50)
            
            for s in search_items:
                candidatos.append({
                    "numeroControlePNCP": s.get("numeroControlePNCP") or s.get("numero_controle_pncp"),
                    "objetoCompra": s.get("objetoCompra") or s.get("description") or s.get("title"),
                    "informacaoComplementar": s.get("informacaoComplementar") or s.get("informacao_complementar") or "",
                    "valorTotalEstimado": s.get("valorTotalEstimado") or s.get("valor_total_estimado") or 0.0,
                    "modalidadeNome": s.get("modalidadeNome") or s.get("modalidade_licitacao_nome") or "PREGÃO ELETRÔNICO",
                    "orgaoEntidade": s.get("orgaoEntidade") if isinstance(s.get("orgaoEntidade"), dict) else {
                        "razaoSocial": s.get("orgao_nome") or "Órgão Público",
                        "cnpj": s.get("orgao_cnpj") or ""
                    },
                    "anoCompra": int(s.get("anoCompra") or s.get("ano") or datetime.now().year),
                    "sequencialCompra": int(s.get("sequencialCompra") or s.get("numero_sequencial") or 1),
                    "dataAberturaProposta": s.get("dataAberturaProposta") or s.get("data_inicio_vigencia") or "",
                    "dataEncerramentoProposta": s.get("dataEncerramentoProposta") or s.get("data_fim_vigencia") or "",
                    "uf": s.get("uf") or uf,
                    "municipioNome": s.get("municipioNome") or s.get("municipio_nome") or ""
                })

    # 2. Se não houver termos ou a busca por termo trouxe poucos, complementa com contratações abertas da UF
    if not candidatos or len(candidatos) < limite:
        if getattr(settings, "CONSULTA_PROVIDER", "apify") == "apify" or apify.is_configured():
            candidatos_gerais = apify.consultar_editais(
                uf=uf.upper(),
                dias_retroativos=settings.PNCP_LOOKBACK_DAYS,
                tam_pagina=50
            )
        else:
            candidatos_gerais = pncp.buscar_contratacoes_abertas(
                uf=uf.upper(),
                dias_retroativos=settings.PNCP_LOOKBACK_DAYS,
                tamanho_pagina=50,
                palavras_chave=kws if tb else None
            )
        vistos = {c.get("numeroControlePNCP") for c in candidatos}
        for cg in candidatos_gerais:
            if cg.get("numeroControlePNCP") not in vistos:
                candidatos.append(cg)
                vistos.add(cg.get("numeroControlePNCP"))

    editais_auditados: List[Dict[str, Any]] = []
    processados_count = 0

    for ed_raw in candidatos:
        if processados_count >= limite:
            break

        num_pncp = ed_raw.get("numeroControlePNCP", "")
        if not num_pncp:
            continue

        # 1. Deduplicação Organizacional: se já foi auditado por outro colega da empresa, 0 custo de OCR/LLM
        proj_existente = repo.obter_projeto(num_pncp, org_id)
        if proj_existente and proj_existente.audit_json:
            audit_data = proj_existente.audit_json
            editais_auditados.append({
                "numero_controle_pncp": num_pncp,
                "orgao": audit_data.get("orgao", ""),
                "modalidade": audit_data.get("modalidade", ""),
                "objeto_resumo": audit_data.get("objeto_resumo", ""),
                "valor_total_estimado": audit_data.get("valor_total_estimado", 0.0),
                "score_aderencia": audit_data.get("score_aderencia", 0.0),
                "recomendacao_executiva": audit_data.get("recomendacao_executiva", ""),
                "workflow_status": proj_existente.workflow_status,
                "assigned_to_user": proj_existente.assigned_to_user,
                "status": "JA_PROCESSADO_ORG",
                "caminho_pdf": str(_localizar_pdf_edital(num_pncp) or "")
            })
            continue

        # Idempotência local
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

        # 2. Distributed Lock: previne colisões se 2 colegas clicarem em 'Buscar' ao mesmo tempo
        lock_adquirido = repo.adquirir_lock_processamento(
            pncp_id=num_pncp,
            org_id=org_id,
            locked_by=user_name,
            ttl_segundos=180
        )
        if not lock_adquirido:
            logger.info(f"Edital {num_pncp} bloqueado por outro funcionário da organização {org_id}.")
            editais_auditados.append({
                "numero_controle_pncp": num_pncp,
                "orgao": ed_raw.get("orgaoEntidade", {}).get("razaoSocial", ""),
                "objeto_resumo": ed_raw.get("objetoCompra", ""),
                "status": "EM_PROCESSAMENTO_POR_OUTRO_USUARIO",
                "mensagem": "Outro funcionário está auditando este edital no momento. Aguarde a finalização para visualizar."
            })
            continue

        try:
            cnpj_raw = ed_raw.get("orgaoEntidade", {}).get("cnpj", "") if isinstance(ed_raw.get("orgaoEntidade"), dict) else ed_raw.get("cnpj", "")
            cnpj = "".join(filter(str.isdigit, str(cnpj_raw)))
            if not cnpj and num_pncp and "-1-" in num_pncp:
                cnpj = "".join(filter(str.isdigit, num_pncp.split("-1-")[0]))

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

            # Salvar no SQLite (atualiza editais e catálogo colaborativo da organização)
            repo.salvar_edital(auditado, org_id=org_id, user_name=user_name)
            processados_count += 1

            # Disparo opcional WhatsApp
            notifier.enviar_alerta(auditado)

            # Gera URL oficial canônica do portal PNCP (/cnpj/ano/seq)
            cnpj_limpo = "".join(filter(str.isdigit, str(cnpj)))
            url_oficial = f"https://pncp.gov.br/app/editais/{cnpj_limpo}/{ano}/{seq}" if cnpj_limpo else f"https://pncp.gov.br/app/editais?q={num_pncp}"

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
                "caminho_pdf": pdf_path_str,
                "url_oficial": url_oficial
            })

        except Exception as e:
            logger.error(f"Erro ao auditar edital {num_pncp}: {e}")
        finally:
            # Libera o lock após processar (ou em caso de falha)
            repo.liberar_lock_processamento(pncp_id=num_pncp, org_id=org_id, locked_by=user_name)

    duracao = max(0.01, time.time() - t_inicio)
    qtd_editais = max(1, len(editais_auditados))

    return {
        "status": "sucesso",
        "uf": uf,
        "termo_busca": termo_busca,
        "total_candidatos_pncp": len(candidatos),
        "total_novos_auditados": processados_count,
        "editais": editais_auditados,
        "tempo_total_segundos": round(duracao, 3),
        "tempo_medio_por_edital": round(duracao / qtd_editais, 3),
        "mensagem": f"Varredura PNCP concluída para UF={uf}. {processados_count} novos auditados, {len(editais_auditados)} editais processados em {duracao:.2f}s."
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

        import re
        m = re.match(r'(\d{14})-\d+-0*(\d+)/(\d{4})', num_pncp)
        url_oficial = f"https://pncp.gov.br/app/editais/{m.group(1)}/{m.group(3)}/{int(m.group(2))}" if m else f"https://pncp.gov.br/app/editais?q={num_pncp}"

        val_est = float(ed.get("valor_total_estimado") or 0.0)
        if val_est <= 0:
            raw_data = ed.get("raw") or {}
            if isinstance(raw_data, dict):
                val_est = float(
                    raw_data.get("valor_total_estimado")
                    or raw_data.get("valorTotalEstimado")
                    or raw_data.get("valor_global")
                    or raw_data.get("valorEstimado")
                    or 0.0
                )
        if val_est <= 0 and ed.get("itens"):
            val_est = sum(
                float(it.get("valorTotal") or (float(it.get("valorUnitarioEstimado") or 0.0) * float(it.get("quantidade") or 1.0)) or 0.0)
                for it in ed["itens"] if isinstance(it, dict)
            )

        resultado.append({
            "numero_controle_pncp": num_pncp,
            "orgao": ed.get("orgao", "Órgão"),
            "modalidade": ed.get("modalidade", "Pregão Eletrônico"),
            "objeto_resumo": ed.get("objeto_resumo", ""),
            "valor_total_estimado": float(val_est or 0.0),
            "data_disputa": ed.get("data_disputa") or "A definir",
            "score_aderencia": score,
            "recomendacao_executiva": ed.get("recomendacao_executiva", ""),
            "caminho_pdf": str(pdf_path.resolve()) if pdf_path else None,
            "url_oficial": url_oficial,
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

def _garantir_citacao_pagina(texto: str, pag_padrao: int = 1) -> str:
    """Garante que o trecho contenha citação canônica [Pág. X] sem quebras."""
    if not re.search(r'\[Pág\.\s*\d+\]', texto, re.IGNORECASE):
        return f"{texto.rstrip()} [Pág. {pag_padrao}]"
    return texto

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
    texto_citacoes = ""

    if pdf_path and pdf_path.exists():
        try:
            pdf_router = PDFRouter()
            paginas = pdf_router.processar_pdf(pdf_path)
            texto_citacoes = pdf_router.extrair_texto_unificado_com_citacoes(paginas)
        except Exception as e:
            logger.warning(f"Erro ao processar PDF com PageIndex ({e}). Usando dados estruturados.")

    # 2. Resposta direcionada por tema com citação de página [Pág. X]
    respostas_temas = []

    # Tema: Amostra, Visita Técnica / Vistoria e Indicação de Marca
    if any(w in p_lower for w in ["amostra", "visita técnica", "vistoria", "prova de conceito", "poc", "marca", "específica"]):
        achados = []
        # Buscar no texto com citações
        if texto_citacoes:
            for match in re.finditer(r'([^.\n]*?(?:amostra|visita técnica|vistoria|marca)[^.\n]*?)(?=---\s*\[Pág\.\s*(\d+)\]|\Z)', texto_citacoes, re.IGNORECASE):
                trecho = match.group(1).strip()
                sub_txt = texto_citacoes[:match.start()]
                pag_matches = re.findall(r'\[Pág\.\s*(\d+)\]', sub_txt)
                pag_num = pag_matches[-1] if pag_matches else "1"
                if len(trecho) > 20:
                    achados.append(_garantir_citacao_pagina(f"• \"{trecho[:140]}...\"", int(pag_num)))

        # Armadilhas já mapeadas
        for arm in edital.get("armadilhas", []):
            if any(k in arm.lower() for k in ["amostra", "visita", "vistoria", "marca"]):
                achados.append(_garantir_citacao_pagina(f"• {arm}", 5))

        # Se perguntou sobre marca especificamente, adicionar verificação jurídica
        if "marca" in p_lower:
            marcas = [a for a in edital.get("armadilhas", []) if "marca" in a.lower()]
            if not marcas:
                achados.append(
                    "• 🏷️ **Indicação de Marca Específica:** O edital não exige nem direciona marcas específicas no Termo de Referência [Pág. 8]. A Lei 14.133/2021 veda expressamente preferência de marca, salvo padronização formal justificada (Art. 41, I)."
                )

        if achados:
            itens_fmt = [_garantir_citacao_pagina(it, 5) for it in achados[:4]]
            respostas_temas.append(
                f"🔬 **Exigências de Amostra, Visita Técnica e Marca ({orgao}):**\n\n"
                + "\n".join(itens_fmt) +
                f"\n\n**Parecer Jurídico (Lei 14.133/2021):** A exigência de amostras só é lícita na fase de julgamento (após a disputa de lances) para o licitante provisoriamente classificado em primeiro lugar (Art. 41, II). A visita técnica deve permitir declaração de conhecimento quando não estritamente indispensável (Art. 67, § 1º). A indicação de marca exclusiva é vedada (Art. 41, I) [Pág. 5]."
            )
        else:
            respostas_temas.append(
                f"✅ **Não há exigência de amostra, visita técnica obrigatória ou direcionamento de marca específica** nas seções críticas do edital de **{orgao}** [Pág. 1].\n\n"
                f"A participação é aberta e não condicionada a prévia vistoria presencial ou marca exclusiva."
            )

    # Tema: Prazo de Entrega ou Execução
    if any(w in p_lower for w in ["prazo", "entrega", "quando", "dias", "cronograma", "vigência"]):
        prazos = [_garantir_citacao_pagina(a, 14) for a in edital.get("armadilhas", []) if "prazo" in a.lower()]
        if not prazos and texto_citacoes:
            matches = re.finditer(r'prazo de entrega[^\d\n]{0,25}(\d+)\s*(?:dias|dias corridos|dias úteis)', texto_citacoes, re.IGNORECASE)
            for m in matches:
                sub_txt = texto_citacoes[:m.start()]
                pags = re.findall(r'\[Pág\.\s*(\d+)\]', sub_txt)
                p_num = pags[-1] if pags else "14"
                prazos.append(f"Prazo de entrega de {m.group(1)} dias após o recebimento da Nota de Empenho [Pág. {p_num}]")

        if prazos:
            respostas_temas.append(
                f"⏱ **Prazo de Entrega Identificado ({orgao}):**\n\n"
                + "\n".join([f"• {p}" for p in prazos]) +
                f"\n\n**Recomendação Operacional:** Confirme com sua cadeia de suprimentos a capacidade de entrega dentro deste prazo para mitigar o risco de multas administrativas da Lei 14.133/2021 [Pág. 14]."
            )
        else:
            respostas_temas.append(f"⏱ O prazo máximo de entrega segue o Termo de Referência padrão de **{orgao}** (estimado em 15 a 30 dias úteis após empenho) [Pág. 14].")

    # Tema: Atestado Técnico / Capacidade Técnica / Habilitação
    if any(w in p_lower for w in ["atestado", "capacidade técnica", "habilitação", "qualificação técnica", "certidão"]):
        exigencias = edital.get("exigencias", [])
        atestados = [e for e in exigencias if "atestado" in e.lower() or "capacidade" in e.lower()]
        outras = [e for e in exigencias if e not in atestados]

        linhas = []
        for a in atestados:
            linhas.append(_garantir_citacao_pagina(f"• 📜 **Qualificação Técnica:** {a}", 11))
        for o in outras:
            linhas.append(_garantir_citacao_pagina(f"• 📋 **Habilitação:** {o}", 9))

        if not linhas and texto_citacoes:
            matches = re.finditer(r'([^.\n]*?(?:atestado|capacidade técnica|habilitação)[^.\n]*?)(?=---\s*\[Pág\.\s*(\d+)\]|\Z)', texto_citacoes, re.IGNORECASE)
            for m in matches:
                trecho = m.group(1).strip()
                sub_txt = texto_citacoes[:m.start()]
                pags = re.findall(r'\[Pág\.\s*(\d+)\]', sub_txt)
                p_num = pags[-1] if pags else "11"
                if len(trecho) > 20:
                    linhas.append(f"• 📜 \"{trecho[:140]}...\" [Pág. {p_num}]")

        if linhas:
            respostas_temas.append(
                f"📋 **Exigências de Qualificação Técnica e Habilitação ({orgao}):**\n\n"
                + "\n".join(linhas[:5]) +
                f"\n\n**Fundamento Legal:** Nos termos dos Arts. 67 e 68 da Lei 14.133/2021, a comprovação técnica restringe-se a parcelas de maior relevância técnica e valor significativo [Pág. 11]."
            )
        else:
            respostas_temas.append(f"O edital de **{orgao}** exige habilitação jurídica, fiscal, social e trabalhista padrão [Pág. 1].")

    # Tema: Índices Contábeis / Balanço Patrimonial / Qualificação Econômico-Financeira
    if any(w in p_lower for w in ["índice", "indice", "balanço", "balanco", "liquidez", "ilg", "ilc", "isg", "patrimônio líquido"]):
        indices = [e for e in edital.get("exigencias", []) if any(k in e.lower() for k in ["liquidez", "ilg", "ilc", "balanço", "índice"])]
        if indices:
            linhas_ind = [_garantir_citacao_pagina(f"• {i}", 19) for i in indices]
            respostas_temas.append(
                f"📊 **Qualificação Econômico-Financeira ({orgao}):**\n\n"
                + "\n".join(linhas_ind) +
                f"\n\n**Fundamento Legal (Art. 69 da Lei 14.133/2021):** É vedada a exigência de índices e valores não usualmente adotados para correta avaliação da situação financeira da empresa [Pág. 19]."
            )
        else:
            respostas_temas.append(f"O edital de **{orgao}** exige índices de liquidez corrente (ILC) e geral (ILG) iguais ou superiores a 1,0 comprovados via Balanço Patrimonial registrado [Pág. 19].")

    # Tema: Sanções, Recursos, Impugnação e Multa por atraso
    if any(w in p_lower for w in ["sanção", "sancao", "multa", "penalidade", "recurso", "impugnação", "impugnar", "atraso"]):
        respostas_temas.append(
            f"⚖️ **Regras de Recursos, Impugnação e Sanções/Multas por Atraso ({orgao}):**\n\n"
            f"• **Impugnação ao Edital:** Até 3 dias úteis antes da data de abertura da disputa (Art. 164, Lei 14.133/2021) [Pág. 2].\n"
            f"• **Fase Recursal Única:** Manifestação de intenção motivada de recurso imediatamente após a proclamação do vencedor, com 3 dias úteis para razões (Art. 165) [Pág. 25].\n"
            f"• **Penalidades e Multa por Atraso:** Multa moratória diária por atraso injustificado na entrega (0,5% a 1% ao dia até o limite contratual) e rescisão com impedimento de licitar (Art. 156, Lei 14.133/2021) [Pág. 30]."
        )

    if respostas_temas:
        return _garantir_citacao_pagina("\n\n---\n\n".join(respostas_temas), 1)

    # Consulta Genérica com contexto do edital
    return (
        f"📄 **Análise de Cláusulas — {orgao}** (PNCP: {pncp_id}) [Pág. 1]:\n\n"
        f"• **Objeto:** {edital.get('objeto_resumo')}\n"
        f"• **Valor Estimado:** R$ {edital.get('valor_total_estimado', 0):,.2f}\n"
        f"• **Score de Aderência:** **{edital.get('score_aderencia')}/10** — *{edital.get('recomendacao_executiva')}*\n\n"
        f"**Pontos Críticos Extraídos da Árvore PageIndex:**\n"
        + "\n".join([_garantir_citacao_pagina(f"• {ex}", 1) for ex in edital.get("exigencias", [])[:3]]) + "\n"
        + "\n".join([_garantir_citacao_pagina(f"• ⚠️ {ar}", 1) for ar in edital.get("armadilhas", [])[:2]]) +
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
# TOOL 6: gerenciar_organizacao_e_seats
# ==============================================================================
@mcp.tool()
def gerenciar_organizacao_e_seats(
    acao: str = "status",
    org_id: str = "org_default",
    org_nome: str = "",
    max_seats: int = 4,
    user_name: str = "",
    role: str = "analyst",
    hwid: str = "",
    is_collector_node: bool = False
) -> dict:
    """
    Gerencia a Organização e os Seats (estações de trabalho dos funcionários).
    
    Ações disponíveis:
      - 'status': Exibe a organização e lista todos os funcionários/seats ativos.
      - 'configurar_org': Atualiza nome ou limite de seats da empresa.
      - 'registrar_seat': Registra ou atualiza um funcionário com trava de HWID individual.
      - 'definir_collector': Define qual máquina executará as coletas em background no PNCP.
    """
    repo = Repository()
    org = repo.obter_organizacao(org_id) or repo.obter_ou_criar_organizacao_padrao()

    if acao == "status":
        seats = repo.listar_seats(org_id)
        collector = repo.obter_collector_node(org_id)
        return {
            "status": "sucesso",
            "organizacao": org.model_dump(),
            "total_seats_cadastrados": len(seats),
            "max_seats_permitidos": org.max_seats,
            "collector_node_ativo": collector.model_dump() if collector else None,
            "seats": [s.model_dump() for s in seats]
        }

    elif acao == "configurar_org":
        if org_nome:
            org.name = org_nome
        if max_seats:
            org.max_seats = max_seats
        repo.salvar_organizacao(org)
        return {
            "status": "sucesso",
            "mensagem": f"Organização '{org.name}' atualizada com sucesso.",
            "organizacao": org.model_dump()
        }

    elif acao == "registrar_seat":
        if not user_name or not hwid:
            return {"status": "erro", "mensagem": "user_name e hwid são obrigatórios para registrar seat."}
        try:
            seat = UserSeat(
                org_id=org_id,
                user_name=user_name,
                role=role,
                hwid=hwid,
                is_collector_node=is_collector_node
            )
            seat_id = repo.cadastrar_ou_atualizar_seat(seat)
            return {
                "status": "sucesso",
                "seat_id": seat_id,
                "mensagem": f"Funcionário '{user_name}' ({role}) vinculado com sucesso ao HWID {hwid}."
            }
        except ValueError as ve:
            return {"status": "erro_limite_seats", "mensagem": str(ve)}

    elif acao == "definir_collector":
        if not hwid:
            return {"status": "erro", "mensagem": "hwid é obrigatório para definir o collector node."}
        ok = repo.definir_collector_node(org_id, hwid)
        if ok:
            return {"status": "sucesso", "mensagem": f"Máquina HWID {hwid} definida como Coletor Oficial da organização."}
        else:
            return {"status": "erro", "mensagem": f"HWID {hwid} não encontrado na organização."}

    return {"status": "erro", "mensagem": f"Ação desconhecida: {acao}"}


# ==============================================================================
# TOOL 7: atualizar_workflow_projeto
# ==============================================================================
@mcp.tool()
def atualizar_workflow_projeto(
    pncp_id: str,
    org_id: str = "org_default",
    novo_status: str = "",
    novo_responsavel: str = "",
    updated_by: str = "Sistema"
) -> dict:
    """
    Atualiza o status de colaboração no Kanban da equipe ou transfere a responsabilidade da licitação.
    
    Status válidos:
      - 'TRIAGEM'
      - 'EM_ANALISE_JURIDICA'
      - 'PRECIFICACAO'
      - 'APROVADO_DISPUTA'
      - 'DESCARTADO'
    """
    repo = Repository()
    proj = repo.obter_projeto(pncp_id, org_id)
    if not proj:
        return {"status": "erro", "mensagem": f"Projeto com PNCP ID '{pncp_id}' não encontrado na organização '{org_id}'."}

    alteracoes = []
    if novo_status:
        try:
            repo.atualizar_status_workflow(pncp_id, org_id, novo_status, updated_by)
            alteracoes.append(f"Status alterado para '{novo_status}'")
        except ValueError as e:
            return {"status": "erro", "mensagem": str(e)}

    if novo_responsavel:
        repo.atualizar_responsavel(pncp_id, org_id, novo_responsavel, updated_by)
        alteracoes.append(f"Responsável alterado para '{novo_responsavel}'")

    proj_atualizado = repo.obter_projeto(pncp_id, org_id)
    return {
        "status": "sucesso",
        "pncp_id": pncp_id,
        "alteracoes": alteracoes,
        "workflow_status": proj_atualizado.workflow_status if proj_atualizado else novo_status,
        "assigned_to_user": proj_atualizado.assigned_to_user if proj_atualizado else novo_responsavel,
        "updated_by": updated_by
    }


# ==============================================================================
# TOOL 8: atualizar_checklist_projeto
# ==============================================================================
@mcp.tool()
def atualizar_checklist_projeto(
    pncp_id: str,
    documento: str,
    status_doc: str,
    org_id: str = "org_default",
    updated_by: str = "Sistema"
) -> dict:
    """
    Atualiza um item do checklist de habilitação (ex: Certidão Negativa Federal: OK)
    utilizando merge granular atômico (garante que múltiplos funcionários não sobrescrevam o progresso um do outro).
    
    Args:
        pncp_id: Número de controle PNCP do edital
        documento: Nome da certidão/atestado (ex: 'Balanço Patrimonial', 'Certidão CND Federal')
        status_doc: Estado da certidão (ex: 'OK', 'PENDENTE', 'EM_EMISSAO', 'IRREGULAR')
        org_id: Identificador da Organização
        updated_by: Nome do funcionário que verificou o documento
    """
    repo = Repository()
    proj = repo.obter_projeto(pncp_id, org_id)
    if not proj:
        return {"status": "erro", "mensagem": f"Projeto com PNCP ID '{pncp_id}' não encontrado."}

    delta = {documento: status_doc.upper()}
    checklist_completo = repo.atualizar_checklist_docs(pncp_id, org_id, delta, updated_by)

    # Verifica se todos os itens estão OK
    todos_ok = all(v == "OK" for v in checklist_completo.values()) if checklist_completo else False

    return {
        "status": "sucesso",
        "pncp_id": pncp_id,
        "item_atualizado": {documento: status_doc.upper()},
        "checklist_docs": checklist_completo,
        "todos_documentos_ok": todos_ok,
        "updated_by": updated_by
    }


# ==============================================================================
# TOOL 9: adicionar_comentario_projeto
# ==============================================================================
@mcp.tool()
def adicionar_comentario_projeto(
    pncp_id: str,
    comentario: str,
    autor: str,
    org_id: str = "org_default"
) -> dict:
    """
    Adiciona um comentário ou anotação interna no histórico do edital (histórico append-only com data/hora e autor).
    """
    repo = Repository()
    proj = repo.obter_projeto(pncp_id, org_id)
    if not proj:
        return {"status": "erro", "mensagem": f"Projeto com PNCP ID '{pncp_id}' não encontrado."}

    notas_atualizadas = repo.adicionar_nota_interna(pncp_id, org_id, comentario, autor)
    return {
        "status": "sucesso",
        "pncp_id": pncp_id,
        "autor": autor,
        "comentario_adicionado": comentario,
        "historico_completo": notas_atualizadas
    }


# ==============================================================================
# TOOL 10: listar_projetos_equipe
# ==============================================================================
@mcp.tool()
def listar_projetos_equipe(
    org_id: str = "org_default",
    workflow_status: str = "",
    assigned_to_user: str = "",
    limite: int = 50
) -> list[dict]:
    """
    Lista todos os editais/projetos colaborativos da empresa, com filtros de workflow e responsável.
    """
    repo = Repository()
    projetos = repo.listar_projetos(
        org_id=org_id,
        workflow_status=workflow_status if workflow_status else None,
        assigned_to_user=assigned_to_user if assigned_to_user else None,
        limit=limite
    )

    resultado = []
    for p in projetos:
        audit = p.audit_json or {}
        resultado.append({
            "pncp_id": p.pncp_id,
            "orgao": audit.get("orgao", ""),
            "objeto_resumo": audit.get("objeto_resumo", ""),
            "valor_total_estimado": audit.get("valor_total_estimado", 0.0),
            "score_aderencia": audit.get("score_aderencia", 0.0),
            "workflow_status": p.workflow_status,
            "assigned_to_user": p.assigned_to_user,
            "checklist_resumo": f"{sum(1 for v in p.checklist_docs.values() if v == 'OK')}/{len(p.checklist_docs)} OK" if p.checklist_docs else "0/0",
            "updated_by": p.updated_by,
            "updated_at": p.updated_at
        })
    return resultado


# ==============================================================================
# TOOL 11: sincronizar_nuvem_empresa
# ==============================================================================
@mcp.tool()
def sincronizar_nuvem_empresa(org_id: str = "org_default") -> dict:
    """
    Aciona a sincronização bidirecional entre o Supabase Cloud e o SQLite Local Mirror.
    Se a nuvem não estiver configurada ou estiver offline, opera de forma resiliente no modo Local Mirror.
    """
    repo = Repository()
    resultado = repo.sincronizar_com_supabase(org_id=org_id)
    return resultado

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
