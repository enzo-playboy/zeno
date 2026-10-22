# -*- coding: utf-8 -*-
"""
Desktop Bridge API (Python <-> JavaScript pywebview)
Comunicação 100% assíncrona conectando a interface gráfica às MCP Tools reais do zeno_mcp.
"""

import os
import sys
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional
import re
from pathlib import Path
from datetime import datetime

from src.database.repository import Repository
from src.intelligence.chat_assistant import ChatAssistant
from src.licensing.validator import LicenseValidator, ClientLicenseConfig
from src.utils.paths import get_config_path, get_downloads_dir, get_app_dir
from src.config import settings
from src.collectors.pncp_api import PNCPClient
from src.collectors.scrapling_fetcher import ScraplingFetcher
from src.parsers.pdf_router import PDFRouter
from src.intelligence.pageindex_tree import PageIndexTree
from src.intelligence.react_auditor import AuditorReActAgent
from src.collectors.apify_client import ApifyClient
from src.cache.redis_client import get_redis_cache


# Importação das MCP Tools Reais do Servidor Central
from src.mcp_server.zeno_mcp import (
    mcp,
    buscar_e_auditar_licitacoes,
    listar_editais_salvos,
    obter_metricas_dashboard,
    consultar_clausula_edital,
    disparar_relatorio_whatsapp,
)

logger = logging.getLogger("DesktopAPI")

class DesktopAPI:
    """
    Bridge Python <-> JavaScript para o pywebview.
    Expõe as ferramentas do Servidor MCP (FastMCP) diretamente para o frontend.
    """

    def __init__(self, license_config: Optional[ClientLicenseConfig] = None):
        self.repo = Repository()
        self.chat_assistant = ChatAssistant()
        self.validator = LicenseValidator()
        self.config_file = get_config_path()
        self.license_config = license_config or self._carregar_ou_criar_config()
        self.redis_cache = get_redis_cache()
        
        # Estado de varredura geral em tempo real
        self.scan_status = {
            "is_running": False,
            "step": "Aguardando comando",
            "percent": 0,
            "novos_editais": 0,
            "mensagem": "Pronto para varredura"
        }

        # Estado de varredura por link específico em tempo real
        self.scan_link_status = {
            "is_running": False,
            "step": "Pronto",
            "percent": 0,
            "mensagem": "Aguardando link de licitação...",
            "detalhes": "",
            "logs": [],
            "url": "",
            "edital": None,
            "error": None
        }

    def _carregar_ou_criar_config(self) -> ClientLicenseConfig:
        """Carrega configuração salva no LOCALAPPDATA ou cria padrão."""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                return ClientLicenseConfig(**data)
            except Exception as e:
                logger.warning(f"Erro ao ler config_cliente.json: {e}")

        try:
            cfg = self.validator.validar_licenca(interactive_ui=False)
            self._salvar_config_arquivo(cfg)
            return cfg
        except Exception:
            cfg = ClientLicenseConfig(
                license_key="ZENO-DEV-LOCAL",
                client_name="Cliente Piloto MT",
                uf_target=settings.TARGET_UF,
                keywords=settings.target_keywords_list
            )
            self._salvar_config_arquivo(cfg)
            return cfg

    def _salvar_config_arquivo(self, cfg: ClientLicenseConfig):
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.config_file.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Erro ao salvar config_cliente.json: {e}")

    # ==========================================================================
    # EXPOSIÇÃO DIRETA DAS MCP TOOLS
    # ==========================================================================

    def mcp_obter_metricas_dashboard(self) -> Dict[str, Any]:
        """Tool MCP: obter_metricas_dashboard"""
        return obter_metricas_dashboard()

    def mcp_listar_editais_salvos(self, filtro_texto: str = "", score_minimo: float = 0.0) -> List[Dict[str, Any]]:
        """Tool MCP: listar_editais_salvos"""
        return listar_editais_salvos(filtro_texto=filtro_texto, score_minimo=score_minimo)

    def mcp_buscar_e_auditar_licitacoes(self, uf: str = "MT", termo_busca: str = "", limite: int = 5) -> Dict[str, Any]:
        """Tool MCP: buscar_e_auditar_licitacoes"""
        return buscar_e_auditar_licitacoes(uf=uf, termo_busca=termo_busca, limite=limite)

    def mcp_consultar_clausula_edital(self, pncp_id: str, pergunta_juridica: str) -> str:
        """Tool MCP: consultar_clausula_edital"""
        return consultar_clausula_edital(pncp_id=pncp_id, pergunta_juridica=pergunta_juridica)

    def mcp_disparar_relatorio_whatsapp(self, pncp_id: str) -> str:
        """Tool MCP: disparar_relatorio_whatsapp"""
        return disparar_relatorio_whatsapp(pncp_id=pncp_id)

    # ==========================================================================
    # MÉTODOS DE SUPORTE À INTERFACE GRÁFICA
    # ==========================================================================

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Retorna todos os dados reais alimentados via MCP + SQLite."""
        metricas = obter_metricas_dashboard()
        editais = listar_editais_salvos()

        return {
            "stats": {
                "total_editais": metricas["total_editais_auditados"],
                "volume_total": metricas["volume_total_disputa"],
                "media_score": metricas["score_medio"],
                "alta_aderencia": metricas["alta_aderencia"],
                "taxa_aproveitamento": metricas["taxa_aproveitamento"]
            },
            "editais": editais,
            "grafico": metricas["distribuicao_semanal"],
            "distribuicao": metricas["aderencia_por_nicho"],
            "cliente": {
                "nome": self.license_config.client_name,
                "uf": self.license_config.uf_target,
                "palavras_chave": self.license_config.keywords,
                "whatsapp": self.license_config.whatsapp_number or "",
                "hwid": self.validator.current_hwid[:16] + "...",
                "chave": self.license_config.license_key
            },
            "scan_status": self.scan_status
        }

    def listar_editais(self, busca: str = "") -> List[Dict[str, Any]]:
        """Pesquisa editais no SQLite via Tool MCP listar_editais_salvos."""
        return listar_editais_salvos(filtro_texto=busca)

    def obter_edital_detalhes(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        """Obtém detalhes do edital para o modal de auditoria."""
        return self.repo.obter_edital(numero_controle_pncp)

    def disparar_varredura(self, termo_busca: str = "") -> Dict[str, Any]:
        """Dispara varredura via MCP Tool em background com emissão de etapas."""
        if self.scan_status["is_running"]:
            return {"status": "ocupado", "mensagem": "Varredura já em andamento."}

        self.scan_status["is_running"] = True
        self.scan_status["step"] = "Varrendo PNCP e auditando PDFs via MCP..."
        self.scan_status["percent"] = 15
        self.scan_status["mensagem"] = f"Consultando API do PNCP (UF={self.license_config.uf_target})..."

        def _worker():
            try:
                time.sleep(0.3)
                self.scan_status["step"] = "Baixando editais e anexos (Scrapling Furtivo)..."
                self.scan_status["percent"] = 45

                time.sleep(0.3)
                self.scan_status["step"] = "Roteando páginas (PyMuPDF / PaddleOCR) & PageIndex Tree..."
                self.scan_status["percent"] = 70

                # Executa a tool MCP
                resultado = buscar_e_auditar_licitacoes(
                    uf=self.license_config.uf_target,
                    termo_busca=termo_busca,
                    limite=5
                )

                novos = resultado.get("total_novos_auditados", 0)
                self.scan_status["step"] = "Auditoria PageIndex & ReAct Concluída"
                self.scan_status["percent"] = 100
                self.scan_status["novos_editais"] = novos
                self.scan_status["mensagem"] = f"Varredura MCP Concluída! {novos} novos editais auditados e registrados."
                logger.info(f"Varredura MCP finalizada: {novos} novos editais.")
            except Exception as e:
                logger.error(f"Erro na varredura MCP: {e}")
                self.scan_status["step"] = "Erro na execução MCP"
                self.scan_status["mensagem"] = str(e)[:120]
            finally:
                self.scan_status["is_running"] = False

        threading.Thread(target=_worker, daemon=True).start()
        return {"status": "iniciado", "mensagem": "Varredura iniciada via MCP."}

    def obter_status_varredura(self) -> Dict[str, Any]:
        """Retorna o estado da varredura em tempo real para a barra de progresso."""
        return self.scan_status

    def obter_status_scan_link(self) -> Dict[str, Any]:
        """
        Retorna o progresso em tempo real do Scan por Link para a tela de carregamento animada.
        Chamado periodicamente pelo frontend (polling a cada 300ms).
        """
        return self.scan_link_status

    def scan_por_link(self, url: str) -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.scan_por_link(url)
        Pipeline Completo de Varredura por Link:
        1. Parser de URL PNCP (extrai CNPJ, Ano, Sequencial e ID Canônico)
        2. Consulta Cache Redis (se já auditado, devolve instantaneamente)
        3. Scraping Obrigatório da página do PNCP com Scrapling (Fetcher/Stealthy)
        4. Consulta API REST do PNCP (arquivos e lotes de itens)
        5. Download de anexos essenciais (Edital, TR, ETP) via Scrapling
        6. Leitura e OCR de PDFs com PDFRouter (PyMuPDF / PaddleOCR) & PageIndex Tree
        7. Auditoria por IA do projeto (ReAct Jurídico): extrai Leis, Brechas/Riscos, Obrigações e Pontos de Atenção
        8. Gravação no Redis Cache e persistência no SQLite
        """
        if self.scan_link_status["is_running"]:
            return {
                "status": "ocupado",
                "mensagem": "Um scan por link já está em andamento. Aguarde a finalização.",
                "progresso": self.scan_link_status
            }

        url_limpa = (url or "").strip()
        if not url_limpa:
            return {"status": "erro", "mensagem": "O link ou ID do edital não foi fornecido."}

        self.scan_link_status["is_running"] = True
        self.scan_link_status["percent"] = 5
        self.scan_link_status["step"] = "Identificando Link"
        self.scan_link_status["mensagem"] = "Validando formato e extraindo identificadores do PNCP..."
        self.scan_link_status["detalhes"] = url_limpa
        self.scan_link_status["logs"] = [f"Iniciando scan para: {url_limpa}"]
        self.scan_link_status["url"] = url_limpa
        self.scan_link_status["edital"] = None
        self.scan_link_status["error"] = None

        def _worker_scan():
            import time
            redis_cache = self.redis_cache

            def _log(msg: str):
                logger.info(f"[ScanLink] {msg}")
                self.scan_link_status["logs"].append(msg)
                if len(self.scan_link_status["logs"]) > 30:
                    self.scan_link_status["logs"] = self.scan_link_status["logs"][-30:]

            try:
                # 1. PARSER DE URL PNCP
                time.sleep(0.3)
                info_link = ScraplingFetcher.extrair_parametros_url(url_limpa)
                cnpj = info_link["cnpj"]
                ano = info_link["ano"]
                seq = info_link["sequencial"]
                num_pncp = info_link["numero_controle_pncp"]
                canonical_url = info_link["canonical_url"]

                _log(f"Link parseado com sucesso: CNPJ {cnpj} | Ano {ano} | Seq {seq} | ID: {num_pncp}")
                self.scan_link_status["percent"] = 15
                self.scan_link_status["step"] = "Link Identificado"
                self.scan_link_status["mensagem"] = f"Edital {num_pncp} identificado."
                self.scan_link_status["detalhes"] = f"CNPJ: {cnpj} • Sequencial: {seq}/{ano}"

                # 2. CONSULTA AO CACHE REDIS
                _log("Verificando se edital já existe no Cache Redis...")
                self.scan_link_status["percent"] = 25
                self.scan_link_status["step"] = "Consultando Redis"
                self.scan_link_status["mensagem"] = "Verificando se já existe auditoria em cache no Redis..."

                ed_cached = redis_cache.obter_auditoria_scan(num_pncp) or redis_cache.obter_auditoria_scan(canonical_url)
                if ed_cached and (ed_cached.get("armadilhas") or ed_cached.get("exigencias") or ed_cached.get("armadilhas_e_riscos")):
                    _log("Cache HIT no Redis! Auditoria carregada instantaneamente da memória.")
                    ed_cached["from_cache"] = True
                    self.scan_link_status["percent"] = 100
                    self.scan_link_status["step"] = "Concluído (Redis Cache)"
                    self.scan_link_status["mensagem"] = "Auditoria recuperada instantaneamente do Cache Redis!"
                    self.scan_link_status["edital"] = ed_cached
                    self.scan_link_status["is_running"] = False
                    return

                # 3. SCRAPING OBRIGATÓRIO DA PÁGINA COM SCRAPLING
                time.sleep(0.3)
                _log("Iniciando raspagem furtiva da página PNCP com Scrapling...")
                self.scan_link_status["percent"] = 40
                self.scan_link_status["step"] = "Raspagem Scrapling"
                self.scan_link_status["mensagem"] = "Raspando página do edital com Scrapling furtivo..."
                self.scan_link_status["detalhes"] = canonical_url

                fetcher = ScraplingFetcher()
                dados_scrapling = fetcher.raspar_pagina_pncp(canonical_url)
                _log(f"Página raspada ({dados_scrapling.get('origem', 'live')}). Órgão detectado: {dados_scrapling.get('orgao') or 'Órgão Licitante'}")

                # 4. CONSULTA API REST DO PNCP
                time.sleep(0.3)
                _log("Consultando API do PNCP para obter metadados oficiais e anexos...")
                self.scan_link_status["percent"] = 55
                self.scan_link_status["step"] = "API Oficial PNCP"
                self.scan_link_status["mensagem"] = "Buscando lista de anexos, documentos e itens oficiais..."

                pncp = PNCPClient()
                arquivos_info = pncp.obter_arquivos_edital(cnpj, ano, seq)
                if not arquivos_info and dados_scrapling.get("anexos_detectados"):
                    arquivos_info = dados_scrapling["anexos_detectados"]

                itens_info = pncp.obter_itens_edital(cnpj, ano, seq)
                compra_info = pncp.obter_compra_detalhes(cnpj, ano, seq) or pncp.consultar_por_numero_controle(num_pncp) or {}

                _log(f"PNCP retornou {len(arquivos_info)} anexo(s) e {len(itens_info)} item(ns).")

                # 5. DOWNLOAD DOS ANEXOS
                time.sleep(0.3)
                _log("Baixando documentos oficiais (Edital, Termo de Referência, ETP)...")
                self.scan_link_status["percent"] = 70
                self.scan_link_status["step"] = "Download de Documentos"
                self.scan_link_status["mensagem"] = "Baixando anexos prioritários para auditoria..."
                self.scan_link_status["detalhes"] = f"{len(arquivos_info)} anexo(s) localizado(s)"

                docs_baixados = fetcher.baixar_documentos_edital(num_pncp, arquivos_info)
                _log(f"{len(docs_baixados)} documento(s) pronto(s) para processamento.")

                # 6. LEITURA E ROTEAMENTO COM PDFROUTER & PAGEINDEX TREE
                time.sleep(0.3)
                _log("Processando páginas com PyMuPDF/PaddleOCR e construindo PageIndex Tree...")
                self.scan_link_status["percent"] = 82
                self.scan_link_status["step"] = "PageIndex Vectorless RAG"
                self.scan_link_status["mensagem"] = "Extraindo textos, tabelas e estruturando sumário das páginas..."

                router = PDFRouter()
                tree = PageIndexTree()
                texto_unificado = ""
                arvore_docs = None

                if docs_baixados:
                    def _score_relevancia(f: Path) -> int:
                        name = f.name.lower()
                        if "edital" in name:
                            return 100
                        if "termo" in name or "referencia" in name or "tr" in name:
                            return 80
                        if "estudo" in name or "etp" in name:
                            return 60
                        if "risco" in name:
                            return 40
                        return 10

                    docs_ordenados = sorted(docs_baixados, key=_score_relevancia, reverse=True)
                    pdf_principal = docs_ordenados[0]
                    paginas = router.processar_pdf(pdf_principal)
                    texto_unificado = router.extrair_texto_unificado_com_citacoes(paginas)
                    arvore_docs = tree.construir_arvore(paginas)
                    _log(f"PageIndex construído para {pdf_principal.name} ({len(paginas)} páginas).")

                # 7. AUDITORIA COM OS MODELOS DE IA DO PROJETO (REACT AUDITOR)
                time.sleep(0.3)
                _log("Executando modelos de IA do projeto: analisando Brechas, Leis, Requisitos e Pontos de Atenção...")
                self.scan_link_status["percent"] = 92
                self.scan_link_status["step"] = "Auditoria por IA"
                self.scan_link_status["mensagem"] = "IA identificando cláusulas abusivas, leis aplicáveis e exigências cruciais..."

                raw_meta = {
                    "numeroControlePNCP": num_pncp,
                    "orgaoEntidade": {
                        "razaoSocial": (compra_info.get("orgaoEntidade", {}).get("razaoSocial") if isinstance(compra_info.get("orgaoEntidade"), dict) else "") or dados_scrapling.get("orgao") or "Órgão Licitante",
                        "cnpj": cnpj
                    },
                    "modalidadeNome": compra_info.get("modalidadeNome") or dados_scrapling.get("modalidade") or "Pregão Eletrônico",
                    "objetoCompra": compra_info.get("objetoCompra") or dados_scrapling.get("objeto") or "Contratação Pública",
                    "valorTotalEstimado": compra_info.get("valorTotalEstimado") or dados_scrapling.get("valor_estimado") or 0.0,
                    "dataEncerramentoProposta": compra_info.get("dataEncerramentoProposta") or dados_scrapling.get("data_encerramento") or "",
                    "linkSistemaOrigem": compra_info.get("linkSistemaOrigem") or dados_scrapling.get("link_disputa") or "PNCP Oficial"
                }

                auditor = AuditorReActAgent()
                ed_auditado = auditor.auditar_edital(
                    raw_pncp_item=raw_meta,
                    arvore_docs=arvore_docs,
                    itens_pncp=itens_info,
                    texto_completo=texto_unificado
                )

                ed_auditado.link_pncp = canonical_url
                ed_auditado.link_disputa = raw_meta.get("linkSistemaOrigem")

                _log(f"Auditoria IA concluída! Score: {ed_auditado.score_aderencia}/10. Brechas detectadas: {len(ed_auditado.armadilhas_e_riscos)}.")

                # 8. PERSISTÊNCIA NO REDIS E NO SQLITE
                time.sleep(0.3)
                _log("Gravando resultados no Cache Redis e no banco de dados SQLite...")
                self.scan_link_status["percent"] = 98
                self.scan_link_status["step"] = "Persistência Redis & SQLite"
                self.scan_link_status["mensagem"] = "Armazenando auditoria estruturada no Redis e SQLite..."

                self.repo.salvar_edital(ed_auditado, org_id="org_default", user_name="Scan Link")
                ed_salvo = self.repo.obter_edital(num_pncp) or ed_auditado.model_dump()
                if isinstance(ed_salvo, dict):
                    ed_salvo["leis_aplicaveis"] = ed_auditado.leis_aplicaveis
                    ed_salvo["coisas_obrigatorias"] = ed_auditado.coisas_obrigatorias
                    ed_salvo["pontos_atencao"] = ed_auditado.pontos_atencao
                    ed_salvo["link_pncp"] = canonical_url
                    ed_salvo["link_disputa"] = ed_auditado.link_disputa

                redis_cache.salvar_auditoria_scan(num_pncp, ed_salvo)
                redis_cache.salvar_auditoria_scan(canonical_url, ed_salvo)
                _log("Resultados armazenados no Redis e SQLite com sucesso.")

                # 9. FINALIZAÇÃO
                self.scan_link_status["percent"] = 100
                self.scan_link_status["step"] = "Auditoria Concluída"
                self.scan_link_status["mensagem"] = f"Edital {num_pncp} auditado com sucesso!"
                self.scan_link_status["detalhes"] = f"Score: {ed_auditado.score_aderencia}/10 • {len(ed_auditado.armadilhas_e_riscos)} Riscos/Brechas mapeados"
                self.scan_link_status["edital"] = ed_salvo
                _log("Scan por link finalizado com sucesso!")
            except Exception as e:
                logger.error(f"Erro no processamento do scan por link: {e}", exc_info=True)
                _log(f"ERRO: {str(e)[:150]}")
                self.scan_link_status["error"] = str(e)
                self.scan_link_status["step"] = "Erro no Scan"
                self.scan_link_status["mensagem"] = f"Falha ao processar link: {str(e)[:120]}"
            finally:
                self.scan_link_status["is_running"] = False

        threading.Thread(target=_worker_scan, daemon=True).start()
        return {
            "status": "iniciado",
            "mensagem": "Scan por link iniciado. Acompanhe o progresso em tempo real.",
            "url": url_limpa
        }

    def salvar_configuracoes_cliente(self, nova_config: Dict[str, Any]) -> Dict[str, Any]:
        """Salva as configurações de busca do cliente no arquivo JSON persistente."""
        try:
            if "client_name" in nova_config and nova_config["client_name"]:
                self.license_config.client_name = nova_config["client_name"].strip()
            if "uf_target" in nova_config and nova_config["uf_target"]:
                self.license_config.uf_target = nova_config["uf_target"].strip().upper()
            if "keywords" in nova_config:
                kws = nova_config["keywords"]
                if isinstance(kws, str):
                    self.license_config.keywords = [k.strip().lower() for k in kws.split(",") if k.strip()]
                elif isinstance(kws, list):
                    self.license_config.keywords = [str(k).strip().lower() for k in kws if str(k).strip()]
            if "negative_keywords" in nova_config:
                nkws = nova_config["negative_keywords"]
                if isinstance(nkws, str):
                    self.license_config.negative_keywords = [k.strip().lower() for k in nkws.split(",") if k.strip()]
                elif isinstance(nkws, list):
                    self.license_config.negative_keywords = [str(k).strip().lower() for k in nkws if str(k).strip()]
            if "valor_minimo" in nova_config:
                try:
                    self.license_config.valor_minimo = float(nova_config["valor_minimo"]) if nova_config["valor_minimo"] is not None and str(nova_config["valor_minimo"]).strip() != "" else None
                except (ValueError, TypeError):
                    self.license_config.valor_minimo = None
            if "valor_maximo" in nova_config:
                try:
                    self.license_config.valor_maximo = float(nova_config["valor_maximo"]) if nova_config["valor_maximo"] is not None and str(nova_config["valor_maximo"]).strip() != "" else None
                except (ValueError, TypeError):
                    self.license_config.valor_maximo = None
            if "modalidades" in nova_config:
                mods = nova_config["modalidades"]
                if isinstance(mods, list):
                    self.license_config.modalidades = [str(m).strip().upper() for m in mods if str(m).strip()]
                elif isinstance(mods, str):
                    self.license_config.modalidades = [m.strip().upper() for m in mods.split(",") if m.strip()]
            if "municipio_filtro" in nova_config:
                self.license_config.municipio_filtro = str(nova_config["municipio_filtro"]).strip() if nova_config["municipio_filtro"] else None
            if "score_minimo" in nova_config:
                try:
                    self.license_config.score_minimo = int(nova_config["score_minimo"]) if nova_config["score_minimo"] is not None and str(nova_config["score_minimo"]).strip() != "" else None
                except (ValueError, TypeError):
                    self.license_config.score_minimo = None
            if "whatsapp_number" in nova_config:
                self.license_config.whatsapp_number = nova_config["whatsapp_number"].strip() if nova_config["whatsapp_number"] else ""

            self._salvar_config_arquivo(self.license_config)
            logger.info("Configurações do cliente salvas com sucesso.")
            return {"status": "ok", "mensagem": "Configurações salvas com sucesso!"}
        except Exception as e:
            logger.error(f"Erro ao salvar configurações: {e}")
            return {"status": "erro", "mensagem": str(e)}

    def listar_relatorios_pdfs(self) -> List[Dict[str, Any]]:
        """
        Escaneia a pasta persistente %LOCALAPPDATA%\\ZenoRadar\\downloads\\
        e cruza com os editais do SQLite para exibir na Galeria Interna.
        """
        pasta = get_downloads_dir()
        arquivos = []
        
        editais = self.repo.listar_editais_dashboard(limit=100)
        mapa_editais = {e["numero_controle_pncp"]: e for e in editais}

        if pasta.exists():
            for f in pasta.glob("**/*"):
                if f.is_file() and f.suffix.lower() in [".pdf", ".zip", ".rar", ".docx", ".txt", ".json"]:
                    stat = f.stat()
                    tam_mb = round(stat.st_size / (1024 * 1024), 2)
                    dt_str = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M")
                    
                    edital_info = None
                    for num_pncp, ed in mapa_editais.items():
                        clean_num = num_pncp.replace("/", "_").replace("-", "_")
                        if num_pncp in str(f) or clean_num in str(f):
                            edital_info = ed
                            break

                    arquivos.append({
                        "nome": f.name,
                        "caminho": str(f.resolve()),
                        "extensao": f.suffix.lower().replace(".", "").upper(),
                        "tamanho_mb": tam_mb if tam_mb > 0.01 else "< 0.01",
                        "data_modificacao": dt_str,
                        "numero_pncp": edital_info["numero_controle_pncp"] if edital_info else "Geral",
                        "orgao": edital_info["orgao"] if edital_info else "Documento de Licitação"
                    })

        # Fallback para relatórios virtuais de editais já auditados caso não haja arquivos físicos
        if not arquivos:
            for ed in editais:
                arquivos.append({
                    "nome": f"Relatorio_Auditoria_{ed['numero_controle_pncp'].replace('/', '_')}.md",
                    "caminho": f"relatorio_virtual_{ed['numero_controle_pncp']}",
                    "extensao": "OAAS",
                    "tamanho_mb": "0.15",
                    "data_modificacao": ed.get("created_at") or datetime.now().strftime("%d/%m/%Y"),
                    "numero_pncp": ed["numero_controle_pncp"],
                    "orgao": ed["orgao"]
                })

        return arquivos

    def abrir_arquivo_pdf(self, caminho: str) -> Dict[str, Any]:
        """Abre o arquivo PDF no visualizador padrão do sistema operacional."""
        try:
            p = Path(caminho)
            if p.exists():
                if sys.platform == "win32":
                    os.startfile(str(p))
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", str(p)])
                return {"status": "ok", "mensagem": f"Arquivo {p.name} aberto."}
            return {"status": "erro", "mensagem": "Arquivo não encontrado."}
        except Exception as e:
            logger.error(f"Erro ao abrir arquivo: {e}")
            return {"status": "erro", "mensagem": str(e)}

    def exportar_resumo_edital(self, numero_controle_pncp: str) -> Dict[str, Any]:
        """Gera e salva um relatório resumido em Markdown na pasta de downloads."""
        edital = self.repo.obter_edital(numero_controle_pncp)
        if not edital:
            return {"status": "erro", "mensagem": "Edital não encontrado."}

        pasta = get_downloads_dir()
        filename = f"Resumo_Executivo_{numero_controle_pncp.replace('/', '_').replace('-', '_')}.md"
        filepath = pasta / filename

        conteudo = f"""# Relatório de Auditoria OaaS — Zeno Radar
**Número de Controle PNCP:** {edital['numero_controle_pncp']}  
**Órgão Licitante:** {edital['orgao']}  
**Modalidade:** {edital['modalidade']}  
**Data da Disputa:** {edital['data_disputa']}  
**Valor Total Estimado:** R$ {edital['valor_total_estimado']:,.2f}  
**Score de Aderência:** {edital['score_aderencia']}/10  

---

### Recomendação Executiva
{edital['recomendacao_executiva']}

---

### Exigências Críticas de Habilitação (Qualificação Técnica e Financeira)
{chr(10).join(['- ' + ex for ex in edital['exigencias']])}

---

### Armadilhas e Riscos Operacionais Identificados
{chr(10).join(['- ' + ar for ar in edital['armadilhas']])}

---

### Itens de Interesse do Cliente
{chr(10).join([f"- Item {it['item']}: {it['descricao']} (Qtd: {it['quantidade']} | Teto: R$ {it['valor_unitario_teto']:,.2f})" for it in edital['itens']])}

---
*Gerado automaticamente pelo Zeno Radar OaaS via MCP Tool.*
"""
        try:
            filepath.write_text(conteudo, encoding="utf-8")
            if sys.platform == "win32":
                os.startfile(str(filepath))
            return {"status": "ok", "caminho": str(filepath), "mensagem": f"Resumo exportado para: {filepath.name}"}
        except Exception as e:
            return {"status": "erro", "mensagem": str(e)}

    def enviar_resumo_whatsapp(self, numero_controle_pncp: str) -> Dict[str, Any]:
        """Dispara envio do resumo executivo para o WhatsApp via Tool MCP."""
        res_str = disparar_relatorio_whatsapp(pncp_id=numero_controle_pncp)
        return {
            "status": "ok",
            "mensagem": res_str
        }

    def chat_perguntar(self, pergunta: str, numero_controle_pncp: Optional[str] = None) -> Dict[str, Any]:
        """Envia mensagem ao Chatbot ReAct equipado com as tools MCP."""
        return self.chat_assistant.responder(pergunta=pergunta, numero_controle_pncp=numero_controle_pncp)

    # ==========================================================================
    # MÉTODOS DA PONTE PYWEBVIEW (CHAMADOS PELO JAVASCRIPT FRONT-END)
    # ==========================================================================

    def buscar_licitacoes(self, uf: str = "MT", termo: str = "") -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.buscar_licitacoes(uf, termo)
        Executa consulta ao Apify (Actor brasildados/editais-licitacoes-brasil),
        persiste imediatamente no SQLite e Supabase, grava em cache no Redis
        e mescla com editais já salvos para que nada suma ou se perca.
        """
        logger.info(f"Ponte PyWebView: buscar_licitacoes via Apify (uf='{uf}', termo='{termo}')")
        uf_clean = (uf or "MT").strip().upper()
        termo_clean = (termo or "").strip()

        # 1. Consulta prioritária no Apify
        itens_apify = []
        try:
            apify = ApifyClient()
            if apify.is_configured():
                logger.info(f"[Apify] Disparando Actor para UF={uf_clean}, Termo='{termo_clean}'...")
                itens_apify = apify.consultar_editais(termo=termo_clean, uf=uf_clean, tam_pagina=50)
            else:
                logger.info("[Apify] API token não configurado. Utilizando coletor PNCP direto...")
                pncp = PNCPClient()
                if termo_clean:
                    itens_apify = pncp.buscar_editais_search(termo=termo_clean, uf=uf_clean, tam_pagina=50)
                else:
                    itens_apify = pncp.buscar_contratacoes_abertas(uf=uf_clean, dias_retroativos=settings.PNCP_LOOKBACK_DAYS, tamanho_pagina=50)
        except Exception as e:
            logger.error(f"Erro na consulta Apify/PNCP: {e}")

        # Se falhou e houver cache no Redis, recupera do cache
        if not itens_apify:
            cache_hit = self.redis_cache.obter_busca_editais(uf_clean, termo_clean)
            if cache_hit:
                logger.info(f"[Redis] Retornando {len(cache_hit)} editais do cache Redis.")
                return {
                    "status": "sucesso",
                    "origem": "redis_cache",
                    "total": len(cache_hit),
                    "editais": cache_hit
                }

        editais_processados = []
        mapa_processados = {}

        # 2. Processa, normaliza e PERSISTE cada edital no SQLite, Supabase e Redis
        for item in itens_apify:
            num_pncp = str(item.get("numeroControlePNCP") or item.get("numero_controle_pncp") or "").strip()
            if not num_pncp:
                continue

            orgao_info = item.get("orgaoEntidade", {})
            razao = orgao_info.get("razaoSocial") if isinstance(orgao_info, dict) else str(orgao_info or "")
            cnpj_org = orgao_info.get("cnpj") if isinstance(orgao_info, dict) else ""
            ano = item.get("anoCompra") or datetime.now().year
            seq = item.get("sequencialCompra") or 1
            cnpj_clean = "".join(filter(str.isdigit, str(cnpj_org)))
            url_oficial = f"https://pncp.gov.br/app/editais/{cnpj_clean}/{ano}/{seq}" if cnpj_clean else f"https://pncp.gov.br/app/editais?q={num_pncp}"

            ed_dict = {
                "numero_controle_pncp": num_pncp,
                "orgao": razao or "Órgão Público",
                "modalidade": (item.get("modalidadeNome") or item.get("modalidade") or "PREGÃO ELETRÔNICO").upper(),
                "objeto_resumo": item.get("objetoCompra") or item.get("objeto") or item.get("informacaoComplementar") or "Contratação Pública",
                "valor_total_estimado": float(item.get("valorTotalEstimado") or item.get("valorEstimado") or 0.0),
                "score_aderencia": None,
                "data_disputa": item.get("dataAberturaProposta") or item.get("dataEncerramentoProposta") or "Aberta",
                "url_oficial": url_oficial,
                "uf": item.get("uf") or uf_clean,
                "municipioNome": item.get("municipioNome") or "",
                "origem_consulta": "apify",
                "status_processamento": "COLETADO",
                "raw": item
            }

            # Preserva dados de auditoria se já foi auditado anteriormente
            ed_salvo = self.repo.obter_edital(num_pncp)
            if ed_salvo:
                if ed_salvo.get("score_aderencia") is not None:
                    ed_dict["score_aderencia"] = ed_salvo.get("score_aderencia")
                if ed_salvo.get("exigencias"):
                    ed_dict["exigencias"] = ed_salvo.get("exigencias")
                if ed_salvo.get("armadilhas"):
                    ed_dict["armadilhas"] = ed_salvo.get("armadilhas")
                if ed_salvo.get("status_processamento"):
                    ed_dict["status_processamento"] = ed_salvo.get("status_processamento")

            # Persistência imediata no SQLite e Supabase
            try:
                self.repo.salvar_edital_coletado(ed_dict)
            except Exception as e_save:
                logger.warning(f"Erro ao persistir edital {num_pncp}: {e_save}")

            # Cache imediato no Redis
            try:
                self.redis_cache.salvar_edital_cache(num_pncp, ed_dict)
            except Exception:
                pass

            mapa_processados[num_pncp] = ed_dict
            editais_processados.append(ed_dict)

        # 3. Mescla com editais já persistidos no banco local para que o histórico NÃO suma
        try:
            editais_banco = self.repo.listar_editais_dashboard(limit=100, busca=termo_clean)
            for eb in editais_banco:
                num = eb.get("numero_controle_pncp")
                if num and num not in mapa_processados:
                    m = re.match(r'(\d{14})-\d+-0*(\d+)/(\d{4})', num)
                    url_of = f"https://pncp.gov.br/app/editais/{m.group(1)}/{m.group(3)}/{int(m.group(2))}" if m else f"https://pncp.gov.br/app/editais?q={num}"

                    item_banco = {
                        "numero_controle_pncp": num,
                        "orgao": eb.get("orgao") or "Órgão Público",
                        "modalidade": (eb.get("modalidade") or "PREGÃO ELETRÔNICO").upper(),
                        "objeto_resumo": eb.get("objeto_resumo") or "",
                        "valor_total_estimado": float(eb.get("valor_total_estimado") or 0.0),
                        "score_aderencia": eb.get("score_aderencia"),
                        "data_disputa": eb.get("data_disputa") or "A definir",
                        "url_oficial": url_of,
                        "uf": uf_clean,
                        "municipioNome": "",
                        "origem_consulta": "persistido",
                        "status_processamento": eb.get("status_processamento") or "COLETADO",
                        "exigencias": eb.get("exigencias", []),
                        "armadilhas": eb.get("armadilhas", []),
                        "raw": eb.get("raw", {})
                    }
                    editais_processados.append(item_banco)
                    mapa_processados[num] = item_banco
        except Exception as e_banco:
            logger.warning(f"Erro ao mesclar com editais do banco: {e_banco}")

        # 4. Grava a lista consolidada no Redis Cache para buscas instantâneas
        try:
            self.redis_cache.salvar_busca_editais(uf_clean, termo_clean, editais_processados)
        except Exception as e_rc:
            logger.warning(f"Erro ao salvar busca no Redis: {e_rc}")

        logger.info(f"[Zeno] Total de {len(editais_processados)} editais consolidados (Apify + SQLite + Supabase + Redis).")
        return {
            "status": "sucesso",
            "origem": "apify_e_persistidos",
            "total": len(editais_processados),
            "editais": editais_processados
        }

    def auditar_edital(self, numero_controle_pncp: str) -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.auditar_edital(numero_controle_pncp)
        Aciona os Agentes de Varredura e Auditoria (Scrapling, PDFRouter, PageIndex Tree, ReAct Auditor)
        para baixar anexos oficiais, extrair lotes/itens e mapear riscos jurídicos e habilitação.
        """
        logger.info(f"Ponte PyWebView: auditar_edital('{numero_controle_pncp}')")
        try:
            num_limpo = numero_controle_pncp.strip()

            # 1. Se já estiver auditado no banco com exigências, retorna imediatamente
            ed_salvo = self.repo.obter_edital(num_limpo)
            if ed_salvo and ed_salvo.get("exigencias"):
                return {
                    "status": "sucesso",
                    "origem": "cache_local",
                    "edital": ed_salvo
                }

            # 2. Extrai dados do identificador PNCP
            cnpj = num_limpo.split("-1-")[0] if "-1-" in num_limpo else ""
            ano = 2026
            seq = 1
            m = re.search(r"(\d{14})-\d+-0*(\d+)/(\d{4})", num_limpo)
            if m:
                cnpj = m.group(1)
                seq = int(m.group(2))
                ano = int(m.group(3))

            pncp = PNCPClient()
            fetcher = ScraplingFetcher()
            router = PDFRouter()
            tree = PageIndexTree()
            auditor = AuditorReActAgent()

            # 3. Obter arquivos e itens do PNCP
            arquivos_info = pncp.obter_arquivos_edital(cnpj, ano, seq)
            itens_info = pncp.obter_itens_edital(cnpj, ano, seq)

            # 4. Baixar arquivos via Scrapling
            docs_baixados = fetcher.baixar_documentos_edital(num_limpo, arquivos_info)

            # 5. Processar documento principal com PDFRouter e PageIndex
            texto_unificado = ""
            arvore_docs = None
            if docs_baixados:
                # Prioriza o documento oficial do Edital ou Termo de Referência
                def _score_relevancia(f: Path) -> int:
                    name = f.name.lower()
                    if "edital" in name:
                        return 100
                    if "termo" in name or "referencia" in name or "tr" in name:
                        return 80
                    if "estudo" in name or "etp" in name:
                        return 60
                    if "risco" in name:
                        return 40
                    return 10

                docs_ordenados = sorted(docs_baixados, key=_score_relevancia, reverse=True)
                pdf_principal = docs_ordenados[0]
                paginas = router.processar_pdf(pdf_principal)
                texto_unificado = router.extrair_texto_unificado_com_citacoes(paginas)
                arvore_docs = tree.construir_arvore(paginas)

            # 6. Auditoria ReAct
            raw_meta = ed_salvo or {
                "numeroControlePNCP": num_limpo,
                "orgaoEntidade": {"razaoSocial": "Órgão Público", "cnpj": cnpj},
                "modalidadeNome": "Pregão Eletrônico",
                "objetoCompra": "Contratação Pública",
                "valorTotalEstimado": 0.0
            }
            ed_auditado = auditor.auditar_edital(
                raw_pncp_item=raw_meta,
                arvore_docs=arvore_docs,
                itens_pncp=itens_info,
                texto_completo=texto_unificado
            )

            # 7. Persistência
            self.repo.salvar_edital(ed_auditado, org_id="org_default", user_name="Auditor")

            return {
                "status": "sucesso",
                "origem": "scrapling_react",
                "edital": self.repo.obter_edital(num_limpo)
            }
        except Exception as e:
            logger.error(f"Erro ao auditar edital {numero_controle_pncp}: {e}")
            return {"status": "erro", "mensagem": str(e)}

    def listar_arquivos_edital(self, numero_controle_pncp: str) -> List[Dict[str, Any]]:
        """
        Chamado pelo JS: window.pywebview.api.listar_arquivos_edital(numero_controle_pncp)
        Retorna a lista real de documentos baixados no disco para um edital específico.
        """
        logger.info(f"Ponte PyWebView: listar_arquivos_edital('{numero_controle_pncp}')")
        res = []
        try:
            num_limpo = numero_controle_pncp.strip()
            id_clean = num_limpo.replace("/", "_").replace("-", "_")
            pastas_busca = [get_downloads_dir(), Path("./downloads").resolve()]

            pastas_alvo = []
            for raiz in pastas_busca:
                if not raiz.exists():
                    continue
                for p in raiz.glob(f"*{id_clean}*"):
                    if p.is_dir() and p not in pastas_alvo:
                        pastas_alvo.append(p)
                m = re.search(r"(\d{14})-\d+-0*(\d+)/(\d{4})", num_limpo)
                if m:
                    cnpj, seq, ano = m.group(1), int(m.group(2)), int(m.group(3))
                    for p in raiz.glob(f"*{cnpj}*{seq}*{ano}*"):
                        if p.is_dir() and p not in pastas_alvo:
                            pastas_alvo.append(p)

            for pasta in pastas_alvo:
                for f in sorted(pasta.iterdir(), key=lambda x: x.name):
                    if f.is_file() and not f.name.startswith("."):
                        size_bytes = f.stat().st_size
                        if size_bytes >= 1024 * 1024:
                            tamanho_str = f"{size_bytes / (1024 * 1024):.2f} MB"
                        else:
                            tamanho_str = f"{size_bytes / 1024:.1f} KB"
                        ext = f.suffix.replace(".", "").upper() or "ARQ"
                        res.append({
                            "nome": f.name,
                            "caminho": str(f.resolve()),
                            "extensao": ext,
                            "tamanho": tamanho_str,
                            "numero_pncp": num_limpo
                        })
        except Exception as e:
            logger.error(f"Erro ao listar arquivos do edital {numero_controle_pncp}: {e}")
        return res

    def obter_metricas(self) -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.obter_metricas()
        Retorna métricas consolidadas dos editais para os cards de estatísticas.
        """
        logger.info("Ponte PyWebView: obter_metricas()")
        m = self.mcp_obter_metricas_dashboard()
        return {
            "total_editais": m.get("total_editais_auditados", 0),
            "volume_total": m.get("volume_total_disputa", 0.0),
            "score_medio": m.get("score_medio", 0.0),
            "alta_aderencia": m.get("alta_aderencia", 0),
            "taxa_aproveitamento": m.get("taxa_aproveitamento", "0%"),
            "distribuicao_semanal": m.get("distribuicao_semanal", []),
            "aderencia_por_nicho": m.get("aderencia_por_nicho", {}),
            "raw": m
        }

    def abrir_pdf(self, filename: str, numero_controle_pncp: str = "") -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.abrir_pdf(filename, numero_controle_pncp)
        Localiza o PDF no disco e abre no visualizador nativo do Windows.
        """
        logger.info(f"Ponte PyWebView: abrir_pdf('{filename}', pncp='{numero_controle_pncp}')")
        try:
            p = Path(filename)
            if p.is_absolute() and p.exists():
                return self.abrir_arquivo_pdf(str(p))

            pastas_busca = [get_downloads_dir(), Path("./downloads").resolve()]
            clean_name = Path(filename).name

            # 1. Se informou o pncp_id, pesquisa prioritariamente na pasta do edital
            if numero_controle_pncp:
                id_clean = numero_controle_pncp.replace("/", "_").replace("-", "_")
                for raiz in pastas_busca:
                    for sub in raiz.glob(f"*{id_clean}*"):
                        if sub.is_dir():
                            for f in sub.iterdir():
                                if f.is_file() and (clean_name.lower() in f.name.lower() or f.name.lower() in clean_name.lower()):
                                    return self.abrir_arquivo_pdf(str(f))

            # 2. Busca recursiva (rglob) pelo nome exato ou parcial
            for raiz in pastas_busca:
                if not raiz.exists():
                    continue
                for match in raiz.rglob(f"*{clean_name}*"):
                    if match.is_file():
                        return self.abrir_arquivo_pdf(str(match))
                
                # Fallback: primeira palavra-chave relevante do nome
                palavras = [w for w in clean_name.split() if len(w) > 3 and not w.isdigit()]
                if palavras:
                    for f in raiz.rglob("*"):
                        if f.is_file() and any(w.lower() in f.name.lower() for w in palavras):
                            return self.abrir_arquivo_pdf(str(f))

            return {"status": "erro", "mensagem": f"Arquivo '{clean_name}' não encontrado no disco local."}
        except Exception as e:
            logger.error(f"Erro ao abrir arquivo {filename}: {e}")
            return {"status": "erro", "mensagem": str(e)}

    def perguntar_juridico(self, pncp_id: str, pergunta: str) -> str:
        """
        Chamado pelo JS: window.pywebview.api.perguntar_juridico(pncp_id, pergunta)
        Consulta os termos de referência e cláusulas de habilitação via IA com citações de página.
        """
        logger.info(f"Ponte PyWebView: perguntar_juridico(pncp_id='{pncp_id}', pergunta='{pergunta}')")
        try:
            edital_alvo = pncp_id
            if pncp_id:
                # Mapeia apelidos da interface para o número de controle PNCP real no SQLite
                editais = self.repo.listar_editais()
                for ed in editais:
                    if pncp_id.lower() in ed["numero_controle_pncp"].lower() or pncp_id.lower() in ed["orgao"].lower():
                        edital_alvo = ed["numero_controle_pncp"]
                        break

            res = self.chat_assistant.responder(pergunta=pergunta, numero_controle_pncp=edital_alvo)
            if isinstance(res, dict):
                resposta = res.get("resposta", "")
            else:
                resposta = str(res)
            return resposta.replace("\n", "<br>")
        except Exception as e:
            logger.error(f"Erro em perguntar_juridico: {e}")
            return f"Erro ao processar consulta jurídica: {str(e)}"

    def salvar_configuracoes(self, config_json: Any) -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.salvar_configuracoes(config_json)
        Atualiza as preferências do cliente (UF, palavras-chave, palavras negativas, valores, modalidades, WhatsApp).
        """
        logger.info(f"Ponte PyWebView: salvar_configuracoes({config_json})")
        try:
            if isinstance(config_json, str):
                data = json.loads(config_json)
            elif isinstance(config_json, dict):
                data = config_json
            else:
                data = {}

            cfg_dict = {}
            if "uf" in data and data["uf"]:
                cfg_dict["uf_target"] = data["uf"]
            elif "uf_target" in data and data["uf_target"]:
                cfg_dict["uf_target"] = data["uf_target"]

            if "whatsapp" in data and data["whatsapp"]:
                cfg_dict["whatsapp_number"] = data["whatsapp"]
            elif "whatsapp_number" in data and data["whatsapp_number"]:
                cfg_dict["whatsapp_number"] = data["whatsapp_number"]

            if "keywords" in data and data["keywords"] is not None:
                cfg_dict["keywords"] = data["keywords"]
            elif "palavras_chave" in data and data["palavras_chave"] is not None:
                cfg_dict["keywords"] = data["palavras_chave"]

            if "negative_keywords" in data and data["negative_keywords"] is not None:
                cfg_dict["negative_keywords"] = data["negative_keywords"]
            elif "palavras_negativas" in data and data["palavras_negativas"] is not None:
                cfg_dict["negative_keywords"] = data["palavras_negativas"]

            if "valor_minimo" in data:
                cfg_dict["valor_minimo"] = data["valor_minimo"]
            if "valor_maximo" in data:
                cfg_dict["valor_maximo"] = data["valor_maximo"]
            if "modalidades" in data and data["modalidades"] is not None:
                cfg_dict["modalidades"] = data["modalidades"]
            if "municipio_filtro" in data:
                cfg_dict["municipio_filtro"] = data["municipio_filtro"]
            if "score_minimo" in data:
                cfg_dict["score_minimo"] = data["score_minimo"]

            if "client_name" in data and data["client_name"]:
                cfg_dict["client_name"] = data["client_name"]

            return self.salvar_configuracoes_cliente(cfg_dict)
        except Exception as e:
            logger.error(f"Erro em salvar_configuracoes: {e}")
            return {"status": "erro", "mensagem": str(e)}

    def obter_configuracoes(self) -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.obter_configuracoes()
        Retorna o conjunto completo de configurações e filtros ativos para pré-popular a UI.
        """
        logger.info("Ponte PyWebView: obter_configuracoes()")
        return {
            "uf": self.license_config.uf_target,
            "keywords": self.license_config.keywords,
            "negative_keywords": getattr(self.license_config, "negative_keywords", []),
            "valor_minimo": getattr(self.license_config, "valor_minimo", None),
            "valor_maximo": getattr(self.license_config, "valor_maximo", None),
            "modalidades": getattr(self.license_config, "modalidades", ["PREGÃO ELETRÔNICO", "DISPENSA ELETRÔNICA", "CONCORRÊNCIA"]),
            "municipio_filtro": getattr(self.license_config, "municipio_filtro", "") or "",
            "score_minimo": getattr(self.license_config, "score_minimo", None),
            "whatsapp": self.license_config.whatsapp_number or "",
            "client_name": self.license_config.client_name or ""
        }


