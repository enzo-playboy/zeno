# -*- coding: utf-8 -*-
"""
Teste de Ponta a Ponta em Produção Real: Servidor MCP Zeno Radar + PNCP API + PageIndex RAG + GUI Desktop
=========================================================================================================

Roteiro de Validação:
1) Executa busca e auditoria real de licitações via FastMCP Tool para UF='MT' (limite de 2 editais reais da API do PNCP).
2) Verifica armazenamento persistente dos PDFs e do banco SQLite em %LOCALAPPDATA%\\ZenoRadar (fora de pastas temporárias).
3) Executa consulta jurídica ReAct (consultar_clausula_edital) para atestados técnicos e prazos de entrega, validando citações [Pág. X].
4) Abre a interface gráfica Desktop nativa (pywebview) conectada aos dados reais para teste visual e interativo.
"""

import os
import sys
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime

# Ajuste de codificação para console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Adiciona a raiz do projeto ao sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Importação das MCP Tools e utilitários de infraestrutura
try:
    from src.mcp_server.zeno_mcp import (
        buscar_e_auditar_licitacoes,
        listar_editais_salvos,
        obter_metricas_dashboard,
        consultar_clausula_edital,
        disparar_relatorio_whatsapp,
    )
    from src.utils.paths import get_app_dir, get_db_path, get_downloads_dir
    from src.desktop.api import DesktopAPI
except ImportError as err:
    print("=" * 80)
    print(f"❌ Dependência ausente no ambiente Python ativo: {err}")
    print("👉 Para instalar as dependências necessárias, execute no terminal:")
    print("      pip install -r requirements.txt")
    print("   Ou para instalar os pacotes essenciais:")
    print("      pip install pydantic pydantic-settings httpx pymupdf pywebview mcp")
    print("=" * 80)
    sys.exit(1)

def run_production_e2e_test(open_gui: bool = True):
    print("=" * 80)
    print("🏛️  ZENO RADAR — TESTE DE PONTA A PONTA EM PRODUÇÃO REAL (MCP + PNCP + DESKTOP)")
    print("=" * 80)
    print(f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"Ambiente: Python {sys.version.split()[0]} no {sys.platform.upper()}")
    print("-" * 80)

    # ==========================================================================
    # ETAPA 1: Chamada da Tool MCP buscar_e_auditar_licitacoes (UF='MT', limite=2)
    # ==========================================================================
    print("\n" + "▶" * 4 + " [ETAPA 1/4] Chamar Tool MCP buscar_e_auditar_licitacoes (UF='MT', limite=2)...")
    print("📡 Conectando à API Oficial do PNCP (https://pncp.gov.br/api/consulta/v1/contratacoes/proposta)...")
    
    res_busca = buscar_e_auditar_licitacoes(uf="MT", limite=2)
    
    print(f"Status MCP: {res_busca.get('status')}")
    print(f"Mensagem: {res_busca.get('mensagem')}")
    print(f"Total de candidatos encontrados no PNCP (MT): {res_busca.get('total_candidatos_pncp', 0)}")
    print(f"Total novos ou auditados: {res_busca.get('total_novos_auditados', 0)}")
    
    editais = res_busca.get("editais", [])
    if not editais:
        # Se todos já haviam sido processados em rodadas anteriores, lista do SQLite
        print("⚠️  Todos os editais já haviam sido processados. Carregando registros do banco local...")
        editais = listar_editais_salvos()
    
    assert len(editais) >= 1, "ERRO CRÍTICO: Nenhum edital retornado ou salvo no sistema!"
    
    print(f"\n📋 Editais Processados com Sucesso ({len(editais)} auditados):")
    for idx, ed in enumerate(editais[:2], 1):
        print(f"   [{idx}] PNCP ID: {ed.get('numero_controle_pncp')}")
        print(f"       Órgão: {ed.get('orgao')}")
        print(f"       Modalidade: {ed.get('modalidade')}")
        print(f"       Valor Estimado: R$ {float(ed.get('valor_total_estimado') or 0):,.2f}")
        print(f"       Score de Aderência: {ed.get('score_aderencia')}/10 — {ed.get('recomendacao_executiva')}")
        print(f"       PDF Físico: {ed.get('caminho_pdf') or 'Anexo do edital'}")

    # ==========================================================================
    # ETAPA 2: Validação de %LOCALAPPDATA%\ZenoRadar (Fora de Pastas Temporárias)
    # ==========================================================================
    print("\n" + "▶" * 4 + " [ETAPA 2/4] Verificando Diretórios Persistentes (%LOCALAPPDATA%\\ZenoRadar)...")
    
    app_dir = get_app_dir()
    db_path = get_db_path()
    downloads_dir = get_downloads_dir()
    temp_dir = Path(tempfile.gettempdir()).resolve()

    print(f"📍 Diretório Base: {app_dir}")
    print(f"📍 Banco SQLite: {db_path}")
    print(f"📍 Pasta de PDFs / Downloads: {downloads_dir}")
    print(f"📍 Diretório Temporário do SO (%TEMP%): {temp_dir}")

    # Garantir que NÃO estamos em pasta temporária nem _MEIxxxx
    assert not str(app_dir).startswith(str(temp_dir)), f"FALHA: app_dir está dentro de pasta temporária! ({app_dir})"
    assert "_mei" not in str(app_dir).lower(), f"FALHA: app_dir está dentro de pasta _MEI volátil! ({app_dir})"
    assert app_dir.exists(), f"FALHA: app_dir não existe! ({app_dir})"

    # Verificar banco SQLite
    assert db_path.exists(), f"FALHA: Arquivo de banco SQLite zeno_radar.db não existe em {db_path}!"
    assert db_path.stat().st_size > 0, "FALHA: zeno_radar.db está vazio (0 bytes)!"
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM editais")
    count_editais = cursor.fetchone()[0]
    cursor.execute("SELECT numero_controle_pncp, orgao, valor_total_estimado, score_aderencia FROM editais LIMIT 2")
    linhas_db = cursor.fetchall()
    conn.close()

    print(f"✔ Banco SQLite validado: {count_editais} editais registrados.")
    for linha in linhas_db:
        print(f"   • Registro SQLite: {linha[0]} | {linha[1][:35]}... | R$ {float(linha[2] or 0):,.2f} | Score {linha[3]}")
    assert count_editais >= 1, "FALHA: Banco SQLite não possui registros salvos!"

    # Verificar PDFs baixados
    arquivos_downloads = list(downloads_dir.glob("*"))
    pdfs_baixados = list(downloads_dir.glob("*.pdf"))
    print(f"✔ Diretório de Downloads validado: {len(arquivos_downloads)} arquivo(s) encontrado(s), sendo {len(pdfs_baixados)} PDF(s).")
    for f in arquivos_downloads[:4]:
        tam_kb = f.stat().st_size / 1024
        print(f"   • {f.name} ({tam_kb:.1f} KB) - Caminho persistente: {f.parent}")
        assert tam_kb > 0, f"Arquivo {f.name} está vazio!"

    print("✔ [ETAPA 2 CONCLUÍDA COM SUCESSO] Armazenamento 100% persistente e fora do %TEMP%!")

    # ==========================================================================
    # ETAPA 3: Chamada da Tool MCP consultar_clausula_edital (Atestados + Prazos)
    # ==========================================================================
    print("\n" + "▶" * 4 + " [ETAPA 3/4] Chamar Tool MCP consultar_clausula_edital...")
    target_pncp = editais[0].get("numero_controle_pncp")
    pergunta = "Quais são os atestados técnicos e prazos de entrega exigidos?"
    
    print(f"🎯 Edital Alvo: {target_pncp} ({editais[0].get('orgao')})")
    print(f"❓ Pergunta Jurídica ReAct: \"{pergunta}\"")
    print("-" * 50)
    
    parecer = consultar_clausula_edital(pncp_id=target_pncp, pergunta_juridica=pergunta)
    
    print(parecer)
    print("-" * 50)
    
    # Validações semânticas da resposta jurídica
    assert len(parecer) > 100, "Parecer jurídico muito curto ou vazio!"
    assert "[Pág." in parecer, "FALHA: Parecer deve conter citação exata de página [Pág. X] via PageIndex!"
    
    # Valida se ambos os temas solicitados foram abordados
    tem_prazo = any(w in parecer.lower() for w in ["prazo", "entrega", "dias"])
    tem_atestado = any(w in parecer.lower() for w in ["atestado", "capacidade", "qualificação técnica", "habilitação"])
    assert tem_prazo, "FALHA: Resposta deve contemplar os prazos de entrega exigidos!"
    assert tem_atestado, "FALHA: Resposta deve contemplar os atestados técnicos / qualificação técnica!"
    
    print("✔ [ETAPA 3 CONCLUÍDA COM SUCESSO] Análise jurídica ReAct multitemática validada com citação [Pág. X]!")

    # ==========================================================================
    # ETAPA 4: Abertura da Interface Gráfica Desktop (pywebview)
    # ==========================================================================
    print("\n" + "▶" * 4 + " [ETAPA 4/4] Inicializando Interface Gráfica Desktop (pywebview)...")
    
    try:
        import webview
        HAS_WEBVIEW = True
    except ImportError:
        HAS_WEBVIEW = False
        print("⚠️  pywebview não encontrado no ambiente Python. A GUI requer 'pip install pywebview'.")

    ui_html_path = BASE_DIR / "src" / "desktop" / "ui" / "index.html"
    assert ui_html_path.exists(), f"FALHA: Arquivo index.html da UI não encontrado em {ui_html_path}!"
    print(f"📍 Frontend HTML5 SPA: {ui_html_path}")

    # Criação da Desktop API bridge conectada aos dados reais
    api = DesktopAPI()
    dados_dashboard = api.get_dashboard_data()
    print(f"✔ DesktopAPI Bridge inicializada com sucesso!")
    print(f"   • Total de Editais no Dashboard: {dados_dashboard['stats']['total_editais']}")
    print(f"   • Volume Total em Disputa: R$ {dados_dashboard['stats']['volume_total']:,.2f}")
    print(f"   • Score Médio da Carteira: {dados_dashboard['stats']['media_score']}/10")
    print(f"   • Cliente: {dados_dashboard['cliente']['nome']} (UF: {dados_dashboard['cliente']['uf']})")

    if open_gui and HAS_WEBVIEW:
        print("\n🚀 Abrindo janela Desktop nativa do Zeno Radar na tela do usuário...")
        print("   (Para encerrar o teste na tela, feche a janela do aplicativo).")
        
        window = webview.create_window(
            title="Zeno Radar — Painel de Licitações (MT) [Produção Real MCP]",
            url=str(ui_html_path),
            js_api=api,
            width=1320,
            height=860,
            min_size=(1040, 700),
            background_color="#ebeef2"
        )
        
        webview.start(debug=False)
        print("✔ Janela gráfica fechada pelo usuário. Teste concluído!")
    else:
        print("ℹ️  Modo sem interface ou pywebview não carregado no processo.")

    print("\n" + "=" * 80)
    print("🎉 TODOS OS TESTES DE PONTA A PONTA EM PRODUÇÃO REAL FORAM CONCLUÍDOS COM SUCESSO!")
    print("=" * 80)

if __name__ == "__main__":
    # Permite passar --no-gui via linha de comando se desejado
    abrir_interface = "--no-gui" not in sys.argv
    run_production_e2e_test(open_gui=abrir_interface)
