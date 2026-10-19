from datetime import datetime
from typing import Optional, List, Dict, Any
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
