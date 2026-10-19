import os
import json
import re
import logging
from typing import Dict, Any, List, Optional
from src.database.models import EditalAuditado, ItemLote
from src.intelligence.pageindex_tree import PageIndexTree, DocumentNode
from src.config import settings

logger = logging.getLogger(__name__)

# Tenta carregar AgentScope
HAS_AGENTSCOPE = False
try:
    import agentscope
    from agentscope.agents import ReActAgent
    HAS_AGENTSCOPE = True
except Exception:
    HAS_AGENTSCOPE = False

class AuditorReActAgent:
    """
    Agente de Auditoria ReAct integrado ao PageIndex e AgentScope.
    Audita cláusulas de risco, exigências de qualificação técnica/financeira,
    itens e preços teto com citação exata de página [Pág. X].
    """

    def __init__(self, model_name: Optional[str] = None, license_key: Optional[str] = None):
        self.model_name = model_name or settings.LLM_MODEL
        self.provider = settings.LLM_PROVIDER
        self.license_key = license_key or settings.LICENSE_KEY
        self.tree_engine = PageIndexTree()

    def auditar_edital(
        self,
        raw_pncp_item: Dict[str, Any],
        arvore_docs: Optional[DocumentNode] = None,
        itens_pncp: Optional[List[Dict[str, Any]]] = None,
        texto_completo: Optional[str] = None
    ) -> EditalAuditado:
        """
        Executa a auditoria completa do edital gerando o Contrato de Saída JSON.
        """
        num_pncp = raw_pncp_item.get("numeroControlePNCP") or raw_pncp_item.get("numero_controle_pncp", "")
        orgao = (raw_pncp_item.get("orgaoEntidade", {}).get("razaoSocial") 
                 or raw_pncp_item.get("unidadeOrgao", {}).get("nomeUnidade")
                 or "Órgão Público")
        modalidade = raw_pncp_item.get("modalidadeNome") or "Pregão Eletrônico"
        data_disputa = raw_pncp_item.get("dataEncerramentoProposta") or raw_pncp_item.get("dataAberturaProposta")
        portal_disputa = raw_pncp_item.get("linkSistemaOrigem") or "PNCP / Compras Públicas"
        objeto = raw_pncp_item.get("objetoCompra") or raw_pncp_item.get("objeto_resumo", "")
        valor_total = raw_pncp_item.get("valorTotalEstimado")

        # 1. Tentar auditoria via LLM / Proxy se houver credenciais
        if self._tem_credenciais_llm() and (texto_completo or arvore_docs):
            try:
                logger.info(f"Executando auditoria LLM ReAct para edital {num_pncp}...")
                resultado_llm = self._executar_auditoria_llm(raw_pncp_item, texto_completo or "", itens_pncp)
                if resultado_llm:
                    return resultado_llm
            except Exception as e:
                logger.warning(f"Auditoria LLM ReAct falhou ({e}). Ativando auditoria heurística estruturada...")

        # 2. Auditoria Heurística Baseada na Árvore PageIndex + Dados PNCP
        return self._auditar_heuristico(
            raw_pncp_item=raw_pncp_item,
            num_pncp=num_pncp,
            orgao=orgao,
            modalidade=modalidade,
            data_disputa=data_disputa,
            portal_disputa=portal_disputa,
            objeto=objeto,
            valor_total=valor_total,
            arvore_docs=arvore_docs,
            itens_pncp=itens_pncp,
            texto_completo=texto_completo or ""
        )

    def _tem_credenciais_llm(self) -> bool:
        if settings.USE_CLOUD_PROXY and settings.AI_PROXY_URL:
            return True
        return bool(settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY or settings.GEMINI_API_KEY)

    def _executar_auditoria_llm(
        self,
        raw_pncp_item: Dict[str, Any],
        texto_documentos: str,
        itens_pncp: Optional[List[Dict[str, Any]]]
    ) -> Optional[EditalAuditado]:
        """Auditoria via API LLM estruturada ou Proxy Seguro Supabase."""
        import httpx

        # Limitar contexto mantendo seções críticas
        prompt = f"""
Você é um Auditor Jurídico e Técnico Especialista em Licitações Públicas (Lei 14.133/2021).
Analise o edital abaixo e retorne ESTRITAMENTE um objeto JSON válido seguindo este formato exato:

{{
  "numero_controle_pncp": "{raw_pncp_item.get('numeroControlePNCP')}",
  "orgao": "{raw_pncp_item.get('orgaoEntidade', {}).get('razaoSocial', '')}",
  "modalidade": "{raw_pncp_item.get('modalidadeNome', 'Pregão Eletrônico')}",
  "data_disputa": "{raw_pncp_item.get('dataEncerramentoProposta', '')}",
  "portal_disputa": "{raw_pncp_item.get('linkSistemaOrigem', '')}",
  "objeto_resumo": "{raw_pncp_item.get('objetoCompra', '')}",
  "valor_total_estimado": {raw_pncp_item.get('valorTotalEstimado') or 0.0},
  "itens_interesse_cliente": [
    {{
      "item": 1,
      "descricao": "Descrição do item",
      "quantidade": 10,
      "valor_unitario_teto": 100.0,
      "pagina_referencia": 1
    }}
  ],
  "exigencias_habilitacao_criticas": [
    "Atestado de capacidade técnica... [Pág. X]",
    "Índice de liquidez corrente... [Pág. Y]"
  ],
  "armadilhas_e_riscos": [
    "Prazo de entrega curto... [Pág. Z]"
  ],
  "score_aderencia": 8.5,
  "recomendacao_executiva": "PARTICIPAR ou NÃO PARTICIPAR - Justificativa executiva cirúrgica."
}}

TEXTO DO EDITAL / ANEXOS COM CITAÇÕES:
{texto_documentos[:15000]}
"""
        # 1. Modo Proxy em Nuvem (Produção Cliente - Segredo Protegido)
        if settings.USE_CLOUD_PROXY and settings.AI_PROXY_URL:
            try:
                from src.licensing.hwid import get_hwid
                active_key = self.license_key or settings.LICENSE_KEY
                headers = {
                    "Content-Type": "application/json",
                    "x-license-key": active_key,
                    "x-hwid": get_hwid()
                }
                payload = {
                    "prompt": prompt,
                    "model": self.model_name or "gpt-4o-mini"
                }
                logger.info("Enviando requisição de auditoria ao Proxy Seguro de IA...")
                resp = httpx.post(
                    settings.AI_PROXY_URL,
                    headers=headers,
                    json=payload,
                    timeout=60.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return EditalAuditado(**data)
                logger.warning(f"Proxy de IA retornou status {resp.status_code}: {resp.text}")
            except Exception as ex:
                logger.warning(f"Falha ao chamar Proxy Seguro de IA: {ex}")

        # 2. Chamada direta local (Ambiente de desenvolvimento)
        if settings.OPENAI_API_KEY:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                json={
                    "model": self.model_name or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"}
                },
                timeout=60.0
            )
            if resp.status_code == 200:
                data = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(data)
                return EditalAuditado(**parsed)
        return None

    def _auditar_heuristico(
        self,
        raw_pncp_item: Dict[str, Any],
        num_pncp: str,
        orgao: str,
        modalidade: str,
        data_disputa: Optional[str],
        portal_disputa: str,
        objeto: str,
        valor_total: Optional[float],
        arvore_docs: Optional[DocumentNode],
        itens_pncp: Optional[List[Dict[str, Any]]],
        texto_completo: str
    ) -> EditalAuditado:
        """
        Auditor Heurístico com detecção de padrões de risco (red flags),
        habilitação jurídica/técnica e lotes de interesse.
        """
        itens_interesse: List[ItemLote] = []
        exigencias: List[str] = []
        armadilhas: List[str] = []

        # 1. Processar itens de interesse
        if itens_pncp:
            for it in itens_pncp:
                desc = it.get("descricao") or it.get("objetoItem") or ""
                qtd = float(it.get("quantidade") or 1.0)
                v_unit = float(it.get("valorUnitarioEstimado") or it.get("valorTotal") or 0.0)
                num_item = int(it.get("numeroItem") or len(itens_interesse) + 1)
                
                # Checar se bate com palavras-chave de interesse
                if any(k in desc.lower() for k in settings.target_keywords_list) or not settings.target_keywords_list:
                    itens_interesse.append(ItemLote(
                        item=num_item,
                        descricao=desc,
                        quantidade=qtd,
                        valor_unitario_teto=v_unit,
                        pagina_referencia=1
                    ))
        
        # Se não vieram itens da API PNCP, tentar extrair das tabelas ou texto
        if not itens_interesse:
            itens_interesse.append(ItemLote(
                item=1,
                descricao=objeto[:150],
                quantidade=1.0,
                valor_unitario_teto=valor_total,
                pagina_referencia=1
            ))

        # 2. Detector de Exigências de Habilitação (Atestados, Índices, Conselhos)
        # Buscar seções na árvore PageIndex ou no texto com citações [Pág. X]
        paginas_blocos = re.split(r'---\s*\[Pág\.\s*(\d+)\]\s*---', texto_completo)
        
        # Mapeamento de texto por página
        texto_por_pagina: Dict[int, str] = {}
        if len(paginas_blocos) > 1:
            for i in range(1, len(paginas_blocos), 2):
                p_num = int(paginas_blocos[i])
                p_txt = paginas_blocos[i+1]
                texto_por_pagina[p_num] = p_txt

        for p_num, p_txt in texto_por_pagina.items():
            txt_lower = p_txt.lower()
            
            # Atestados de Capacidade Técnica
            if "atestado" in txt_lower and ("capacidade técnica" in txt_lower or "fornecimento" in txt_lower):
                # Captura trecho relevante
                match = re.search(r'([^.\n]*atestado[^.\n]*capacidade técnica[^.\n]*)', p_txt, re.IGNORECASE)
                resumo_atestado = match.group(0).strip() if match else "Atestado de capacidade técnica para fornecimento compatível"
                exigencias.append(f"{resumo_atestado[:120]} [Pág. {p_num}]")

            # Índices de Balanço (ILG, ILC, ISG)
            if any(idx in txt_lower for idx in ["liquidez geral", "liquidez corrente", "ilg", "ilc"]):
                exigencias.append(f"Comprovação de Índices de Liquidez (ILG / ILC >= 1,0) via Balanço Patrimonial [Pág. {p_num}]")

            # Certidões ou Conselhos (CREA, CRA, ANVISA, Falência)
            if "certidão negativa de falência" in txt_lower:
                exigencias.append(f"Certidão Negativa de Falência e Recuperação Judicial [Pág. {p_num}]")
            if any(cons in txt_lower for cons in ["registro no crea", "registro no cra", "anvisa"]):
                exigencias.append(f"Registro ou autorização em conselho de classe/órgão regulador [Pág. {p_num}]")

            # 3. Detector de Armadilhas ("Red Flags")
            # Prazo de entrega muito curto (ex: 5 a 15 dias)
            prazo_match = re.search(r'prazo de entrega[^\d\n]{0,25}(\d+)\s*(?:dias|dias corridos|dias úteis)', txt_lower)
            if prazo_match:
                dias = int(prazo_match.group(1))
                if dias <= 15:
                    armadilhas.append(f"Prazo de entrega curto: {dias} dias após Nota de Empenho [Pág. {p_num}]")

            # Exigência de amostra
            if "amostra" in txt_lower and any(w in txt_lower for w in ["apresentação", "prazo de", "sob pena"]):
                armadilhas.append(f"Exigência de apresentação de amostra/prova de conceito em prazo determinado [Pág. {p_num}]")

            # Marca exclusiva / direcionamento
            if "vedada a marca" in txt_lower or "marca exclusiva" in txt_lower:
                armadilhas.append(f"Cláusula de restrição de marca identificada [Pág. {p_num}]")

        # Exigências padrão mínimas caso o PDF não tenha sido baixado ainda
        if not exigencias:
            exigencias.append("Habilitação jurídica, regularidade fiscal e trabalhista padrão da Lei 14.133/2021 [Pág. 1]")

        # Limitar para os mais críticos
        exigencias = list(dict.fromkeys(exigencias))[:5]
        armadilhas = list(dict.fromkeys(armadilhas))[:4]

        # 4. Cálculo do Score de Aderência (0.0 a 10.0)
        score = 8.5
        if armadilhas:
            score -= len(armadilhas) * 1.2
        if any("amostra" in a.lower() for a in armadilhas):
            score -= 1.0
        if itens_interesse:
            score += min(1.5, len(itens_interesse) * 0.5)
        score = round(max(1.0, min(10.0, score)), 1)

        # 5. Recomendação Executiva
        if score >= 7.5:
            rec = f"PARTICIPAR - Oportunidade com alta compatibilidade ({len(itens_interesse)} itens de interesse) e riscos controlados."
        elif score >= 5.0:
            rec = f"AVALIAR COM RESSALVAS - Atenção redobrada aos prazos de entrega ou exigências de amostra apontadas."
        else:
            rec = f"NÃO RECOMENDADO - Alto risco operacional devido a cláusulas restritivas ou prazos inexequíveis."

        return EditalAuditado(
            numero_controle_pncp=num_pncp,
            orgao=orgao,
            modalidade=modalidade,
            data_disputa=data_disputa,
            portal_disputa=portal_disputa,
            objeto_resumo=objeto,
            valor_total_estimado=valor_total,
            itens_interesse_cliente=itens_interesse,
            exigencias_habilitacao_criticas=exigencias,
            armadilhas_e_riscos=armadilhas,
            score_aderencia=score,
            recomendacao_executiva=rec,
            raw_pncp_data=raw_pncp_item,
            status_processamento="CONCLUIDO"
        )
