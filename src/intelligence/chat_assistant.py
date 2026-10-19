# -*- coding: utf-8 -*-
"""
Chatbot Jurídico IA — Especialista em Licitações Públicas (Lei 14.133/2021)
Totalmente conectado ao Servidor Central MCP (FastMCP) e ReAct Auditor (AgentScope).
"""

import json
import logging
import re
from typing import Optional, Dict, Any, List

from src.database.repository import Repository
from src.config import settings
from src.mcp_server.zeno_mcp import (
    buscar_e_auditar_licitacoes,
    listar_editais_salvos,
    obter_metricas_dashboard,
    consultar_clausula_edital,
    disparar_relatorio_whatsapp,
)

logger = logging.getLogger("ChatAssistant")

class ChatAssistant:
    """
    Assistente Jurídico e Chatbot ReAct do Zeno Radar.
    Conectado às ferramentas MCP reais para busca no PNCP, navegação em árvore
    PageIndex, consulta de cláusulas com citação [Pág. X] e disparo WhatsApp.
    """

    def __init__(self):
        self.repo = Repository()

    def responder(self, pergunta: str, numero_controle_pncp: Optional[str] = None) -> Dict[str, Any]:
        """
        Recebe a pergunta do usuário e decide via ReAct qual Tool MCP acionar:
        1. buscar_e_auditar_licitacoes
        2. consultar_clausula_edital
        3. disparar_relatorio_whatsapp
        4. obter_metricas_dashboard
        5. listar_editais_salvos
        """
        pergunta_limpa = pergunta.strip()
        if not pergunta_limpa:
            return {
                "resposta": "Por favor, digite uma dúvida ou selecione um edital para análise.",
                "referencias": []
            }

        logger.info(f"Chatbot ReAct recebendo pergunta: '{pergunta_limpa}' (Edital em Foco: {numero_controle_pncp})")
        p_lower = pergunta_limpa.lower()

        # ----------------------------------------------------------------------
        # INTENÇÃO 1: Busca e Varredura no PNCP via MCP Tool
        # Ex: "Busque licitações de ar condicionado em MT", "Procurar Novas Licitações no MT"
        # ----------------------------------------------------------------------
        palavras_busca = ["busque", "buscar", "procure", "procurar", "pesquise", "pesquisar", "varra", "varrer", "encontre"]
        eh_comando_busca = any(cb in p_lower for cb in palavras_busca) and ("licitaç" in p_lower or "pregao" in p_lower or "pregão" in p_lower or "edital" in p_lower or "mt" in p_lower or "pncp" in p_lower or "novas" in p_lower)

        if eh_comando_busca:
            # Extrair UF se mencionada (ex: MT, SP, etc.)
            uf_match = re.search(r'\b(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b', pergunta_limpa, re.IGNORECASE)
            uf_alvo = uf_match.group(1).upper() if uf_match else settings.TARGET_UF

            # Extrair termo de busca (ex: "ar condicionado", "computadores")
            termo = ""
            for pref in ["licitações de ", "licitações para ", "licitacoes de ", "pregões de ", "pregoes de ", "editais de ", "sobre "]:
                if pref in p_lower:
                    partes = p_lower.split(pref, 1)
                    if len(partes) > 1:
                        termo = partes[1].split(" em ")[0].split(" no ")[0].split(" na ")[0].strip()
                        break

            if not termo and "ar condicionado" in p_lower:
                termo = "ar condicionado"
            elif not termo and any(k in p_lower for k in ["computador", "ti", "notebook", "software", "servidor"]):
                termo = "informatica"

            logger.info(f"ReAct Tool Dispatch -> buscar_e_auditar_licitacoes(uf='{uf_alvo}', termo_busca='{termo}')")
            resultado_mcp = buscar_e_auditar_licitacoes(uf=uf_alvo, termo_busca=termo, limite=5)

            editais_auditados = resultado_mcp.get("editais", [])
            total_novos = resultado_mcp.get("total_novos_auditados", 0)

            if not editais_auditados:
                return {
                    "resposta": f"🔍 **Varredura PNCP concluída via MCP Tool ({uf_alvo}):**\n\n"
                                f"Nenhuma contratação aberta com propostas ativas foi encontrada para o termo **'{termo or 'geral'}'** na janela recente de {settings.PNCP_LOOKBACK_DAYS} dias.\n"
                                f"Tente buscar outros termos como *'computadores'*, *'softwares'* ou ampliar a região.",
                    "referencias": []
                }

            linhas_editais = []
            for ed in editais_auditados:
                val_fmt = f"R$ {ed['valor_total_estimado']:,.2f}" if ed.get('valor_total_estimado') else "Sob Demanda"
                linhas_editais.append(
                    f"• 🏢 **{ed['orgao']}** (PNCP: `{ed['numero_controle_pncp']}`)\n"
                    f"  - Objeto: *{ed['objeto_resumo'][:100]}...*\n"
                    f"  - Valor: **{val_fmt}** | Score: **{ed['score_aderencia']}/10** 🎯\n"
                    f"  - *{ed['recomendacao_executiva']}*\n"
                )

            return {
                "resposta": f"🎯 **Varredura Realizada via MCP Tool (`buscar_e_auditar_licitacoes`):**\n\n"
                            f"Identifiquei e processei **{len(editais_auditados)} editais** ({total_novos} novos salvos no SQLite persistente) em **{uf_alvo}**:\n\n"
                            + "\n".join(linhas_editais) +
                            f"\nVocê pode selecionar qualquer um desses editais no seletor **📄 Edital em Foco** acima para analisar exigências técnicas, amostras e prazos!",
                "referencias": []
            }

        # ----------------------------------------------------------------------
        # INTENÇÃO 2: Disparo de Relatório WhatsApp via MCP Tool
        # Ex: "Envie no WhatsApp", "Mandar relatório via WhatsApp"
        # ----------------------------------------------------------------------
        if any(w in p_lower for w in ["whatsapp", "zap", "enviar relatório", "disparar relatório", "mandar no whatsapp"]):
            edital_id = numero_controle_pncp
            if not edital_id:
                # Se não selecionado no topo, checa se mencionou algum número
                pncp_match = re.search(r'\d{14}-\d+-\d+/\d{4}', pergunta_limpa)
                if pncp_match:
                    edital_id = pncp_match.group(0)

            if not edital_id:
                editais_existentes = self.repo.listar_editais_dashboard(limit=1)
                if editais_existentes:
                    edital_id = editais_existentes[0]["numero_controle_pncp"]

            if edital_id:
                logger.info(f"ReAct Tool Dispatch -> disparar_relatorio_whatsapp(pncp_id='{edital_id}')")
                resposta_zap = disparar_relatorio_whatsapp(pncp_id=edital_id)
                return {
                    "resposta": resposta_zap,
                    "referencias": []
                }
            return {
                "resposta": "Por favor, selecione um edital no seletor **📄 Edital em Foco** acima para disparar o relatório via WhatsApp.",
                "referencias": []
            }

        # ----------------------------------------------------------------------
        # INTENÇÃO 3: Métricas do Dashboard via MCP Tool
        # Ex: "Qual o volume total?", "Quais as métricas do banco?"
        # ----------------------------------------------------------------------
        if any(w in p_lower for w in ["métrica", "metrica", "volume total", "quantos editais", "estatística"]):
            logger.info("ReAct Tool Dispatch -> obter_metricas_dashboard()")
            metricas = obter_metricas_dashboard()
            return {
                "resposta": f"📊 **Métricas Atuais do Motor Zeno Radar (via MCP `obter_metricas_dashboard`):**\n\n"
                            f"• **Volume Total em Disputa:** R$ {metricas['volume_total_disputa']:,.2f}\n"
                            f"• **Total de Editais Auditados:** {metricas['total_editais_auditados']}\n"
                            f"• **Score Médio de Aderência:** {metricas['score_medio']}/10\n"
                            f"• **Oportunidades de Alta Compatibilidade:** {metricas['alta_aderencia']} ({metricas['taxa_aproveitamento']}% do banco)\n\n"
                            f"Todos os dados são extraídos diretamente do seu SQLite persistente.",
                "referencias": []
            }

        # ----------------------------------------------------------------------
        # INTENÇÃO 4: Consulta a Cláusula de Edital via MCP Tool (PageIndex Tree)
        # Ex: "Esse edital exige amostra ou visita técnica?", "Prazo de Entrega", "Atestado Técnico"
        # ----------------------------------------------------------------------
        target_pncp = numero_controle_pncp

        # Se o usuário citou o ID no texto
        pncp_match = re.search(r'\d{14}-\d+-\d+/\d{4}', pergunta_limpa)
        if pncp_match:
            target_pncp = pncp_match.group(0)

        # Se nenhum selecionado mas só existe 1 no banco, adota o único disponível
        if not target_pncp:
            existentes = self.repo.listar_editais_dashboard(limit=2)
            if len(existentes) == 1:
                target_pncp = existentes[0]["numero_controle_pncp"]

        if target_pncp:
            logger.info(f"ReAct Tool Dispatch -> consultar_clausula_edital(pncp_id='{target_pncp}', pergunta='{pergunta_limpa}')")
            resposta_clausula = consultar_clausula_edital(
                pncp_id=target_pncp,
                pergunta_juridica=pergunta_limpa
            )
            return {
                "resposta": resposta_clausula,
                "referencias": self._extrair_citacoes(resposta_clausula)
            }

        # ----------------------------------------------------------------------
        # INTENÇÃO 5: Dúvidas Gerais sobre a Nova Lei de Licitações (Lei 14.133/2021)
        # ----------------------------------------------------------------------
        if "lei 14.133" in p_lower or "nova lei" in p_lower:
            return {
                "resposta": "A **Lei 14.133/2021** (Nova Lei de Licitações e Contratos Administrativos) unificou o regime licitatório brasileiro:\n\n"
                            "• **Formato Eletrônico Obrigatório:** Presencial apenas em situações excepcionais motivadas (Art. 17, § 2º).\n"
                            "• **PNCP Central:** Divulgação obrigatória de todos os editais e atas no Portal Nacional de Contratações Públicas (Art. 174).\n"
                            "• **Fase Recursal Única:** Julgamento e habilitação unificados com manifestação recursal imediata.\n"
                            "• **Extinção de Modalidades:** Convite e Tomada de Preços foram extintos, restando Pregão, Concorrência, Concurso, Leilão e Diálogo Competitivo.\n\n"
                            "Selecione um edital no seletor **📄 Edital em Foco** para analisar como essas regras se aplicam ao seu caso concreto!",
                "referencias": ["Lei 14.133/2021", "Art. 17, § 2º", "Art. 174"]
            }

        if any(w in p_lower for w in ["impugnação", "impugnar", "prazo de impugnar"]):
            return {
                "resposta": "⚖️ **Regra de Impugnação (Art. 164 da Lei 14.133/2021):**\n\n"
                            "• Qualquer pessoa física ou jurídica pode impugnar o edital até **3 dias úteis antes da data de abertura do certame**.\n"
                            "• A Administração tem o dever de responder no prazo de até **3 dias úteis**, limitado ao último dia anterior à abertura da disputa.\n"
                            "• Acolhida a impugnação com alteração substancial que afete a formulação das propostas, deve haver republicação com reabertura de prazo integral.",
                "referencias": ["Art. 164 da Lei 14.133/2021"]
            }

        # Mensagem padrão do assistente
        editais_salvos = self.repo.listar_editais_dashboard(limit=5)
        lista_msg = ""
        if editais_salvos:
            lista_msg = "\n\nEditais disponíveis no banco:\n" + "\n".join([f"• **{e['orgao']}** (Score: {e['score_aderencia']}/10)" for e in editais_salvos[:3]])

        return {
            "resposta": f"Olá! Sou o **Zeno Bot**, seu auditor jurídico de licitações integrado via **MCP**.\n\n"
                        f"Você pode:\n"
                        f"1. **Selecionar um edital** no seletor **📄 Edital em Foco** acima para analisar cláusulas, exigências técnicas e prazos com citação de página `[Pág. X]`.\n"
                        f"2. **Pedir no chat:** *\"Busque licitações de ar condicionado em MT\"* para acionar a tool de varredura PNCP em tempo real.\n"
                        f"3. Clicar nos botões rápidos abaixo como **⏱ Prazo de Entrega**, **📋 Atestado Técnico** ou **📊 Índices Contábeis**."
                        + lista_msg,
            "referencias": []
        }

    def _extrair_citacoes(self, texto: str) -> List[str]:
        return list(dict.fromkeys(re.findall(r'\[Pág\.\s*\d+\]', texto)))
