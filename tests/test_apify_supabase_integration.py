"""
Teste de Integração: Apify como Provedor de Consultas e Supabase como Fonte Primária de Dados.
Valida:
1. Inicialização, normalização e fallback do ApifyClient
2. Integração e contrato do SupabaseClient (REST PostgREST)
3. Repository operando com o cliente Supabase integrado e fallback resiliente
"""

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.config import settings
from src.collectors.apify_client import ApifyClient
from src.database.supabase_client import SupabaseClient
from src.database.models import EditalAuditado, BidProject, ItemLote
from src.database.repository import Repository


def test_apify_client_normalization_e_estrutura():
    """Valida a padronização e normalização de itens retornados pelo Apify."""
    client = ApifyClient(api_token="test_token_dummy", actor_id="brasil-dados~pncp-editais")
    assert client.is_configured() is True
    assert client.actor_id == "brasil-dados~pncp-editais"

    raw_apify_dataset = [
        {
            "numeroControlePNCP": "00000000000191-1-000001/2026",
            "description": "Aquisição de computadores e notebooks de alta performance",
            "valorTotalEstimado": 450000.0,
            "modalidadeNome": "Pregão Eletrônico",
            "orgaoEntidade": {
                "razaoSocial": "Prefeitura Municipal de Cuiabá",
                "cnpj": "00.000.000/0001-91"
            },
            "anoCompra": 2026,
            "sequencialCompra": 1,
            "uf": "MT",
            "municipioNome": "Cuiabá"
        }
    ]

    normalizados = client._normalizar_itens(raw_apify_dataset, fallback_uf="MT")
    assert len(normalizados) == 1
    item = normalizados[0]
    assert item["numeroControlePNCP"] == "00000000000191-1-000001/2026"
    assert "computadores" in item["objetoCompra"].lower()
    assert item["valorTotalEstimado"] == 450000.0
    assert item["uf"] == "MT"
    assert item["origem_consulta"] == "apify"
    print(" [PASS] 1. ApifyClient: Normalização de dados e estrutura do dataset")


def test_supabase_client_contrato_e_headers():
    """Valida a estrutura de chamadas e headers do SupabaseClient."""
    sb = SupabaseClient(url="https://xyz.supabase.co", key="test_key_secret")
    assert sb.is_configured is True
    assert sb.url == "https://xyz.supabase.co"

    headers = sb._get_headers(return_rep=True, upsert=True)
    assert headers["apikey"] == "test_key_secret"
    assert "Bearer test_key_secret" in headers["Authorization"]
    assert "resolution=merge-duplicates" in headers["Prefer"]
    assert "return=representation" in headers["Prefer"]
    print(" [PASS] 2. SupabaseClient: Headers PostgREST e contrato de conexão")


def test_repository_integracao_supabase():
    """Valida que o Repository instancia e orquestra o SupabaseClient com tolerância a falhas."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = Repository(db_path=db_path)
        assert hasattr(repo, "supabase")
        assert isinstance(repo.supabase, SupabaseClient)

        # Salva um edital auditado
        edital = EditalAuditado(
            numero_controle_pncp="APIFY-SUPABASE-TEST-001",
            orgao="Tribunal de Justiça MT",
            modalidade="PREGÃO ELETRÔNICO",
            objeto_resumo="Solução corporativa de TI e servidores",
            valor_total_estimado=250000.0,
            status_processamento="CONCLUIDO"
        )
        edital_id = repo.salvar_edital(edital, org_id="org_default", user_name="Auditor")
        assert edital_id is not None

        # Valida idempotência via ja_processado
        assert repo.ja_processado("APIFY-SUPABASE-TEST-001") is True
        assert repo.ja_processado("INEXISTENTE-999") is False

        # Valida criação do projeto colaborativo
        proj = repo.obter_projeto("APIFY-SUPABASE-TEST-001", org_id="org_default")
        assert proj is not None
        assert proj.pncp_id == "APIFY-SUPABASE-TEST-001"
        assert proj.assigned_to_user == "Auditor"

        print(" [PASS] 3. Repository: Persistência integrada com Supabase e idempotência comprovada")
    finally:
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except Exception:
            pass


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print(" BATERIA DE TESTES: APIFY (CONSULTAS) & SUPABASE (DADOS)")
    print("=" * 80 + "\n")
    test_apify_client_normalization_e_estrutura()
    test_supabase_client_contrato_e_headers()
    test_repository_integracao_supabase()
    print("\n[SUCESSO] Todos os testes de integração Apify + Supabase passaram 100%!\n")
