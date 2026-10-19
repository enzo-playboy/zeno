from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field

class Cliente(BaseModel):
    id: Optional[int] = None
    nome: str
    cnpj: Optional[str] = None
    palavras_chave: List[str] = Field(default_factory=list)
    palavras_negativas: List[str] = Field(default_factory=list)
    telefone_whatsapp: Optional[str] = None
    ativo: bool = True
    created_at: Optional[datetime] = None

class ItemLote(BaseModel):
    item: int
    descricao: str
    quantidade: float
    valor_unitario_teto: Optional[float] = None
    pagina_referencia: Optional[int] = None

class EditalAuditado(BaseModel):
    numero_controle_pncp: str
    orgao: str
    modalidade: str
    data_disputa: Optional[str] = None
    portal_disputa: Optional[str] = None
    objeto_resumo: str
    valor_total_estimado: Optional[float] = None
    itens_interesse_cliente: List[ItemLote] = Field(default_factory=list)
    exigencias_habilitacao_criticas: List[str] = Field(default_factory=list)
    armadilhas_e_riscos: List[str] = Field(default_factory=list)
    score_aderencia: float = 0.0
    recomendacao_executiva: str = ""
    raw_pncp_data: Optional[Dict[str, Any]] = None
    status_processamento: str = "PENDENTE"
    hash_identificador: Optional[str] = None
    created_at: Optional[datetime] = None

class AlertaEnviado(BaseModel):
    id: Optional[int] = None
    numero_controle_pncp: str
    destinatario: str
    score: float
    mensagem_status: str
    enviado_em: Optional[datetime] = None


# ==============================================================================
# Modelos Corporativos Multi-Usuário (Organizations, Seats & Cloud Sync)
# ==============================================================================

class Organization(BaseModel):
    """Representa a empresa cliente titular do plano corporativo."""
    id: str
    name: str
    cnpj: Optional[str] = None
    plan_tier: str = "CORPORATE"  # PRO, CORPORATE, ENTERPRISE
    max_seats: int = 4  # Quantidade de licenças/máquinas contratadas
    uf_target: str = "MT"
    keywords: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class UserSeat(BaseModel):
    """Representa o computador / posto de trabalho de cada funcionário."""
    id: Optional[Union[int, str]] = None
    org_id: str
    user_name: str  # Ex: "João - Comercial", "Maria - Jurídico"
    role: str = "analyst"  # admin, analyst, viewer
    hwid: str  # Trava individual por hardware do PC
    is_collector_node: bool = False  # Define qual PC faz a varredura automática em 2º plano
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None


class BidProject(BaseModel):
    """Representa o Edital como Projeto Colaborativo da Equipe Corporativa."""
    id: Optional[int] = None
    pncp_id: str
    org_id: str
    audit_json: Dict[str, Any] = Field(default_factory=dict)
    workflow_status: str = "TRIAGEM"  # TRIAGEM, EM_ANALISE_JURIDICA, PRECIFICACAO, APROVADO_DISPUTA, DESCARTADO
    assigned_to_user: Optional[str] = None  # Qual funcionário é o dono atual daquela licitação
    internal_notes: Optional[str] = None  # Histórico de comentários / anotações da equipe
    checklist_docs: Dict[str, str] = Field(default_factory=dict)  # Ex: {"certidao_federal": "OK", "balanco": "PENDENTE"}
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ProcessingLock(BaseModel):
    """Distributed lock para evitar que dois funcionários processem o mesmo edital ao mesmo tempo."""
    pncp_id: str
    org_id: str
    locked_by_user: str
    locked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

