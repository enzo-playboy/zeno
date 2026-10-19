import logging
from typing import Optional, Dict, Any
import httpx
from src.database.models import EditalAuditado
from src.config import settings

logger = logging.getLogger(__name__)

class WhatsAppSender:
    """
    Formatador OaaS e despachante de alertas executivos via WhatsApp (Z-API / Evolution / Webhooks).
    Entrega relatórios cirúrgicos diretamente para o decisor da empresa.
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_token: Optional[str] = None,
        recipient_phone: Optional[str] = None
    ):
        self.api_url = api_url or settings.WHATSAPP_API_URL
        self.api_token = api_token or settings.WHATSAPP_API_TOKEN
        self.recipient_phone = recipient_phone or settings.WHATSAPP_RECIPIENT_PHONE
        self.enabled = settings.WHATSAPP_ENABLED

    def formatar_mensagem_oaas(self, edital: EditalAuditado) -> str:
        """Formata o relatório executivo cirúrgico para leitura rápida no celular."""
        valor_fmt = f"R$ {edital.valor_total_estimado:,.2f}" if edital.valor_total_estimado else "Sob Demanda / Não informado"
        
        # Ícone do score
        if edital.score_aderencia >= 8.0:
            emoji_score = "🟢"
        elif edital.score_aderencia >= 5.0:
            emoji_score = "🟡"
        else:
            emoji_score = "🔴"

        # Itens
        itens_txt = ""
        for it in edital.itens_interesse_cliente[:3]:
            valor_it = f" | Teto: R$ {it.valor_unitario_teto:,.2f}" if it.valor_unitario_teto else ""
            itens_txt += f"  • Item {it.item}: {it.descricao[:70]}... (Qtd: {it.quantidade:.0f}{valor_it})\n"
        if not itens_txt:
            itens_txt = "  • Ver detalhes no Termo de Referência anexo.\n"

        # Exigências Críticas
        exigencias_txt = ""
        for ex in edital.exigencias_habilitacao_criticas[:3]:
            exigencias_txt += f"  ⚖️ {ex}\n"

        # Armadilhas
        riscos_txt = ""
        if edital.armadilhas_e_riscos:
            for r in edital.armadilhas_e_riscos[:2]:
                riscos_txt += f"  ⚠️ {r}\n"
        else:
            riscos_txt = "  ✅ Nenhuma armadilha ou prazo abusivo detectado.\n"

        mensagem = f"""*{emoji_score} RADAR DE LICITAÇÕES OAAS | MT*
━━━━━━━━━━━━━━━━━━━━━
🏢 *Órgão:* {edital.orgao}
📋 *Modalidade:* {edital.modalidade}
📅 *Disputa:* {edital.data_disputa or 'A Definir'}
💰 *Valor Estimado:* {valor_fmt}
🌐 *Portal:* {edital.portal_disputa}

🎯 *Score de Compatibilidade:* *{edital.score_aderencia}/10*
📌 *Recomendação:* {edital.recomendacao_executiva}

📦 *ITENS DE INTERESSE:*
{itens_txt}
📑 *EXIGÊNCIAS DE HABILITAÇÃO:*
{exigencias_txt}
🚨 *ARMADILHAS & RISCOS:*
{riscos_txt}
━━━━━━━━━━━━━━━━━━━━━
📎 *ID PNCP:* `{edital.numero_controle_pncp}`
"""
        return mensagem.strip()

    def enviar_alerta(self, edital: EditalAuditado) -> bool:
        """Envia mensagem formatada para o número cadastrado."""
        mensagem = self.formatar_mensagem_oaas(edital)

        if not self.enabled or not self.api_url:
            logger.info(f"[SIMULAÇÃO WHATSAPP] Alerta gerado para {edital.numero_controle_pncp}:\n{mensagem}")
            return True

        try:
            payload = {
                "phone": self.recipient_phone,
                "message": mensagem
            }
            headers = {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}
            
            with httpx.Client(timeout=15.0) as client:
                res = client.post(self.api_url, json=payload, headers=headers)
                if res.status_code in (200, 201):
                    logger.info(f"Alerta WhatsApp enviado com sucesso para {self.recipient_phone}")
                    return True
                else:
                    logger.error(f"Falha ao enviar alerta WhatsApp ({res.status_code}): {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Erro ao disparar webhook do WhatsApp: {e}")
            return False
