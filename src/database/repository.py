import sqlite3
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

from src.database.models import (
    EditalAuditado,
    Cliente,
    AlertaEnviado,
    Organization,
    UserSeat,
    BidProject,
    ProcessingLock
)
from src.config import settings

logger = logging.getLogger("ZenoRepository")


class Repository:
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            raw_url = settings.DATABASE_URL
            if raw_url.startswith("sqlite:///"):
                self.db_path = raw_url.replace("sqlite:///", "")
            else:
                self.db_path = "./bid_engine.db"
        else:
            self.db_path = db_path
            
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabela de Editais e Auditorias (Idempotência via hash_pncp)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS editais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_controle_pncp TEXT UNIQUE,
                hash_identificador TEXT UNIQUE,
                orgao TEXT,
                modalidade TEXT,
                data_disputa TEXT,
                portal_disputa TEXT,
                objeto_resumo TEXT,
                valor_total_estimado REAL,
                score_aderencia REAL,
                recomendacao_executiva TEXT,
                itens_json TEXT,
                exigencias_json TEXT,
                armadilhas_json TEXT,
                raw_pncp_json TEXT,
                status_processamento TEXT DEFAULT 'PENDENTE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # Tabela de Clientes para monitoramento
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                cnpj TEXT,
                palavras_chave TEXT,
                palavras_negativas TEXT,
                telefone_whatsapp TEXT,
                ativo INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # Tabela de Alertas enviados (WhatsApp / Webhook)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS alertas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_controle_pncp TEXT,
                destinatario TEXT,
                score REAL,
                mensagem_status TEXT,
                enviado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # ==================================================================
            # Schema Corporativo Multi-Usuário (Organizations, Seats & Projects)
            # ==================================================================

            # 1. Organizations (A Empresa Cliente)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS organizations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                cnpj TEXT,
                plan_tier TEXT DEFAULT 'CORPORATE',
                max_seats INTEGER DEFAULT 4,
                uf_target TEXT DEFAULT 'MT',
                keywords TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # 2. User Seats (Cada Funcionário / Computador na empresa)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_seats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                role TEXT DEFAULT 'analyst',
                hwid TEXT NOT NULL,
                is_collector_node INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(org_id) REFERENCES organizations(id),
                UNIQUE(org_id, hwid)
            );
            """)

            # 3. Bid Projects (O Edital como Projeto Colaborativo da Empresa)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS bid_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pncp_id TEXT NOT NULL,
                org_id TEXT NOT NULL,
                audit_json TEXT,
                workflow_status TEXT DEFAULT 'TRIAGEM',
                assigned_to_user TEXT,
                internal_notes TEXT,
                checklist_docs TEXT,
                updated_by TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(org_id) REFERENCES organizations(id),
                UNIQUE(pncp_id, org_id)
            );
            """)

            # 4. Processing Locks (Distributed Lock anti-colisão de scraping/OCR)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS processing_locks (
                pncp_id TEXT NOT NULL,
                org_id TEXT NOT NULL,
                locked_by_user TEXT NOT NULL,
                locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                PRIMARY KEY (pncp_id, org_id)
            );
            """)

            # Índices de performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_editais_pncp ON editais(numero_controle_pncp);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bid_projects_org ON bid_projects(org_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bid_projects_pncp ON bid_projects(pncp_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_seats_org ON user_seats(org_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_seats_hwid ON user_seats(hwid);")

            conn.commit()

        # Garante a existência da organização padrão
        self.obter_ou_criar_organizacao_padrao()

    # ==========================================================================
    # Gestão de Organizações e Assentos (Seats)
    # ==========================================================================

    def obter_ou_criar_organizacao_padrao(self) -> Organization:
        """Garante que sempre exista ao menos uma organização padrão corporativa."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM organizations WHERE id = 'org_default'")
            row = cursor.fetchone()
            if row:
                return Organization(
                    id=row["id"],
                    name=row["name"],
                    cnpj=row["cnpj"],
                    plan_tier=row["plan_tier"],
                    max_seats=row["max_seats"],
                    uf_target=row["uf_target"],
                    keywords=json.loads(row["keywords"]) if row["keywords"] else settings.target_keywords_list,
                    created_at=row["created_at"]
                )

            # Criar organização default com 4 seats
            default_org = Organization(
                id="org_default",
                name="Zeno Corporate Enterprise",
                cnpj="00.000.000/0001-99",
                plan_tier="CORPORATE",
                max_seats=4,
                uf_target=settings.TARGET_UF,
                keywords=settings.target_keywords_list
            )
            cursor.execute("""
            INSERT INTO organizations (id, name, cnpj, plan_tier, max_seats, uf_target, keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                default_org.id,
                default_org.name,
                default_org.cnpj,
                default_org.plan_tier,
                default_org.max_seats,
                default_org.uf_target,
                json.dumps(default_org.keywords, ensure_ascii=False)
            ))
            conn.commit()
            return default_org

    def salvar_organizacao(self, org: Organization) -> str:
        """Cadastra ou atualiza uma organização corporativa."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO organizations (id, name, cnpj, plan_tier, max_seats, uf_target, keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                cnpj = excluded.cnpj,
                plan_tier = excluded.plan_tier,
                max_seats = excluded.max_seats,
                uf_target = excluded.uf_target,
                keywords = excluded.keywords
            """, (
                org.id,
                org.name,
                org.cnpj,
                org.plan_tier,
                org.max_seats,
                org.uf_target,
                json.dumps(org.keywords, ensure_ascii=False)
            ))
            conn.commit()
            return org.id

    def obter_organizacao(self, org_id: str) -> Optional[Organization]:
        """Obtém detalhes da organização pelo ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM organizations WHERE id = ?", (org_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Organization(
                id=row["id"],
                name=row["name"],
                cnpj=row["cnpj"],
                plan_tier=row["plan_tier"],
                max_seats=row["max_seats"],
                uf_target=row["uf_target"],
                keywords=json.loads(row["keywords"]) if row["keywords"] else [],
                created_at=row["created_at"]
            )

    def cadastrar_ou_atualizar_seat(self, seat: UserSeat) -> int:
        """
        Cadastra ou atualiza um posto de trabalho/funcionário.
        Valida rigorosamente a trava por HWID e a cota máxima de max_seats.
        Retorna o ID do assento (int > 0).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Obter max_seats da organização
            cursor.execute("SELECT max_seats FROM organizations WHERE id = ?", (seat.org_id,))
            org_row = cursor.fetchone()
            if not org_row:
                self.obter_ou_criar_organizacao_padrao()
                cursor.execute("SELECT max_seats FROM organizations WHERE id = ?", (seat.org_id,))
                org_row = cursor.fetchone()
            max_seats = org_row["max_seats"] if org_row else 4

            # 2. Verificar se HWID já possui assento registrado nesta org
            cursor.execute("SELECT id FROM user_seats WHERE org_id = ? AND hwid = ?", (seat.org_id, seat.hwid))
            existing_hwid = cursor.fetchone()

            # 3. Se for um novo HWID, verificar se já estourou o limite de máquinas
            if not existing_hwid:
                cursor.execute("SELECT COUNT(DISTINCT hwid) as qtd FROM user_seats WHERE org_id = ?", (seat.org_id,))
                qtd_atual = cursor.fetchone()["qtd"] or 0
                if qtd_atual >= max_seats:
                    raise ValueError(
                        f"Limite de {max_seats} seats atingido para a organização '{seat.org_id}'. "
                        f"Não é possível cadastrar novo computador com HWID '{seat.hwid}'."
                    )

            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
            INSERT INTO user_seats (org_id, user_name, role, hwid, is_collector_node, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(org_id, hwid) DO UPDATE SET
                user_name = excluded.user_name,
                role = excluded.role,
                is_collector_node = excluded.is_collector_node,
                last_active_at = excluded.last_active_at
            """, (
                seat.org_id,
                seat.user_name,
                seat.role,
                seat.hwid,
                1 if seat.is_collector_node else 0,
                now_str,
                now_str
            ))
            conn.commit()

            # Se este nó foi designado como collector node, desativa os outros da organização
            if seat.is_collector_node:
                cursor.execute(
                    "UPDATE user_seats SET is_collector_node = 0 WHERE org_id = ? AND hwid != ?",
                    (seat.org_id, seat.hwid)
                )
                conn.commit()

            cursor.execute("SELECT id FROM user_seats WHERE org_id = ? AND hwid = ?", (seat.org_id, seat.hwid))
            row = cursor.fetchone()
            return int(row["id"]) if row else 1

    def listar_seats(self, org_id: str) -> List[UserSeat]:
        """Lista todos os postos de trabalho/funcionários de uma organização."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_seats WHERE org_id = ? ORDER BY user_name ASC", (org_id,))
            rows = cursor.fetchall()
            return [
                UserSeat(
                    id=r["id"],
                    org_id=r["org_id"],
                    user_name=r["user_name"],
                    role=r["role"],
                    hwid=r["hwid"],
                    is_collector_node=bool(r["is_collector_node"]),
                    created_at=r["created_at"],
                    last_active_at=r["last_active_at"]
                ) for r in rows
            ]

    def obter_seat_por_hwid(self, arg1: str, arg2: Optional[str] = None) -> Optional[UserSeat]:
        """
        Localiza um assento pelo HWID ou pelo par (org_id, HWID).
        Compatível com obter_seat_por_hwid(hwid) e obter_seat_por_hwid(org_id, hwid).
        """
        if arg2 is not None:
            org_id = arg1
            hwid = arg2
        else:
            org_id = None
            hwid = arg1

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if org_id:
                cursor.execute("SELECT * FROM user_seats WHERE org_id = ? AND hwid = ?", (org_id, hwid))
            else:
                cursor.execute("SELECT * FROM user_seats WHERE hwid = ? ORDER BY id DESC LIMIT 1", (hwid,))
            r = cursor.fetchone()
            if not r:
                return None
            return UserSeat(
                id=r["id"],
                org_id=r["org_id"],
                user_name=r["user_name"],
                role=r["role"],
                hwid=r["hwid"],
                is_collector_node=bool(r["is_collector_node"]),
                created_at=r["created_at"],
                last_active_at=r["last_active_at"]
            )

    def definir_collector_node(self, org_id: str, hwid: str) -> bool:
        """Define com exclusividade qual computador fará a varredura automática no PNCP."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE user_seats SET is_collector_node = 0 WHERE org_id = ?", (org_id,))
            cursor.execute("UPDATE user_seats SET is_collector_node = 1 WHERE org_id = ? AND hwid = ?", (org_id, hwid))
            conn.commit()
            return cursor.rowcount > 0

    def obter_collector_node(self, org_id: str) -> Optional[UserSeat]:
        """Retorna o computador designado como coletor da empresa."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_seats WHERE org_id = ? AND is_collector_node = 1", (org_id,))
            r = cursor.fetchone()
            if not r:
                return None
            return UserSeat(
                id=r["id"],
                org_id=r["org_id"],
                user_name=r["user_name"],
                role=r["role"],
                hwid=r["hwid"],
                is_collector_node=True,
                created_at=r["created_at"],
                last_active_at=r["last_active_at"]
            )

    # ==========================================================================
    # Gestão Colaborativa de Projetos de Editais (bid_projects)
    # ==========================================================================

    def salvar_ou_atualizar_projeto(self, projeto: BidProject) -> int:
        """
        Salva ou atualiza um projeto colaborativo de licitação.
        Realiza merge de anotações e checklist sem sobrescrever dados de outros funcionários.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, checklist_docs, internal_notes, assigned_to_user, workflow_status FROM bid_projects WHERE pncp_id = ? AND org_id = ?",
                (projeto.pncp_id, projeto.org_id)
            )
            row = cursor.fetchone()
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            if row:
                existing_docs = json.loads(row["checklist_docs"]) if row["checklist_docs"] else {}
                merged_docs = {**existing_docs, **projeto.checklist_docs}

                existing_notes = row["internal_notes"] or ""
                new_notes = projeto.internal_notes or ""
                if new_notes and new_notes not in existing_notes:
                    final_notes = f"{existing_notes}\n{new_notes}".strip() if existing_notes else new_notes
                else:
                    final_notes = existing_notes or new_notes

                final_assigned = projeto.assigned_to_user if projeto.assigned_to_user is not None else row["assigned_to_user"]
                final_status = projeto.workflow_status if projeto.workflow_status else row["workflow_status"]
                final_audit = projeto.audit_json if projeto.audit_json else {}

                cursor.execute("""
                UPDATE bid_projects SET
                    audit_json = ?,
                    workflow_status = ?,
                    assigned_to_user = ?,
                    internal_notes = ?,
                    checklist_docs = ?,
                    updated_by = ?,
                    updated_at = ?
                WHERE pncp_id = ? AND org_id = ?
                """, (
                    json.dumps(final_audit, ensure_ascii=False, default=str),
                    final_status,
                    final_assigned,
                    final_notes,
                    json.dumps(merged_docs, ensure_ascii=False, default=str),
                    projeto.updated_by or "Sistema",
                    now_str,
                    projeto.pncp_id,
                    projeto.org_id
                ))
                conn.commit()
                return row["id"]
            else:
                cursor.execute("""
                INSERT INTO bid_projects (
                    pncp_id, org_id, audit_json, workflow_status, assigned_to_user,
                    internal_notes, checklist_docs, updated_by, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    projeto.pncp_id,
                    projeto.org_id,
                    json.dumps(projeto.audit_json or {}, ensure_ascii=False, default=str),
                    projeto.workflow_status or "TRIAGEM",
                    projeto.assigned_to_user,
                    projeto.internal_notes,
                    json.dumps(projeto.checklist_docs or {}, ensure_ascii=False, default=str),
                    projeto.updated_by or "Sistema",
                    now_str,
                    now_str
                ))
                conn.commit()
                return cursor.lastrowid

    def obter_projeto(self, pncp_id: str, org_id: str) -> Optional[BidProject]:
        """Recupera um projeto de licitação da organização."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bid_projects WHERE pncp_id = ? AND org_id = ?", (pncp_id, org_id))
            r = cursor.fetchone()
            if not r:
                return None
            return BidProject(
                id=r["id"],
                pncp_id=r["pncp_id"],
                org_id=r["org_id"],
                audit_json=json.loads(r["audit_json"]) if r["audit_json"] else {},
                workflow_status=r["workflow_status"] or "TRIAGEM",
                assigned_to_user=r["assigned_to_user"],
                internal_notes=r["internal_notes"],
                checklist_docs=json.loads(r["checklist_docs"]) if r["checklist_docs"] else {},
                updated_by=r["updated_by"],
                updated_at=r["updated_at"],
                created_at=r["created_at"]
            )

    def listar_projetos(
        self,
        org_id: str,
        workflow_status: Optional[str] = None,
        assigned_to_user: Optional[str] = None,
        limit: int = 100
    ) -> List[BidProject]:
        """Lista editais em andamento na empresa com filtros colaborativos."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM bid_projects WHERE org_id = ?"
            params: List[Any] = [org_id]
            if workflow_status:
                query += " AND workflow_status = ?"
                params.append(workflow_status)
            if assigned_to_user:
                query += " AND assigned_to_user = ?"
                params.append(assigned_to_user)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [
                BidProject(
                    id=r["id"],
                    pncp_id=r["pncp_id"],
                    org_id=r["org_id"],
                    audit_json=json.loads(r["audit_json"]) if r["audit_json"] else {},
                    workflow_status=r["workflow_status"] or "TRIAGEM",
                    assigned_to_user=r["assigned_to_user"],
                    internal_notes=r["internal_notes"],
                    checklist_docs=json.loads(r["checklist_docs"]) if r["checklist_docs"] else {},
                    updated_by=r["updated_by"],
                    updated_at=r["updated_at"],
                    created_at=r["created_at"]
                ) for r in rows
            ]

    def atualizar_status_workflow(self, pncp_id: str, org_id: str, novo_status: str, updated_by: str) -> bool:
        """Transiciona o status do edital no Kanban da equipe."""
        status_validos = {"TRIAGEM", "EM_ANALISE_JURIDICA", "PRECIFICACAO", "APROVADO_DISPUTA", "DESCARTADO"}
        if novo_status not in status_validos:
            raise ValueError(f"Status inválido: '{novo_status}'. Válidos: {status_validos}")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
            UPDATE bid_projects 
            SET workflow_status = ?, updated_by = ?, updated_at = ?
            WHERE pncp_id = ? AND org_id = ?
            """, (novo_status, updated_by, now_str, pncp_id, org_id))
            conn.commit()
            return cursor.rowcount > 0

    def atualizar_responsavel(self, pncp_id: str, org_id: str, assigned_to_user: str, updated_by: str) -> bool:
        """Transfere a responsabilidade do edital para outro funcionário."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
            UPDATE bid_projects 
            SET assigned_to_user = ?, updated_by = ?, updated_at = ?
            WHERE pncp_id = ? AND org_id = ?
            """, (assigned_to_user, updated_by, now_str, pncp_id, org_id))
            conn.commit()
            return cursor.rowcount > 0

    def atualizar_checklist_docs(self, pncp_id: str, org_id: str, docs_delta: Dict[str, str], updated_by: str) -> Dict[str, str]:
        """
        Atualiza o checklist de habilitação através de merge granular.
        Garante que múltiplos funcionários preenchendo certidões distintas não sobrescrevam o progresso um do outro.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT checklist_docs FROM bid_projects WHERE pncp_id = ? AND org_id = ?", (pncp_id, org_id))
            row = cursor.fetchone()

            current_docs = {}
            if row and row["checklist_docs"]:
                try:
                    current_docs = json.loads(row["checklist_docs"])
                except Exception:
                    current_docs = {}

            current_docs.update(docs_delta)
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
            UPDATE bid_projects 
            SET checklist_docs = ?, updated_by = ?, updated_at = ?
            WHERE pncp_id = ? AND org_id = ?
            """, (json.dumps(current_docs, ensure_ascii=False, default=str), updated_by, now_str, pncp_id, org_id))
            conn.commit()
            return current_docs

    def adicionar_nota_interna(self, pncp_id: str, org_id: str, nota: str, autor: str) -> str:
        """Adiciona uma anotação com timestamp e autor ao histórico do edital (append-only)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT internal_notes FROM bid_projects WHERE pncp_id = ? AND org_id = ?", (pncp_id, org_id))
            row = cursor.fetchone()

            current_notes = (row["internal_notes"] or "") if row else ""
            timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            nova_linha = f"[{timestamp} - {autor}]: {nota.strip()}"

            updated_notes = f"{current_notes}\n{nova_linha}".strip() if current_notes else nova_linha
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
            UPDATE bid_projects 
            SET internal_notes = ?, updated_by = ?, updated_at = ?
            WHERE pncp_id = ? AND org_id = ?
            """, (updated_notes, autor, now_str, pncp_id, org_id))
            conn.commit()
            return updated_notes

    # ==========================================================================
    # Distributed Lock Anti-Colisão (Anti-Redundância de OCR e LLM)
    # ==========================================================================

    def adquirir_lock_processamento(
        self,
        pncp_id: str,
        org_id: str,
        user_name: Optional[str] = None,
        locked_by: Optional[str] = None,
        timeout_seconds: int = 120,
        ttl_segundos: Optional[int] = None
    ) -> bool:
        """
        Tenta adquirir o lock de processamento de um edital.
        Retorna True se o lock foi concedido ao usuário, ou False se outro funcionário estiver processando.
        """
        user = locked_by or user_name or "Sistema"
        timeout = ttl_segundos if ttl_segundos is not None else timeout_seconds
        now = datetime.utcnow()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        expires_at = (now + timedelta(seconds=timeout)).strftime("%Y-%m-%d %H:%M:%S")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Remover locks que já expiraram
            cursor.execute("DELETE FROM processing_locks WHERE expires_at <= ?", (now_str,))

            # 2. Verificar se existe lock ativo para (pncp_id, org_id)
            cursor.execute(
                "SELECT locked_by_user, expires_at FROM processing_locks WHERE pncp_id = ? AND org_id = ?",
                (pncp_id, org_id)
            )
            row = cursor.fetchone()
            if row:
                if row["locked_by_user"] == user:
                    # O mesmo usuário renova seu lock
                    cursor.execute(
                        "UPDATE processing_locks SET expires_at = ?, locked_at = ? WHERE pncp_id = ? AND org_id = ?",
                        (expires_at, now_str, pncp_id, org_id)
                    )
                    conn.commit()
                    return True
                else:
                    # Outro funcionário está com o lock ativo
                    return False

            # 3. Tentar inserir lock
            try:
                cursor.execute(
                    "INSERT INTO processing_locks (pncp_id, org_id, locked_by_user, locked_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                    (pncp_id, org_id, user, now_str, expires_at)
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def liberar_lock_processamento(
        self,
        pncp_id: str,
        org_id: str,
        user_name: Optional[str] = None,
        locked_by: Optional[str] = None
    ) -> bool:
        """Libera o lock de processamento do edital."""
        user = locked_by or user_name or ""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM processing_locks WHERE pncp_id = ? AND org_id = ? AND (locked_by_user = ? OR ? = 'admin' OR ? = '')",
                (pncp_id, org_id, user, user, user)
            )
            conn.commit()
            return cursor.rowcount > 0

    def verificar_lock_processamento(self, pncp_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        """Verifica se há lock ativo para o edital."""
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM processing_locks WHERE pncp_id = ? AND org_id = ? AND expires_at > ?",
                (pncp_id, org_id, now_str)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "pncp_id": row["pncp_id"],
                "org_id": row["org_id"],
                "locked_by": row["locked_by_user"],
                "locked_by_user": row["locked_by_user"],
                "locked_at": row["locked_at"],
                "expires_at": row["expires_at"]
            }

    # ==========================================================================
    # Sincronização Supabase Cloud <-> SQLite Local Mirror
    # ==========================================================================

    def sincronizar_com_supabase(self, org_id: str = "org_default") -> Dict[str, Any]:
        """
        Sincroniza bidirecionalmente a base local com a nuvem Supabase.
        Se as credenciais não estiverem configuradas, opera como Local Mirror com total integridade.
        """
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            logger.info("[Sync] Modo Local Mirror ativo. Credenciais Supabase não configuradas no ambiente.")
            return {
                "status": "local_mirror_active",
                "org_id": org_id,
                "supabase_configured": False,
                "synced_projects": 0,
                "mensagem": "Operando no modo SQLite Local Mirror com suporte corporativo completo."
            }

        try:
            import httpx
            headers = {
                "apikey": settings.SUPABASE_KEY,
                "Authorization": f"Bearer {settings.SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            client = httpx.Client(timeout=15.0)

            # 1. Puxar projetos atualizados da nuvem
            url_projetos = f"{settings.SUPABASE_URL}/rest/v1/bid_projects?org_id=eq.{org_id}"
            resp = client.get(url_projetos, headers=headers)
            synced_count = 0
            if resp.status_code == 200:
                cloud_projects = resp.json()
                for cp in cloud_projects:
                    proj = BidProject(
                        pncp_id=cp["pncp_id"],
                        org_id=cp["org_id"],
                        audit_json=cp.get("audit_json") or {},
                        workflow_status=cp.get("workflow_status") or "TRIAGEM",
                        assigned_to_user=cp.get("assigned_to_user"),
                        internal_notes=cp.get("internal_notes"),
                        checklist_docs=cp.get("checklist_docs") or {},
                        updated_by=cp.get("updated_by"),
                        updated_at=cp.get("updated_at")
                    )
                    self.salvar_ou_atualizar_projeto(proj)
                    synced_count += 1

            # 2. Enviar projetos locais para a nuvem (Upsert)
            locais = self.listar_projetos(org_id=org_id)
            for lp in locais:
                payload = {
                    "pncp_id": lp.pncp_id,
                    "org_id": lp.org_id,
                    "audit_json": lp.audit_json,
                    "workflow_status": lp.workflow_status,
                    "assigned_to_user": lp.assigned_to_user,
                    "internal_notes": lp.internal_notes,
                    "checklist_docs": lp.checklist_docs,
                    "updated_by": lp.updated_by
                }
                client.post(
                    f"{settings.SUPABASE_URL}/rest/v1/bid_projects",
                    headers={**headers, "Prefer": "resolution=merge-duplicates"},
                    json=payload
                )

            return {
                "status": "sucesso",
                "org_id": org_id,
                "supabase_configured": True,
                "synced_projects": synced_count,
                "mensagem": f"Sincronização concluída com Supabase ({synced_count} projetos sincronizados)."
            }
        except Exception as e:
            logger.warning(f"[Sync] Erro ao sincronizar com Supabase: {e}. Mantendo Local Mirror.")
            return {
                "status": "erro_fallback_local",
                "org_id": org_id,
                "supabase_configured": True,
                "erro": str(e),
                "mensagem": "Falha na conexão com Supabase. Dados preservados no SQLite Local Mirror."
            }

    # ==========================================================================
    # Métodos Legados / Compatibilidade Retroativa
    # ==========================================================================

    @staticmethod
    def gerar_hash_edital(numero_controle_pncp: str, data_atualizacao: str = "") -> str:
        content = f"{numero_controle_pncp}:{data_atualizacao}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def ja_processado(self, numero_controle_pncp: str) -> bool:
        """Verifica idempotência: se o edital já foi processado/auditado anteriormente."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM editais WHERE numero_controle_pncp = ? AND status_processamento = 'CONCLUIDO'", (numero_controle_pncp,))
            row = cursor.fetchone()
            if row:
                return True
            
            # Também verifica se já existe na tabela de projetos colaborativos
            cursor.execute("SELECT id FROM bid_projects WHERE pncp_id = ? AND audit_json IS NOT NULL AND audit_json != '{}'", (numero_controle_pncp,))
            return cursor.fetchone() is not None

    def salvar_edital(self, edital: EditalAuditado, org_id: str = "org_default", user_name: str = "Sistema") -> int:
        hash_id = self.gerar_hash_edital(edital.numero_controle_pncp)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO editais (
                numero_controle_pncp,
                hash_identificador,
                orgao,
                modalidade,
                data_disputa,
                portal_disputa,
                objeto_resumo,
                valor_total_estimado,
                score_aderencia,
                recomendacao_executiva,
                itens_json,
                exigencias_json,
                armadilhas_json,
                raw_pncp_json,
                status_processamento,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(numero_controle_pncp) DO UPDATE SET
                score_aderencia = excluded.score_aderencia,
                recomendacao_executiva = excluded.recomendacao_executiva,
                itens_json = excluded.itens_json,
                exigencias_json = excluded.exigencias_json,
                armadilhas_json = excluded.armadilhas_json,
                status_processamento = excluded.status_processamento,
                updated_at = CURRENT_TIMESTAMP
            """, (
                edital.numero_controle_pncp,
                hash_id,
                edital.orgao,
                edital.modalidade,
                edital.data_disputa,
                edital.portal_disputa,
                edital.objeto_resumo,
                edital.valor_total_estimado,
                edital.score_aderencia,
                edital.recomendacao_executiva,
                json.dumps([i.model_dump() for i in edital.itens_interesse_cliente], ensure_ascii=False),
                json.dumps(edital.exigencias_habilitacao_criticas, ensure_ascii=False),
                json.dumps(edital.armadilhas_e_riscos, ensure_ascii=False),
                json.dumps(edital.raw_pncp_data or {}, ensure_ascii=False),
                edital.status_processamento
            ))
            conn.commit()
            edital_id = cursor.lastrowid

        # Sincroniza automaticamente com a tabela colaborativa bid_projects
        proj = BidProject(
            pncp_id=edital.numero_controle_pncp,
            org_id=org_id,
            audit_json=edital.model_dump(),
            workflow_status="TRIAGEM",
            assigned_to_user=user_name,
            internal_notes=f"Edital auditado por {user_name} e registrado no catálogo colaborativo.",
            checklist_docs={ex: "PENDENTE" for ex in edital.exigencias_habilitacao_criticas},
            updated_by=user_name
        )
        self.salvar_ou_atualizar_projeto(proj)

        return edital_id

    def registrar_alerta(self, alerta: AlertaEnviado) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO alertas (numero_controle_pncp, destinatario, score, mensagem_status)
            VALUES (?, ?, ?, ?)
            """, (alerta.numero_controle_pncp, alerta.destinatario, alerta.score, alerta.mensagem_status))
            conn.commit()
            return cursor.lastrowid

    def listar_editais_dashboard(self, limit: int = 50, busca: str = "") -> List[Dict[str, Any]]:
        """Lista os editais para exibição no Dashboard interativo."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
            SELECT 
                numero_controle_pncp,
                orgao,
                modalidade,
                data_disputa,
                portal_disputa,
                objeto_resumo,
                valor_total_estimado,
                score_aderencia,
                recomendacao_executiva,
                itens_json,
                exigencias_json,
                armadilhas_json,
                status_processamento,
                created_at
            FROM editais
            """
            params = []
            if busca:
                query += " WHERE orgao LIKE ? OR objeto_resumo LIKE ? OR numero_controle_pncp LIKE ?"
                term = f"%{busca}%"
                params.extend([term, term, term])

            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            result = []
            for r in rows:
                result.append({
                    "numero_controle_pncp": r["numero_controle_pncp"],
                    "orgao": r["orgao"] or "Órgão Público",
                    "modalidade": r["modalidade"] or "Pregão Eletrônico",
                    "data_disputa": r["data_disputa"] or "A definir",
                    "portal_disputa": r["portal_disputa"] or "PNCP",
                    "objeto_resumo": r["objeto_resumo"] or "",
                    "valor_total_estimado": float(r["valor_total_estimado"] or 0.0),
                    "score_aderencia": float(r["score_aderencia"] or 0.0),
                    "recomendacao_executiva": r["recomendacao_executiva"] or "",
                    "itens": json.loads(r["itens_json"]) if r["itens_json"] else [],
                    "exigencias": json.loads(r["exigencias_json"]) if r["exigencias_json"] else [],
                    "armadilhas": json.loads(r["armadilhas_json"]) if r["armadilhas_json"] else [],
                    "status_processamento": r["status_processamento"],
                    "created_at": r["created_at"]
                })
            return result

    def obter_edital(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        """Obtém detalhes completos de um edital específico."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM editais WHERE numero_controle_pncp = ?", (numero_controle_pncp,))
            r = cursor.fetchone()
            if not r:
                # Tentar recuperar via bid_projects caso tenha vindo de sincronização de outro computador
                cursor.execute("SELECT * FROM bid_projects WHERE pncp_id = ?", (numero_controle_pncp,))
                bp = cursor.fetchone()
                if not bp or not bp["audit_json"]:
                    return None
                try:
                    audit_data = json.loads(bp["audit_json"])
                    return {
                        "numero_controle_pncp": bp["pncp_id"],
                        "orgao": audit_data.get("orgao", "Órgão Público"),
                        "modalidade": audit_data.get("modalidade", "Pregão Eletrônico"),
                        "data_disputa": audit_data.get("data_disputa", "A definir"),
                        "portal_disputa": audit_data.get("portal_disputa", "PNCP"),
                        "objeto_resumo": audit_data.get("objeto_resumo", ""),
                        "valor_total_estimado": float(audit_data.get("valor_total_estimado") or 0.0),
                        "score_aderencia": float(audit_data.get("score_aderencia") or 0.0),
                        "recomendacao_executiva": audit_data.get("recomendacao_executiva", ""),
                        "itens": audit_data.get("itens_interesse_cliente") or [],
                        "exigencias": audit_data.get("exigencias_habilitacao_criticas") or [],
                        "armadilhas": audit_data.get("armadilhas_e_riscos") or [],
                        "status_processamento": bp["workflow_status"],
                        "created_at": bp["created_at"]
                    }
                except Exception:
                    return None

            return {
                "numero_controle_pncp": r["numero_controle_pncp"],
                "orgao": r["orgao"],
                "modalidade": r["modalidade"],
                "data_disputa": r["data_disputa"],
                "portal_disputa": r["portal_disputa"],
                "objeto_resumo": r["objeto_resumo"],
                "valor_total_estimado": float(r["valor_total_estimado"] or 0.0),
                "score_aderencia": float(r["score_aderencia"] or 0.0),
                "recomendacao_executiva": r["recomendacao_executiva"],
                "itens": json.loads(r["itens_json"]) if r["itens_json"] else [],
                "exigencias": json.loads(r["exigencias_json"]) if r["exigencias_json"] else [],
                "armadilhas": json.loads(r["armadilhas_json"]) if r["armadilhas_json"] else [],
                "status_processamento": r["status_processamento"],
                "created_at": r["created_at"]
            }

    def obter_estatisticas(self) -> Dict[str, Any]:
        """Calcula métricas agregadas para os cards e gráficos do Dashboard."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT 
                COUNT(*) as total_editais,
                COALESCE(SUM(valor_total_estimado), 0.0) as volume_total,
                COALESCE(AVG(score_aderencia), 0.0) as media_score,
                SUM(CASE WHEN score_aderencia >= 7.5 THEN 1 ELSE 0 END) as alta_aderencia
            FROM editais
            """)
            row = cursor.fetchone()
            total = row["total_editais"] or 0
            volume = float(row["volume_total"] or 0.0)
            media = round(float(row["media_score"] or 0.0), 1)
            alta = row["alta_aderencia"] or 0

            return {
                "total_editais": total,
                "volume_total": volume,
                "media_score": media,
                "alta_aderencia": alta,
                "taxa_aproveitamento": round((alta / total * 100) if total > 0 else 0, 1)
            }
