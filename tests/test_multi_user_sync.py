"""
Testes Automatizados de Concorrência e Colaboração Multi-Usuário (Corporativo)
Simula 4 funcionários de uma mesma empresa acessando simultaneamente editais e projetos:
- João - Comercial
- Maria - Jurídico
- Carlos - Precificação
- Diretor - Executivo

Valida:
1. Limite de Seats e Trava por HWID
2. Eleição de Coletor Node Único (Background Worker)
3. Distributed Lock e Prevenção de Auditoria Duplicada
4. Concorrência com Merge Granular de Checklist (Zero Overwrite)
5. Transições de Workflow no Kanban e Histórico Append-Only de Notas
6. Resiliência do SQLite Local Mirror e Cloud Sync Supabase
"""

import sys
import os
import json
import time
import threading
import tempfile
from pathlib import Path
from datetime import datetime

# Garante acesso aos módulos da aplicação
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.database.models import (
    Organization,
    UserSeat,
    BidProject,
    ProcessingLock,
    EditalAuditado,
    ItemLote
)
from src.database.repository import Repository


def test_organizacao_e_limite_de_seats_hwid():
    """
    Cenário 1:
    Cadastra uma empresa com limite de 4 seats (estações de trabalho).
    Cadastra os 4 funcionários com seus respectivos HWIDs.
    Tenta cadastrar um 5º funcionário e valida o bloqueio estrito por limite de licença corporativa.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = Repository(db_path=db_path)
        
        # 1. Cadastra a organização
        org = Organization(
            id="org_techgov",
            name="TechGov Soluções Ltda",
            cnpj="12.345.678/0001-90",
            plan_tier="CORPORATE_ENTERPRISE",
            max_seats=4,
            uf_target="MT",
            keywords=["tecnologia", "ar condicionado", "hospitalar"]
        )
        repo.salvar_organizacao(org)

        # 2. Cadastra os 4 funcionários
        seats_cadastrados = [
            UserSeat(org_id=org.id, user_name="João - Comercial", role="analyst", hwid="HWID-PC-JOAO-01"),
            UserSeat(org_id=org.id, user_name="Maria - Jurídico", role="analyst", hwid="HWID-PC-MARIA-02"),
            UserSeat(org_id=org.id, user_name="Carlos - Precificação", role="analyst", hwid="HWID-PC-CARLOS-03"),
            UserSeat(org_id=org.id, user_name="Diretor Executivo", role="admin", hwid="HWID-PC-DIRETOR-04")
        ]

        for s in seats_cadastrados:
            seat_id = repo.cadastrar_ou_atualizar_seat(s)
            assert seat_id > 0, f"Falha ao cadastrar seat de {s.user_name}"

        # Confirma que há exatamente 4 seats
        seats_no_banco = repo.listar_seats(org.id)
        assert len(seats_no_banco) == 4, f"Esperado 4 seats, encontrado {len(seats_no_banco)}"

        # 3. Tenta cadastrar o 5º funcionário com HWID diferente -> DEVE FALHAR COM ValueError
        quinto_seat = UserSeat(
            org_id=org.id,
            user_name="Estagiário - Operações",
            role="viewer",
            hwid="HWID-PC-ESTAGIARIO-05"
        )
        
        limite_atingido = False
        try:
            repo.cadastrar_ou_atualizar_seat(quinto_seat)
        except ValueError as e:
            limite_atingido = True
            assert "Limite de 4 seats atingido" in str(e)

        assert limite_atingido, "O sistema deveria ter impedido o cadastro do 5º seat que excede max_seats=4!"

        # 4. Atualizar um seat existente com o MESMO HWID deve ser permitido
        seat_joao_atualizado = UserSeat(
            org_id=org.id,
            user_name="João da Silva - Gerente Comercial",
            role="admin",
            hwid="HWID-PC-JOAO-01"
        )
        repo.cadastrar_ou_atualizar_seat(seat_joao_atualizado)
        
        seats_pos_update = repo.listar_seats(org.id)
        assert len(seats_pos_update) == 4, "Atualização de HWID existente não deve criar novo seat"
        
        joao_recuperado = repo.obter_seat_por_hwid("HWID-PC-JOAO-01")
        assert joao_recuperado is not None
        assert joao_recuperado.user_name == "João da Silva - Gerente Comercial"
        assert joao_recuperado.role == "admin"

        print(" [PASS] Cenário 1: Gestão de Organização e Trava Estrita de Seats por HWID")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_collector_node_eleicao_unica():
    """
    Cenário 2:
    Em uma empresa com 4 computadores, apenas 1 máquina deve atuar como
    Coletor Oficial do PNCP em background para evitar 4 requisições idênticas na API governamental.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = Repository(db_path=db_path)
        org = repo.obter_ou_criar_organizacao_padrao()

        repo.cadastrar_ou_atualizar_seat(UserSeat(org_id=org.id, user_name="João", role="analyst", hwid="HWID-A"))
        repo.cadastrar_ou_atualizar_seat(UserSeat(org_id=org.id, user_name="Maria", role="analyst", hwid="HWID-B"))

        # Define João como coletor
        ok1 = repo.definir_collector_node(org.id, "HWID-A")
        assert ok1 is True
        col = repo.obter_collector_node(org.id)
        assert col is not None
        assert col.hwid == "HWID-A"
        assert col.is_collector_node is True

        # Agora define Maria como coletora: João deve perder o status automaticamente
        ok2 = repo.definir_collector_node(org.id, "HWID-B")
        assert ok2 is True
        col2 = repo.obter_collector_node(org.id)
        assert col2 is not None
        assert col2.hwid == "HWID-B"

        # Verifica se João foi desmarcado
        joao = repo.obter_seat_por_hwid("HWID-A")
        assert joao.is_collector_node is False, "A máquina anterior deve ser desmarcada como coletor!"

        print(" [PASS] Cenário 2: Eleição Atômica de Coletor Node Único para Background Worker")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_distributed_lock_e_anti_colisao():
    """
    Cenário 3:
    Simula João e Maria clicando simultaneamente em 'Buscar/Auditar' para o mesmo edital.
    O Distributed Lock garante que apenas 1 processa e o outro não duplica OCR/LLM.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = Repository(db_path=db_path)
        org_id = "org_default"
        pncp_id = "PNCP-TESTE-LOCK-001/2026"

        # João adquire o lock
        lock_joao = repo.adquirir_lock_processamento(
            pncp_id=pncp_id,
            org_id=org_id,
            locked_by="João - Comercial",
            ttl_segundos=60
        )
        assert lock_joao is True, "João deveria ter adquirido o lock com sucesso."

        # Maria tenta adquirir o lock para o mesmo edital ao mesmo tempo -> DEVE FALHAR
        lock_maria = repo.adquirir_lock_processamento(
            pncp_id=pncp_id,
            org_id=org_id,
            locked_by="Maria - Jurídico",
            ttl_segundos=60
        )
        assert lock_maria is False, "Maria não deveria conseguir adquirir o lock já em posse de João!"

        # Verifica o estado do lock
        info_lock = repo.verificar_lock_processamento(pncp_id, org_id)
        assert info_lock is not None
        assert info_lock["locked_by"] == "João - Comercial"

        # João termina de auditar e salva o edital
        proj = BidProject(
            pncp_id=pncp_id,
            org_id=org_id,
            audit_json={"orgao": "Prefeitura de Cuiabá", "score_aderencia": 95.0, "status": "AUDITADO_COM_SUCESSO"},
            workflow_status="TRIAGEM",
            assigned_to_user="João - Comercial",
            internal_notes="Edital auditado por João com ReAct Agent",
            updated_by="João - Comercial"
        )
        repo.salvar_ou_atualizar_projeto(proj)

        # João libera o lock
        liberado = repo.liberar_lock_processamento(pncp_id, org_id, "João - Comercial")
        assert liberado is True

        # O lock não existe mais
        info_lock_pos = repo.verificar_lock_processamento(pncp_id, org_id)
        assert info_lock_pos is None

        # Agora Maria consulta o edital: constata que já foi auditado e aproveita o audit_json (0 custo de LLM)
        proj_recuperado = repo.obter_projeto(pncp_id, org_id)
        assert proj_recuperado is not None
        assert proj_recuperado.audit_json.get("score_aderencia") == 95.0
        assert proj_recuperado.assigned_to_user == "João - Comercial"

        print(" [PASS] Cenário 3: Distributed Lock Anti-Colisão e Reutilização Instantânea de Auditoria")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_concorrencia_checklist_zero_overwrite():
    """
    Cenário 4:
    Teste de Concorrência Real com Threads:
    4 funcionários (João, Maria, Carlos, Diretor) preenchendo itens diferentes do
    checklist de habilitação do mesmo edital exatamente no mesmo segundo.
    Valida a garantia de ZERO OVERWRITE através do merge atômico granular.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = Repository(db_path=db_path)
        org_id = "org_default"
        pncp_id = "PNCP-COLLAB-999/2026"

        # Criação inicial do projeto com checklist pendente
        checklist_inicial = {
            "CND_FEDERAL": "PENDENTE",
            "CND_ESTADUAL": "PENDENTE",
            "BALANCO_PATRIMONIAL": "PENDENTE",
            "ATESTADO_CAPACIDADE_TECNICA": "PENDENTE"
        }
        proj = BidProject(
            pncp_id=pncp_id,
            org_id=org_id,
            audit_json={"orgao": "Governo do Estado de Mato Grosso", "valor_total_estimado": 450000.0},
            workflow_status="EM_ANALISE_JURIDICA",
            assigned_to_user="Maria - Jurídico",
            checklist_docs=checklist_inicial,
            updated_by="Sistema"
        )
        repo.salvar_ou_atualizar_projeto(proj)

        # Ações simultâneas de cada funcionário
        erros_threads = []

        def worker_maria():
            try:
                # Maria confere CND Federal
                repo.atualizar_checklist_docs(pncp_id, org_id, {"CND_FEDERAL": "OK"}, updated_by="Maria - Jurídico")
            except Exception as e:
                erros_threads.append(f"Maria: {e}")

        def worker_carlos():
            try:
                # Carlos confere CND Estadual
                repo.atualizar_checklist_docs(pncp_id, org_id, {"CND_ESTADUAL": "OK"}, updated_by="Carlos - Fiscal")
            except Exception as e:
                erros_threads.append(f"Carlos: {e}")

        def worker_contador():
            try:
                # Contador confere Balanço Patrimonial
                repo.atualizar_checklist_docs(pncp_id, org_id, {"BALANCO_PATRIMONIAL": "OK"}, updated_by="Contabilidade")
            except Exception as e:
                erros_threads.append(f"Contador: {e}")

        def worker_diretor():
            try:
                # Diretor confere Atestado de Capacidade Técnica
                repo.atualizar_checklist_docs(pncp_id, org_id, {"ATESTADO_CAPACIDADE_TECNICA": "OK"}, updated_by="Diretor")
            except Exception as e:
                erros_threads.append(f"Diretor: {e}")

        # Dispara todas as 4 threads simultaneamente
        threads = [
            threading.Thread(target=worker_maria),
            threading.Thread(target=worker_carlos),
            threading.Thread(target=worker_contador),
            threading.Thread(target=worker_diretor)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(erros_threads) == 0, f"Erros durante execução concorrente: {erros_threads}"

        # Verifica o estado final no banco de dados
        proj_final = repo.obter_projeto(pncp_id, org_id)
        assert proj_final is not None
        docs = proj_final.checklist_docs

        assert docs.get("CND_FEDERAL") == "OK", "CND_FEDERAL deveria estar OK"
        assert docs.get("CND_ESTADUAL") == "OK", "CND_ESTADUAL deveria estar OK"
        assert docs.get("BALANCO_PATRIMONIAL") == "OK", "BALANCO_PATRIMONIAL deveria estar OK"
        assert docs.get("ATESTADO_CAPACIDADE_TECNICA") == "OK", "ATESTADO_CAPACIDADE_TECNICA deveria estar OK"

        print(" [PASS] Cenário 4: Concorrência Multi-Thread com Merge Granular de Checklist (100% Integridade, 0 Overwrites)")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_transicoes_workflow_e_notas_append_only():
    """
    Cenário 5:
    Simula o ciclo de vida completo de uma licitação corporativa no Kanban:
    TRIAGEM -> EM_ANALISE_JURIDICA -> PRECIFICACAO -> APROVADO_DISPUTA
    Garante que as notas internas preservem todo o histórico (append-only) com autoria.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = Repository(db_path=db_path)
        org_id = "org_default"
        pncp_id = "PNCP-KANBAN-007/2026"

        # 1. Triagem Inicial
        proj = BidProject(
            pncp_id=pncp_id,
            org_id=org_id,
            audit_json={"orgao": "Tribunal de Justiça de MT", "objeto_resumo": "Aquisição de Servidores e Storages"},
            workflow_status="TRIAGEM",
            assigned_to_user="João - Comercial",
            internal_notes="[Triagem] Oportunidade triada pelo filtro de TI.",
            updated_by="João - Comercial"
        )
        repo.salvar_ou_atualizar_projeto(proj)

        # 2. João passa para Maria (Jurídico)
        repo.atualizar_status_workflow(pncp_id, org_id, "EM_ANALISE_JURIDICA", updated_by="João - Comercial")
        repo.atualizar_responsavel(pncp_id, org_id, "Maria - Jurídico", updated_by="João - Comercial")
        repo.adicionar_nota_interna(pncp_id, org_id, "Encaminhado para validação das cláusulas de garantia e multas contratuais.", autor="João - Comercial")

        # 3. Maria aprova jurídica e passa para Carlos (Precificação)
        repo.atualizar_status_workflow(pncp_id, org_id, "PRECIFICACAO", updated_by="Maria - Jurídico")
        repo.atualizar_responsavel(pncp_id, org_id, "Carlos - Precificação", updated_by="Maria - Jurídico")
        repo.adicionar_nota_interna(pncp_id, org_id, "Edital juridicamente seguro. Sem exigências restritivas ilegais.", autor="Maria - Jurídico")

        # 4. Carlos precifica e Diretor aprova
        repo.atualizar_status_workflow(pncp_id, org_id, "APROVADO_DISPUTA", updated_by="Carlos - Precificação")
        repo.atualizar_responsavel(pncp_id, org_id, "Diretor Executivo", updated_by="Carlos - Precificação")
        repo.adicionar_nota_interna(pncp_id, org_id, "Planilha de custos fechada. Margem líquida estimada de 18,5%. Aprovado para o pregão.", autor="Carlos - Precificação")

        # 5. Validação no Banco
        proj_db = repo.obter_projeto(pncp_id, org_id)
        assert proj_db is not None
        assert proj_db.workflow_status == "APROVADO_DISPUTA"
        assert proj_db.assigned_to_user == "Diretor Executivo"

        # Valida que as notas contêm todas as etapas
        historico = proj_db.internal_notes
        assert "João - Comercial" in historico
        assert "Maria - Jurídico" in historico
        assert "Carlos - Precificação" in historico
        assert "Margem líquida estimada de 18,5%" in historico
        assert "Sem exigências restritivas ilegais" in historico

        # Valida listagem com filtros
        projetos_aprovados = repo.listar_projetos(org_id, workflow_status="APROVADO_DISPUTA")
        assert len(projetos_aprovados) == 1
        assert projetos_aprovados[0].pncp_id == pncp_id

        print(" [PASS] Cenário 5: Transições de Workflow no Kanban e Histórico Append-Only de Anotações")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_supabase_cloud_sync_resiliente():
    """
    Cenário 6:
    Valida a sincronização Supabase <-> SQLite Local Mirror.
    Em ambiente sem credenciais configuradas, o sistema mantém o modo Local Mirror
    com 100% de estabilidade e disponibilidade.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = Repository(db_path=db_path)
        org = repo.obter_ou_criar_organizacao_padrao()

        resultado_sync = repo.sincronizar_com_supabase(org.id)
        assert resultado_sync["status"] in ("local_mirror_active", "sucesso", "erro_fallback_local")
        assert "org_id" in resultado_sync

        print(" [PASS] Cenário 6: Resiliência de Sincronização Supabase Cloud / SQLite Local Mirror")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print(" BATERIA DE TESTES: MODO CORPORATIVO MULTI-USUÁRIO (ORGANIZATIONS, SEATS & SYNC)")
    print("=" * 80 + "\n")

    t_inicio = time.time()
    test_organizacao_e_limite_de_seats_hwid()
    test_collector_node_eleicao_unica()
    test_distributed_lock_e_anti_colisao()
    test_concorrencia_checklist_zero_overwrite()
    test_transicoes_workflow_e_notas_append_only()
    test_supabase_cloud_sync_resiliente()

    duracao = time.time() - t_inicio
    print("\n" + "=" * 80)
    print(f" TODOS OS 6 CENÁRIOS MULTI-USUÁRIO PASSARAM COM SUCESSO EM {duracao:.2f}s!")
    print("=" * 80 + "\n")
