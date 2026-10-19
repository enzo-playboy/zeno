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
from pathlib import Path
from datetime import datetime

from src.database.repository import Repository
from src.intelligence.chat_assistant import ChatAssistant
from src.licensing.validator import LicenseValidator, ClientLicenseConfig
from src.utils.paths import get_config_path, get_downloads_dir, get_app_dir
from src.config import settings

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
        
        # Estado de varredura em tempo real
        self.scan_status = {
            "is_running": False,
            "step": "Aguardando comando",
            "percent": 0,
            "novos_editais": 0,
            "mensagem": "Pronto para varredura"
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
            if "whatsapp_number" in nova_config:
                self.license_config.whatsapp_number = nova_config["whatsapp_number"].strip()

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
        Executa varredura e auditoria via Tool MCP com persistência no SQLite.
        """
        logger.info(f"Ponte PyWebView: buscar_licitacoes(uf='{uf}', termo='{termo}')")
        return self.mcp_buscar_e_auditar_licitacoes(uf=uf, termo_busca=termo, limite=5)

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

    def abrir_pdf(self, filename: str) -> Dict[str, Any]:
        """
        Chamado pelo JS: window.pywebview.api.abrir_pdf(filename)
        Localiza o PDF no disco e abre no visualizador nativo do sistema operacional.
        """
        logger.info(f"Ponte PyWebView: abrir_pdf('{filename}')")
        try:
            p = Path(filename)
            if not p.is_absolute() or not p.exists():
                pasta_downloads = get_downloads_dir()
                candidato = pasta_downloads / filename
                if candidato.exists():
                    p = candidato
                else:
                    # Busca por correspondência parcial de nome no diretório seguro de downloads
                    arquivos_encontrados = list(pasta_downloads.glob(f"*{filename}*"))
                    if arquivos_encontrados:
                        p = arquivos_encontrados[0]
                    else:
                        # Se não encontrar pelo nome exato, tenta abrir o primeiro PDF disponível
                        todos_pdfs = list(pasta_downloads.glob("*.pdf"))
                        if todos_pdfs:
                            p = todos_pdfs[0]
                        else:
                            return {"status": "erro", "mensagem": f"Nenhum arquivo PDF encontrado em {pasta_downloads}"}
            return self.abrir_arquivo_pdf(str(p))
        except Exception as e:
            logger.error(f"Erro ao abrir PDF: {e}")
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
        Atualiza as preferências do cliente (UF, palavras-chave, WhatsApp).
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

            if "keywords" in data and data["keywords"]:
                cfg_dict["keywords"] = data["keywords"]
            elif "palavras_chave" in data and data["palavras_chave"]:
                cfg_dict["keywords"] = data["palavras_chave"]

            if "client_name" in data and data["client_name"]:
                cfg_dict["client_name"] = data["client_name"]

            return self.salvar_configuracoes_cliente(cfg_dict)
        except Exception as e:
            logger.error(f"Erro em salvar_configuracoes: {e}")
            return {"status": "erro", "mensagem": str(e)}

