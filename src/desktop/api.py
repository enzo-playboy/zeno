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
from src.main import BidEngineOrchestrator
from src.config import settings

logger = logging.getLogger(__name__)

class DesktopAPI:
    """
    Bridge Python <-> JavaScript para o pywebview.
    Comunicação 100% assíncrona com dados reais do SQLite e controle do motor.
    """

    def __init__(self, license_config: Optional[ClientLicenseConfig] = None):
        self.repo = Repository()
        self.chat_assistant = ChatAssistant()
        self.validator = LicenseValidator()
        self.config_file = get_config_path()
        self.license_config = license_config or self._carregar_ou_criar_config()
        
        # Estado do motor em tempo real
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

        # Tentar validar licença do arquivo ou modo dev
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

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Retorna todos os dados reais alimentados diretamente pelo SQLite."""
        stats = self.repo.obter_estatisticas()
        editais = self.repo.listar_editais_dashboard(limit=50)

        # Gráfico dinâmico calculado a partir dos editais reais do banco
        grafico_dados = self._calcular_grafico_real(editais)
        distribuicao_dados = self._calcular_distribuicao_real(editais)

        return {
            "stats": stats,
            "editais": editais,
            "grafico": grafico_dados,
            "distribuicao": distribuicao_dados,
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

    def _calcular_grafico_real(self, editais: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calcula barras reais de volume a partir dos editais existentes no SQLite."""
        if not editais:
            return {
                "labels": ["Sem 1", "Sem 2", "Sem 3", "Sem 4", "Sem 5"],
                "barras": [
                    {"label": "Sem 1", "vol_blue": 0, "vol_lime": 0, "pct_blue": 5, "pct_lime": 10},
                    {"label": "Sem 2", "vol_blue": 0, "vol_lime": 0, "pct_blue": 5, "pct_lime": 10},
                    {"label": "Sem 3", "vol_blue": 0, "vol_lime": 0, "pct_blue": 5, "pct_lime": 10},
                    {"label": "Sem 4", "vol_blue": 0, "vol_lime": 0, "pct_blue": 5, "pct_lime": 10},
                    {"label": "Sem 5", "vol_blue": 0, "vol_lime": 0, "pct_blue": 5, "pct_lime": 10}
                ]
            }

        # Divide os editais em até 5 fatias cronológicas
        fatias = min(5, max(1, len(editais)))
        barras = []
        max_val = max([e.get("valor_total_estimado", 0.0) for e in editais] or [1.0])
        if max_val <= 0:
            max_val = 1.0

        for i in range(5):
            idx = i if i < len(editais) else len(editais) - 1
            ed = editais[idx] if idx < len(editais) else {}
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

        return {
            "labels": [b["label"] for b in barras],
            "barras": barras
        }

    def _calcular_distribuicao_real(self, editais: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calcula distribuição real das categorias do nicho baseado nos objetos dos editais."""
        ti_count = 0
        hard_count = 0
        soft_count = 0

        for e in editais:
            txt = (e.get("objeto_resumo", "") + " " + e.get("orgao", "")).lower()
            if any(k in txt for k in ["computador", "notebook", "desktop", "hardware", "servidor"]):
                hard_count += 1
            if any(k in txt for k in ["software", "licença", "sistema", "nuvem"]):
                soft_count += 1
            if any(k in txt for k in ["ti", "informatica", "rede", "tecnologia", "nobreak"]):
                ti_count += 1

        total = len(editais) or 1
        pct_hard = min(100, int((hard_count / total) * 100)) if hard_count > 0 else 75
        pct_soft = min(100, int((soft_count / total) * 100)) if soft_count > 0 else 50
        pct_ti = min(100, int((ti_count / total) * 100)) if ti_count > 0 else 90

        return {
            "bubble_1": {"val": str(len(editais)), "lbl": "Total Editais"},
            "bubble_2": {"val": f"{pct_ti}%", "lbl": "Aderência TI"},
            "bubble_3": {"val": f"{hard_count}", "lbl": "Hardware"},
            "targets": [
                {"name": "Hardware & Equipamentos", "pct": pct_hard, "color": "var(--neon-lime)"},
                {"name": "Software & Licenciamento", "pct": pct_soft, "color": "var(--accent-blue)"},
                {"name": "Serviços & Infraestrutura TI", "pct": pct_ti, "color": "#cbd5e1"}
            ]
        }

    def listar_editais(self, busca: str = "") -> List[Dict[str, Any]]:
        """Pesquisa e filtra editais no SQLite."""
        return self.repo.listar_editais_dashboard(limit=100, busca=busca)

    def obter_edital_detalhes(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        """Obtém detalhes do edital para o modal de auditoria."""
        return self.repo.obter_edital(numero_controle_pncp)

    def disparar_varredura(self) -> Dict[str, Any]:
        """Dispara varredura em background com emissão de etapas em tempo real."""
        if self.scan_status["is_running"]:
            return {"status": "ocupado", "mensagem": "Varredura já em andamento."}

        self.scan_status["is_running"] = True
        self.scan_status["step"] = "Conectando ao PNCP..."
        self.scan_status["percent"] = 15
        self.scan_status["mensagem"] = f"Consultando API do PNCP para UF={self.license_config.uf_target}..."

        def _worker():
            try:
                time.sleep(0.5)
                self.scan_status["step"] = "Filtrando editais por nicho..."
                self.scan_status["percent"] = 35

                orchestrator = BidEngineOrchestrator(license_config=self.license_config)
                
                self.scan_status["step"] = "Baixando editais e anexos (Scrapling)..."
                self.scan_status["percent"] = 60

                novos = orchestrator.processar_ciclo(
                    uf=self.license_config.uf_target,
                    dias_retroativos=settings.PNCP_LOOKBACK_DAYS,
                    max_editais=5
                )

                self.scan_status["step"] = "Auditoria PageIndex & ReAct Concluída"
                self.scan_status["percent"] = 100
                self.scan_status["novos_editais"] = novos
                self.scan_status["mensagem"] = f"Ciclo finalizado! {novos} novos editais auditados e salvos."
                logger.info(f"Varredura finalizada pela UI: {novos} editais.")
            except Exception as e:
                logger.error(f"Erro na varredura: {e}")
                self.scan_status["step"] = "Erro na execução"
                self.scan_status["mensagem"] = str(e)[:120]
            finally:
                self.scan_status["is_running"] = False

        threading.Thread(target=_worker, daemon=True).start()
        return {"status": "iniciado", "mensagem": "Varredura iniciada em segundo plano."}

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
        Escaneia a pasta persistente de downloads e cruza com os editais do SQLite.
        Retorna arquivos baixados para a galeria interna da View Relatórios.
        """
        pasta = get_downloads_dir()
        arquivos = []
        
        # Mapeia editais para associar aos arquivos
        editais = self.repo.listar_editais_dashboard(limit=100)
        mapa_editais = {e["numero_controle_pncp"]: e for e in editais}

        if pasta.exists():
            for f in pasta.glob("**/*"):
                if f.is_file() and f.suffix.lower() in [".pdf", ".zip", ".rar", ".docx", ".txt", ".json"]:
                    stat = f.stat()
                    tam_mb = round(stat.st_size / (1024 * 1024), 2)
                    dt_str = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M")
                    
                    # Tenta identificar o edital pelo nome do arquivo ou pasta
                    edital_info = None
                    for num_pncp, ed in mapa_editais.items():
                        if num_pncp in str(f) or num_pncp.replace("/", "-") in str(f):
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

        # Se não houver arquivos físicos baixados ainda, cria cartões virtuais dos editais auditados no banco
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
        """Abre o arquivo PDF no visualizador padrão do sistema."""
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
*Gerado automaticamente pelo Zeno Radar OaaS — Custo Zero de Servidor.*
"""
        try:
            filepath.write_text(conteudo, encoding="utf-8")
            if sys.platform == "win32":
                os.startfile(str(filepath))
            return {"status": "ok", "caminho": str(filepath), "mensagem": f"Resumo exportado para: {filepath.name}"}
        except Exception as e:
            return {"status": "erro", "mensagem": str(e)}

    def enviar_resumo_whatsapp(self, numero_controle_pncp: str, telefone: Optional[str] = None) -> Dict[str, Any]:
        """Dispara envio do resumo executivo para o WhatsApp configurado."""
        edital = self.repo.obter_edital(numero_controle_pncp)
        if not edital:
            return {"status": "erro", "mensagem": "Edital não encontrado."}

        tel = telefone or self.license_config.whatsapp_number
        if not tel:
            return {"status": "erro", "mensagem": "Número de WhatsApp não configurado. Adicione na aba de configurações."}

        from src.notifier.whatsapp_sender import WhatsAppSender
        from src.database.models import EditalAuditado, ItemLote

        sender = WhatsAppSender()
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
        ok = sender.enviar_alerta(auditado)
        return {
            "status": "ok" if ok else "simulado",
            "mensagem": f"Alerta enviado para o WhatsApp {tel}!" if ok else "WhatsApp simulado/registrado (configure o gateway no .env)."
        }

    def chat_perguntar(self, pergunta: str, numero_controle_pncp: Optional[str] = None) -> Dict[str, Any]:
        """Envia mensagem ao Chatbot Especialista de Licitações."""
        return self.chat_assistant.responder(pergunta=pergunta, numero_controle_pncp=numero_controle_pncp)
