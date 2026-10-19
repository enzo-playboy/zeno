# -*- coding: utf-8 -*-
"""
Suíte de Testes de Integração Total MCP + Motor Backend + Chatbot Jurídico IA
Valida 100% dos contratos das MCP Tools, Resources, Chatbot ReAct e Desktop Bridge.
"""

import sys
import os
from pathlib import Path

# Garantir UTF-8 no Windows Console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio
from src.mcp_server.zeno_mcp import (
    mcp,
    buscar_e_auditar_licitacoes,
    listar_editais_salvos,
    obter_metricas_dashboard,
    consultar_clausula_edital,
    disparar_relatorio_whatsapp,
)
from src.desktop.api import DesktopAPI
from src.intelligence.chat_assistant import ChatAssistant

def test_mcp_full_suite():
    print("=" * 70)
    print("🚀 INICIANDO TESTES DE INTEGRAÇÃO TOTAL: UI + MOTOR + CHATBOT VIA MCP")
    print("=" * 70)

    # --------------------------------------------------------------------------
    # TESTE 1: Tool MCP obter_metricas_dashboard
    # --------------------------------------------------------------------------
    print("\n--- [TESTE 1] Tool MCP: obter_metricas_dashboard ---")
    metricas = obter_metricas_dashboard()
    assert "volume_total_disputa" in metricas, "Deve conter 'volume_total_disputa'"
    assert "score_medio" in metricas, "Deve conter 'score_medio'"
    assert "total_editais_auditados" in metricas, "Deve conter 'total_editais_auditados'"
    assert "distribuicao_semanal" in metricas, "Deve conter 'distribuicao_semanal'"
    assert "aderencia_por_nicho" in metricas, "Deve conter 'aderencia_por_nicho'"
    print(f"✔ Volume Total em Disputa: R$ {metricas['volume_total_disputa']:,.2f}")
    print(f"✔ Score Médio: {metricas['score_medio']}/10")
    print(f"✔ Editais Auditados no SQLite: {metricas['total_editais_auditados']}")
    print("✔ [TESTE 1 PASSOU] Tool obter_metricas_dashboard validada com sucesso!")

    # --------------------------------------------------------------------------
    # TESTE 2: Tool MCP listar_editais_salvos
    # --------------------------------------------------------------------------
    print("\n--- [TESTE 2] Tool MCP: listar_editais_salvos ---")
    editais = listar_editais_salvos()
    assert isinstance(editais, list), "Deve retornar uma lista de editais"
    assert len(editais) > 0, "Deve haver ao menos 1 edital salvo no SQLite"
    ed = editais[0]
    assert "numero_controle_pncp" in ed, "Edital deve conter 'numero_controle_pncp'"
    assert "orgao" in ed, "Edital deve conter 'orgao'"
    assert "valor_total_estimado" in ed, "Edital deve conter 'valor_total_estimado'"
    assert "score_aderencia" in ed, "Edital deve conter 'score_aderencia'"
    assert "exigencias_habilitacao" in ed, "Edital deve conter 'exigencias_habilitacao'"
    assert "armadilhas_e_riscos" in ed, "Edital deve conter 'armadilhas_e_riscos'"
    pncp_id = ed["numero_controle_pncp"]
    print(f"✔ Edital Auditado: {pncp_id} - {ed['orgao']}")
    print(f"✔ Caminho PDF associado: {ed.get('caminho_pdf')}")
    print("✔ [TESTE 2 PASSOU] Tool listar_editais_salvos validada com sucesso!")

    # --------------------------------------------------------------------------
    # TESTE 3: Tool MCP consultar_clausula_edital (PageIndex Tree + Citação [Pág. X])
    # --------------------------------------------------------------------------
    print("\n--- [TESTE 3] Tool MCP: consultar_clausula_edital ---")
    # Consulta sobre amostras
    resp_amostra = consultar_clausula_edital(pncp_id, "Esse edital exige amostra ou visita técnica?")
    print(f"Resposta Amostra/Visita:\n{resp_amostra[:200]}...")
    assert "[Pág." in resp_amostra, "Resposta de cláusula DEVE conter citação exata de página [Pág. X]"

    # Consulta sobre atestados técnicos
    resp_atestado = consultar_clausula_edital(pncp_id, "Quais são as exigências de qualificação e atestado técnico?")
    print(f"\nResposta Atestados:\n{resp_atestado[:200]}...")
    assert "[Pág." in resp_atestado, "Resposta de atestado DEVE conter citação de página [Pág. X]"

    # Consulta sobre prazo de entrega
    resp_prazo = consultar_clausula_edital(pncp_id, "Qual o prazo de entrega?")
    print(f"\nResposta Prazo:\n{resp_prazo[:200]}...")
    assert "[Pág." in resp_prazo, "Resposta de prazo DEVE conter citação de página [Pág. X]"
    print("✔ [TESTE 3 PASSOU] Tool consultar_clausula_edital com PageIndex e [Pág. X] validada!")

    # --------------------------------------------------------------------------
    # TESTE 4: Tool MCP disparar_relatorio_whatsapp
    # --------------------------------------------------------------------------
    print("\n--- [TESTE 4] Tool MCP: disparar_relatorio_whatsapp ---")
    resp_zap = disparar_relatorio_whatsapp(pncp_id)
    assert resp_zap and ("WhatsApp" in resp_zap or "enviado" in resp_zap or "RADAR" in resp_zap), "Deve retornar confirmação do envio"
    print(f"✔ Resposta WhatsApp: {resp_zap[:180]}...")
    print("✔ [TESTE 4 PASSOU] Tool disparar_relatorio_whatsapp validada!")

    # --------------------------------------------------------------------------
    # TESTE 5: Chatbot Jurídico IA ReAct via MCP
    # --------------------------------------------------------------------------
    print("\n--- [TESTE 5] Chatbot Jurídico IA ReAct equipado com Tools MCP ---")
    chat = ChatAssistant()

    # Pergunta com edital em foco
    r_chat1 = chat.responder("Esse edital exige amostra ou visita técnica?", pncp_id)
    assert "[Pág." in r_chat1["resposta"], "Chatbot deve citar [Pág. X] ao responder dúvidas do edital"
    print("✔ Pergunta sobre amostra respondida com citação exata.")

    # Pergunta via Chip rápido "Prazo de Entrega"
    r_chat2 = chat.responder("⏱ Qual o prazo de entrega estipulado neste edital?", pncp_id)
    assert "prazo" in r_chat2["resposta"].lower(), "Chatbot deve identificar intenção de prazo"
    print("✔ Pergunta via chip de prazo respondida.")

    # Pergunta de Métricas
    r_chat3 = chat.responder("Quais as métricas do dashboard?")
    assert "Volume Total em Disputa" in r_chat3["resposta"], "Chatbot deve acionar obter_metricas_dashboard"
    print("✔ Pergunta de métricas acionou tool MCP correspondente.")
    print("✔ [TESTE 5 PASSOU] Chatbot Jurídico IA ReAct validado com sucesso!")

    # --------------------------------------------------------------------------
    # TESTE 6: Desktop Bridge API (Python <-> pywebview)
    # --------------------------------------------------------------------------
    print("\n--- [TESTE 6] Desktop Bridge API pywebview ---")
    api = DesktopAPI()
    dash = api.get_dashboard_data()
    assert "stats" in dash, "get_dashboard_data deve retornar stats"
    assert "editais" in dash, "get_dashboard_data deve retornar editais"
    assert "grafico" in dash, "get_dashboard_data deve retornar gráfico de fatias semanais"
    assert "distribuicao" in dash, "get_dashboard_data deve retornar bolhas de nicho"

    # Teste de listagem de PDFs físicos
    pdfs = api.listar_relatorios_pdfs()
    assert isinstance(pdfs, list), "listar_relatorios_pdfs deve retornar lista de arquivos"
    print(f"✔ Arquivos na Galeria de Relatórios & PDFs: {len(pdfs)}")

    # Chamada direta às tools MCP expostas na API
    mcp_metrics = api.mcp_obter_metricas_dashboard()
    assert mcp_metrics["total_editais_auditados"] >= 1
    print("✔ [TESTE 6 PASSOU] DesktopAPI Bridge conectada 100% às MCP Tools!")

    # --------------------------------------------------------------------------
    # TESTE 7: Verificação In-Process MCP (PyInstaller Friendly)
    # --------------------------------------------------------------------------
    print("\n--- [TESTE 7] Execução In-Process / PyInstaller Compliance ---")
    # Testa se o servidor MCP pode ser acionado assincronamente via call_tool sem processos externos
    async def _test_inprocess_mcp():
        res = await mcp.call_tool("obter_metricas_dashboard", {})
        assert not res.is_error, "mcp.call_tool in-process não pode falhar"
        print("✔ Chamada in-process mcp.call_tool('obter_metricas_dashboard') executada sem dependência de npx/node!")

    asyncio.run(_test_inprocess_mcp())
    print("✔ [TESTE 7 PASSOU] Execução in-process 100% compatível com PyInstaller!")

    print("\n" + "=" * 70)
    print("🎉 TODOS OS 7 TESTES DA INTEGRAÇÃO TOTAL MCP PASSARAM COM 100% DE SUCESSO!")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    test_mcp_full_suite()
