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
        return bool(
            settings.OPENROUTER_API_KEY
            or settings.OPENAI_API_KEY 
            or settings.ANTHROPIC_API_KEY 
            or settings.GEMINI_API_KEY
        )

    def _executar_auditoria_llm(
        self,
        raw_pncp_item: Dict[str, Any],
        texto_documentos: str,
        itens_pncp: Optional[List[Dict[str, Any]]]
    ) -> Optional[EditalAuditado]:
        """Auditoria via API LLM estruturada (OpenRouter, Gemini, OpenAI) ou Proxy Seguro Supabase."""
        import httpx

        def _extrair_json(raw: str) -> Dict[str, Any]:
            txt = raw.strip()
            if txt.startswith("```json"):
                txt = txt[7:]
            elif txt.startswith("```"):
                txt = txt[3:]
            if txt.endswith("```"):
                txt = txt[:-3]
            return json.loads(txt.strip())

        # Limitar contexto mantendo seções críticas
        prompt = f"""
Você é um Auditor Jurídico e Técnico Especialista em Licitações Públicas (Lei 14.133/2021).
Analise com extrema precisão o edital abaixo e retorne ESTRITAMENTE um objeto JSON válido seguindo este formato exato:

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
    "BRECHA/RISCO: Prazo de entrega curto de 5 dias após empenho [Pág. Z]",
    "BRECHA/RISCO: Multa diária excessiva de 2% [Pág. W]"
  ],
  "leis_aplicaveis": [
    "Lei Federal 14.133/2021 (Regime Geral de Contratações) [Pág. 1]",
    "Lei Complementar 123/2006 (Benefício ME/EPP e cota de 25%) [Pág. 3]"
  ],
  "coisas_obrigatorias": [
    "Apresentação de Balanço Patrimonial e DRE registrados [Pág. 8]",
    "Certidão Negativa de Falência e CNDT Válidas [Pág. 9]"
  ],
  "pontos_atencao": [
    "Início da Disputa em 15/04 às 09:00 no Portal ComprasGov [Pág. 2]",
    "Modo de disputa Aberto com intervalo mínimo de lance de R$ 0,05 [Pág. 4]"
  ],
  "score_aderencia": 8.5,
  "recomendacao_executiva": "PARTICIPAR ou NÃO PARTICIPAR - Parecer executivo direto sobre a viabilidade comercial e jurídica."
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
                    "model": self.model_name or settings.LLM_MODEL or "gpt-4o-mini"
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

        # 2. Chamada direta OpenRouter (com fallback automático de modelos)
        if settings.OPENROUTER_API_KEY:
            modelos_tentar = []
            if self.model_name:
                modelos_tentar.append(self.model_name)
            if settings.LLM_MODEL and settings.LLM_MODEL not in modelos_tentar:
                modelos_tentar.append(settings.LLM_MODEL)
            # Candidatos adicionais de alta disponibilidade e gratuitos
            candidatos_padrao = [
                "nvidia/nemotron-3.5-lightning:free",
                "nvidia/nemotron-3-super-120b-a12b:free",
                "google/gemma-4-26b-a4b-it:free",
                "google/gemma-4-31b-it:free"
            ]
            for c in candidatos_padrao:
                if c not in modelos_tentar:
                    modelos_tentar.append(c)

            for model in modelos_tentar:
                try:
                    logger.info(f"Executando auditoria LLM via OpenRouter ({model})...")
                    resp = httpx.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                            "HTTP-Referer": "https://zenolicitacoes.com.br",
                            "X-Title": "Zeno Licitacoes"
                        },
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "response_format": {"type": "json_object"}
                        },
                        timeout=60.0
                    )
                    if resp.status_code == 200:
                        raw_content = resp.json()["choices"][0]["message"]["content"]
                        parsed = _extrair_json(raw_content)
                        return EditalAuditado(**parsed)
                    else:
                        logger.warning(f"OpenRouter ({model}) retornou status {resp.status_code}: {resp.text[:200]}")
                except Exception as ex:
                    logger.warning(f"Falha na chamada OpenRouter ({model}): {ex}")

        # 3. Chamada direta OpenAI (Ambiente de desenvolvimento)
        if settings.OPENAI_API_KEY:
            try:
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
                    raw_content = resp.json()["choices"][0]["message"]["content"]
                    parsed = _extrair_json(raw_content)
                    return EditalAuditado(**parsed)
            except Exception as ex:
                logger.warning(f"Falha na chamada OpenAI: {ex}")

        # 4. Chamada direta Google Gemini
        if settings.GEMINI_API_KEY:
            try:
                model = self.model_name if self.model_name and "gemini" in self.model_name else "gemini-1.5-flash"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.GEMINI_API_KEY}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json"
                    }
                }
                resp = httpx.post(url, json=payload, timeout=60.0)
                if resp.status_code == 200:
                    res_json = resp.json()
                    raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = _extrair_json(raw_text)
                    return EditalAuditado(**parsed)
                else:
                    logger.warning(f"Google Gemini API retornou status {resp.status_code}: {resp.text}")
            except Exception as ex:
                logger.warning(f"Falha na chamada Google Gemini: {ex}")

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
        leis: List[str] = []
        coisas_obrigatorias: List[str] = []
        pontos_atencao: List[str] = []

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

        # 2. Detector de Exigências, Leis, Armadilhas e Pontos de Atenção
        paginas_blocos = re.split(r'---\s*\[Pág\.\s*(\d+)\]\s*---', texto_completo)
        
        texto_por_pagina: Dict[int, str] = {}
        if len(paginas_blocos) > 1:
            for i in range(1, len(paginas_blocos), 2):
                p_num = int(paginas_blocos[i])
                p_txt = paginas_blocos[i+1]
                texto_por_pagina[p_num] = p_txt

        for p_num, p_txt in texto_por_pagina.items():
            txt_lower = p_txt.lower()
            
            # --- LEIS E DECRETOS ---
            if "14.133" in txt_lower or "lei 14.133" in txt_lower or "lei nº 14.133" in txt_lower:
                leis.append(f"Lei Federal nº 14.133/2021 (Nova Lei de Licitações e Contratos Administrativos) [Pág. {p_num}]")
            if "123" in txt_lower and ("me/epp" in txt_lower or "microempresa" in txt_lower or "complementar" in txt_lower):
                leis.append(f"Lei Complementar nº 123/2006 (Tratamento Diferenciado e Cota Reservada ME/EPP) [Pág. {p_num}]")
            if "10.024" in txt_lower or "decreto 10.024" in txt_lower:
                leis.append(f"Decreto Federal nº 10.024/2019 (Regulamento do Pregão Eletrônico) [Pág. {p_num}]")
            if "13.303" in txt_lower:
                leis.append(f"Lei Federal nº 13.303/2016 (Estatuto Jurídico das Empresas Estatais) [Pág. {p_num}]")
            
            # --- HABILITAÇÃO & COISAS OBRIGATÓRIAS ---
            if "atestado" in txt_lower and ("capacidade técnica" in txt_lower or "fornecimento" in txt_lower or "serviço" in txt_lower):
                match = re.search(r'([^.\n]*atestado[^.\n]*capacidade técnica[^.\n]*)', p_txt, re.IGNORECASE)
                resumo_atestado = match.group(0).strip() if match else "Atestado de capacidade técnica para fornecimento compatível"
                exigencias.append(f"{resumo_atestado[:120]} [Pág. {p_num}]")
                coisas_obrigatorias.append(f"Atestado de Capacidade Técnica comprovando execução compatível [Pág. {p_num}]")

            if any(idx in txt_lower for idx in ["liquidez geral", "liquidez corrente", "ilg", "ilc"]):
                exigencias.append(f"Comprovação de Índices de Liquidez (ILG / ILC >= 1,0) via Balanço Patrimonial [Pág. {p_num}]")
                coisas_obrigatorias.append(f"Balanço Patrimonial e Demonstrações Contábeis com índices mínimos [Pág. {p_num}]")

            if "certidão negativa de falência" in txt_lower or "recuperação judicial" in txt_lower:
                exigencias.append(f"Certidão Negativa de Falência e Recuperação Judicial [Pág. {p_num}]")
                coisas_obrigatorias.append(f"Certidão de Falência e Regularidade Fiscal Conjunta [Pág. {p_num}]")

            if any(cons in txt_lower for cons in ["registro no crea", "registro no cra", "anvisa", "conselho regional"]):
                exigencias.append(f"Registro ou autorização em conselho de classe/órgão regulador [Pág. {p_num}]")
                coisas_obrigatorias.append(f"Registro Profissional no Conselho de Classe Competente [Pág. {p_num}]")

            if "garantia de proposta" in txt_lower or "garantia contratual" in txt_lower:
                coisas_obrigatorias.append(f"Prestação de Garantia Contratual exigida no edital [Pág. {p_num}]")

            # --- BRECHAS E RISCOS (ONDE PODEM PREJUDICAR O CLIENTE) ---
            prazo_match = re.search(r'prazo de entrega[^\d\n]{0,25}(\d+)\s*(?:dias|dias corridos|dias úteis)', txt_lower)
            if prazo_match:
                dias = int(prazo_match.group(1))
                if dias <= 15:
                    armadilhas.append(f"BRECHA/RISCO CRÍTICO: Prazo de entrega muito curto ({dias} dias após empenho) [Pág. {p_num}]")

            if "amostra" in txt_lower and any(w in txt_lower for w in ["apresentação", "prazo de", "sob pena", "desclassificação"]):
                armadilhas.append(f"BRECHA/RISCO: Exigência de apresentação de amostra/prova de conceito com risco de desclassificação [Pág. {p_num}]")

            if "vedada a marca" in txt_lower or "marca exclusiva" in txt_lower or "indicação de marca" in txt_lower:
                armadilhas.append(f"BRECHA/RISCO: Cláusula com potencial restrição ou direcionamento de marca [Pág. {p_num}]")

            if "multa de" in txt_lower and any(p in txt_lower for p in ["por dia", "ao dia", "10%", "20%"]):
                armadilhas.append(f"BRECHA/RISCO: Previsão de multas severas e penalidades rígidas de rescisão unilateral [Pág. {p_num}]")

            if "visita técnica" in txt_lower and ("obrigatória" in txt_lower or "indeferimento" in txt_lower):
                armadilhas.append(f"BRECHA/RISCO: Visita técnica presencial obrigatória como condição de habilitação [Pág. {p_num}]")

            # --- PONTOS DE ATENÇÃO OPERACIONAIS ---
            if any(term in txt_lower for term in ["modo de disputa", "intervalo mínimo", "lances"]):
                pontos_atencao.append(f"Atenção às regras de lances e modo de disputa do pregão [Pág. {p_num}]")
            if any(term in txt_lower for term in ["pagamento em até", "prazo de pagamento", "ordem bancária"]):
                m_pg = re.search(r'pagamento\s+em\s+até\s+(\d+)\s+dias', txt_lower)
                d_pg = m_pg.group(1) if m_pg else "30"
                pontos_atencao.append(f"Prazo estimado de pagamento: {d_pg} dias após atesto da Nota Fiscal [Pág. {p_num}]")
            if any(term in txt_lower for term in ["reajuste", "reajustamento", "repactuação"]):
                pontos_atencao.append(f"Cláusula de reajustamento de preços e índice pactuado no contrato [Pág. {p_num}]")

        # Fallbacks enriquecidos
        if not leis:
            leis.append("Lei Federal nº 14.133/2021 (Normas gerais de licitações públicas)")
            leis.append("Lei Complementar nº 123/2006 (Tratamento diferenciado para ME e EPP)")

        if not exigencias:
            exigencias.append("Habilitação jurídica, regularidade fiscal e trabalhista padrão da Lei 14.133/2021 [Pág. 1]")

        if not coisas_obrigatorias:
            coisas_obrigatorias.append("Certidões Negativas Federal, Estadual, Municipal e Trabalhista (CNDT)")
            coisas_obrigatorias.append("Declaração de cumprimento aos requisitos de habilitação e menor aprendiz")

        if not pontos_atencao:
            data_disp_str = data_disputa or "Consulte a data no portal oficial"
            pontos_atencao.append(f"Data e horário limite de disputa: {data_disp_str}")
            pontos_atencao.append(f"Portal de Disputa: {portal_disputa}")

        # Limitar para garantir síntese executiva de alto impacto
        exigencias = list(dict.fromkeys(exigencias))[:6]
        armadilhas = list(dict.fromkeys(armadilhas))[:6]
        leis = list(dict.fromkeys(leis))[:5]
        coisas_obrigatorias = list(dict.fromkeys(coisas_obrigatorias))[:6]
        pontos_atencao = list(dict.fromkeys(pontos_atencao))[:5]

        # 4. Cálculo do Score de Aderência (0.0 a 10.0)
        score = 8.5
        if armadilhas:
            score -= len(armadilhas) * 1.0
        if any("amostra" in a.lower() for a in armadilhas):
            score -= 0.8
        if any("visita técnica" in a.lower() for a in armadilhas):
            score -= 0.7
        if itens_interesse:
            score += min(1.5, len(itens_interesse) * 0.4)
        score = round(max(1.0, min(10.0, score)), 1)

        # 5. Recomendação Executiva
        if score >= 7.5:
            rec = f"PARTICIPAR - Oportunidade com alta compatibilidade ({len(itens_interesse)} itens mapeados) e riscos controlados."
        elif score >= 5.0:
            rec = f"AVALIAR COM RESSALVAS - Atenção aos prazos de entrega ou exigências apontadas antes de formular proposta."
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
            leis_aplicaveis=leis,
            coisas_obrigatorias=coisas_obrigatorias,
            pontos_atencao=pontos_atencao,
            score_aderencia=score,
            recomendacao_executiva=rec,
            raw_pncp_data=raw_pncp_item,
            status_processamento="CONCLUIDO"
        )
