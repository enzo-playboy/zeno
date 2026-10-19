import json
import logging
import re
from typing import Optional, Dict, Any, List

from src.database.repository import Repository
from src.config import settings
from src.licensing.validator import LicenseValidator

logger = logging.getLogger(__name__)

class ChatAssistant:
    """
    Assistente Especialista e Chatbot Jurídico em Licitações Públicas (Lei 14.133/2021).
    Responde dúvidas de clientes sobre editais auditados, prazos, armadilhas,
    exigências técnicas e itens, com citação de página [Pág. X].
    """

    def __init__(self):
        self.repo = Repository()

    def responder(self, pergunta: str, numero_controle_pncp: Optional[str] = None) -> Dict[str, Any]:
        pergunta_limpa = pergunta.strip()
        if not pergunta_limpa:
            return {
                "resposta": "Por favor, digite uma dúvida ou selecione um edital para análise.",
                "referencias": []
            }

        edital = None
        if numero_controle_pncp:
            edital = self.repo.obter_edital(numero_controle_pncp)

        # 1. Tentar via LLM / Cloud Proxy se houver credenciais
        if self._pode_usar_llm():
            try:
                resposta_llm = self._chamar_llm(pergunta_limpa, edital)
                if resposta_llm:
                    return resposta_llm
            except Exception as e:
                logger.warning(f"Falha na resposta via LLM ({e}). Ativando resposta heurística do assistente...")

        # 2. Resposta Heurística / Base de Conhecimento Estruturada
        return self._resposta_heuristica(pergunta_limpa, edital)

    def _pode_usar_llm(self) -> bool:
        if settings.USE_CLOUD_PROXY and settings.AI_PROXY_URL:
            return True
        return bool(settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY or settings.GEMINI_API_KEY)

    def _chamar_llm(self, pergunta: str, edital: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        import httpx

        contexto_edital = ""
        if edital:
            contexto_edital = f"""
DADOS DO EDITAL SELECIONADO:
- Número PNCP: {edital.get('numero_controle_pncp')}
- Órgão: {edital.get('orgao')}
- Modalidade: {edital.get('modalidade')}
- Data da Disputa: {edital.get('data_disputa')}
- Objeto: {edital.get('objeto_resumo')}
- Valor Total Estimado: R$ {edital.get('valor_total_estimado', 0):,.2f}
- Score de Aderência: {edital.get('score_aderencia')}/10
- Recomendação Executiva: {edital.get('recomendacao_executiva')}
- Itens e Lotes: {json.dumps(edital.get('itens', []), ensure_ascii=False)}
- Exigências Críticas de Habilitação: {json.dumps(edital.get('exigencias', []), ensure_ascii=False)}
- Armadilhas e Riscos Identificados: {json.dumps(edital.get('armadilhas', []), ensure_ascii=False)}
"""

        prompt = f"""
Você é o Zeno Bot, Auditor Jurídico e Especialista em Licitações Públicas (Lei 14.133/2021) do Zeno Radar.
Responda à dúvida do cliente de forma executiva, objetiva e profissional.
Sempre que citar exigências, atestados, prazos ou regras, inclua a citação de página no formato [Pág. X], se disponível no contexto.

{contexto_edital}

PERGUNTA DO CLIENTE:
"{pergunta}"

Responda diretamente com formatação Markdown clara (em tópicos se aplicável).
"""

        # Modo Proxy Nuvem
        if settings.USE_CLOUD_PROXY and settings.AI_PROXY_URL:
            from src.licensing.hwid import get_hwid
            headers = {
                "Content-Type": "application/json",
                "x-license-key": settings.LICENSE_KEY or "ZENO-DEV",
                "x-hwid": get_hwid()
            }
            resp = httpx.post(
                settings.AI_PROXY_URL,
                headers=headers,
                json={"prompt": prompt, "model": settings.LLM_MODEL},
                timeout=45.0
            )
            if resp.status_code == 200:
                texto = resp.json()
                if isinstance(texto, dict) and "resposta" in texto:
                    return texto
                return {"resposta": str(texto), "referencias": self._extrair_citacoes(str(texto))}

        # Modo OpenAI Local
        if settings.OPENAI_API_KEY:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                json={
                    "model": settings.LLM_MODEL or "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "Você é o assistente de auditoria de licitações Zeno Radar."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2
                },
                timeout=45.0
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                return {"resposta": content, "referencias": self._extrair_citacoes(content)}

        return None

    def _resposta_heuristica(self, pergunta: str, edital: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Motor heurístico que responde instantaneamente com base nos dados auditados."""
        p_lower = pergunta.lower()

        if not edital:
            # Dúvidas gerais de licitação
            if "lei 14.133" in p_lower or "nova lei" in p_lower:
                return {
                    "resposta": "A **Lei 14.133/2021** (Nova Lei de Licitações e Contratos Administrativos) unificou o regime licitatório brasileiro, tornando o formato eletrônico obrigatório, extinguindo modalidades antigas (como Convite e Tomada de Preços) e fortalecendo a governança com o PNCP.",
                    "referencias": ["Lei 14.133/2021"]
                }
            if "impugnação" in p_lower or "prazo de impugnar" in p_lower:
                return {
                    "resposta": "Conforme o **Art. 164 da Lei 14.133/2021**, qualquer pessoa pode impugnar o edital de licitação por irregularidade na aplicação da lei até **3 dias úteis** antes da data de abertura do certame.",
                    "referencias": ["Art. 164 da Lei 14.133/2021"]
                }
            return {
                "resposta": "Olá! Sou o **Zeno Bot**, seu assistente de licitações. Selecione um edital na lista acima ou pergunte sobre **prazos de entrega**, **atestados técnicos**, **armadilhas contratuais**, **itens de interesse** ou regras da **Lei 14.133/2021**.",
                "referencias": []
            }

        # Análise específica do edital selecionado
        orgao = edital.get("orgao", "Órgão")
        armadilhas = edital.get("armadilhas", [])
        exigencias = edital.get("exigencias", [])
        itens = edital.get("itens", [])
        score = edital.get("score_aderencia", 0.0)
        rec = edital.get("recomendacao_executiva", "")

        # Dúvidas de Prazo de Entrega
        if any(w in p_lower for w in ["prazo", "entrega", "dias", "quando"]):
            prazos_detectados = [a for a in armadilhas if "prazo" in a.lower()]
            if prazos_detectados:
                return {
                    "resposta": f"⏱ **Prazo de Entrega Identificado para {orgao}:**\n\n" + "\n".join([f"• {p}" for p in prazos_detectados]) + f"\n\n**Recomendação:** Certifique-se com seus fornecedores sobre a disponibilidade imediata para cumprir este prazo sem risco de penalidades contratuais.",
                    "referencias": self._extrair_citacoes(" ".join(prazos_detectados))
                }
            return {
                "resposta": f"O prazo de entrega informado nos autos para **{orgao}** segue os termos do Termo de Referência padrão (geralmente entre 15 a 30 dias úteis após o empenho).",
                "referencias": ["[Pág. 1]"]
            }

        # Dúvidas de Habilitação / Atestados / Certidões
        if any(w in p_lower for w in ["atestado", "capacidade técnica", "habilitação", "certidão", "balanço", "índice"]):
            if exigencias:
                return {
                    "resposta": f"📋 **Exigências de Habilitação Críticas ({orgao}):**\n\n" + "\n".join([f"• {e}" for e in exigencias]) + "\n\n**Atenção:** Verifique a validade de suas certidões e os atestados com antecedência mínima de 48h da disputa.",
                    "referencias": self._extrair_citacoes(" ".join(exigencias))
                }

        # Dúvidas de Riscos / Armadilhas / Red Flags
        if any(w in p_lower for w in ["risco", "armadilha", "perigo", "cuidado", "red flag", "penalidade"]):
            if armadilhas:
                return {
                    "resposta": f"🚨 **Armadilhas e Riscos Operacionais Identificados:**\n\n" + "\n".join([f"• {a}" for a in armadilhas]) + f"\n\n**Score de Risco:** {score}/10 — *{rec}*",
                    "referencias": self._extrair_citacoes(" ".join(armadilhas))
                }
            return {
                "resposta": f"Nenhum risco de alto impacto imediato foi identificado nas cláusulas críticas de **{orgao}**. O score de aderência é de **{score}/10**.",
                "referencias": []
            }

        # Dúvidas de Itens / Valores / Lotes
        if any(w in p_lower for w in ["item", "itens", "lote", "valor", "preço", "quanto vale", "quantidade"]):
            if itens:
                linhas_itens = []
                for it in itens[:6]:
                    linhas_itens.append(f"• **Item {it.get('item')}:** {it.get('descricao')} | Qtd: **{it.get('quantidade')}** | Teto: **R$ {it.get('valor_unitario_teto', 0):,.2f}**")
                return {
                    "resposta": f"📦 **Itens de Interesse do Cliente ({orgao}):**\n\n" + "\n".join(linhas_itens) + f"\n\n**Valor Total Estimado do Edital:** R$ {edital.get('valor_total_estimado', 0):,.2f}",
                    "referencias": ["[Pág. 1]"]
                }

        # Resumo Executivo Geral
        return {
            "resposta": f"📊 **Resumo de Auditoria — {orgao}** (PNCP: {edital.get('numero_controle_pncp')}):\n\n"
                        f"• **Objeto:** {edital.get('objeto_resumo')}\n"
                        f"• **Valor Estimado:** R$ {edital.get('valor_total_estimado', 0):,.2f}\n"
                        f"• **Data da Disputa:** {edital.get('data_disputa')}\n"
                        f"• **Score de Aderência:** **{score}/10**\n"
                        f"• **Recomendação Executiva:** {rec}\n\n"
                        f"Você pode me perguntar especificamente sobre os *atestados técnicos*, o *prazo de entrega* ou os *itens deste pregão*.",
            "referencias": self._extrair_citacoes(f"{exigencias} {armadilhas}")
        }

    def _extrair_citacoes(self, texto: str) -> List[str]:
        return list(dict.fromkeys(re.findall(r'\[Pág\.\s*\d+\]', texto)))
