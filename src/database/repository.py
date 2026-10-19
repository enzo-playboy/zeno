import sqlite3
import hashlib
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from src.database.models import EditalAuditado, Cliente, AlertaEnviado
from src.config import settings

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
            conn.commit()

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
            return row is not None

    def salvar_edital(self, edital: EditalAuditado) -> int:
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
            return cursor.lastrowid

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
