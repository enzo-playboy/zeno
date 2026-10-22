import json
import logging
import httpx
from typing import Optional, List, Dict, Any
from src.config import settings

logger = logging.getLogger("SupabaseCloudClient")


class SupabaseClient:
    """
    Cliente REST oficial para o Supabase (PostgREST API).
    Garante que todos os dados de licenças, organizações, usuários, editais e projetos
    sejam obtidos e persistidos primariamente na nuvem Supabase.
    """
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None, timeout: float = 15.0):
        self.url = (url or settings.SUPABASE_URL or "").rstrip("/")
        self.key = key or settings.SUPABASE_KEY or ""
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.key)

    def _get_headers(self, return_rep: bool = False, upsert: bool = False) -> Dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        prefer = []
        if return_rep:
            prefer.append("return=representation")
        if upsert:
            prefer.append("resolution=merge-duplicates")
        if prefer:
            headers["Prefer"] = ", ".join(prefer)
        return headers

    # =========================================================================
    # Operações em Organizations
    # =========================================================================

    def obter_organizacao(self, org_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            return None
        try:
            url = f"{self.url}/rest/v1/organizations?id=eq.{org_id}&select=*"
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return data[0] if data else None
        except Exception as e:
            logger.warning(f"[Supabase] Falha ao buscar organização '{org_id}': {e}")
        return None

    def upsert_organizacao(self, dados: Dict[str, Any]) -> bool:
        if not self.is_configured:
            return False
        try:
            url = f"{self.url}/rest/v1/organizations"
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=self._get_headers(upsert=True), json=dados)
                return resp.status_code in (200, 201, 204)
        except Exception as e:
            logger.error(f"[Supabase] Erro ao salvar organização: {e}")
            return False

    # =========================================================================
    # Operações em User Seats
    # =========================================================================

    def listar_seats(self, org_id: str) -> Optional[List[Dict[str, Any]]]:
        if not self.is_configured:
            return None
        try:
            url = f"{self.url}/rest/v1/user_seats?org_id=eq.{org_id}&select=*&order=created_at.asc"
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning(f"[Supabase] Falha ao listar seats da organização '{org_id}': {e}")
        return None

    def upsert_seat(self, dados: Dict[str, Any]) -> bool:
        if not self.is_configured:
            return False
        try:
            url = f"{self.url}/rest/v1/user_seats"
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=self._get_headers(upsert=True), json=dados)
                return resp.status_code in (200, 201, 204)
        except Exception as e:
            logger.error(f"[Supabase] Erro ao upsert seat: {e}")
            return False

    # =========================================================================
    # Operações em Bid Projects
    # =========================================================================

    def listar_projetos(self, org_id: str, workflow_status: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        if not self.is_configured:
            return None
        try:
            url = f"{self.url}/rest/v1/bid_projects?org_id=eq.{org_id}&select=*&order=updated_at.desc"
            if workflow_status:
                url += f"&workflow_status=eq.{workflow_status}"
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning(f"[Supabase] Falha ao listar bid_projects: {e}")
        return None

    def obter_projeto(self, pncp_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            return None
        try:
            url = f"{self.url}/rest/v1/bid_projects?pncp_id=eq.{pncp_id}&org_id=eq.{org_id}&select=*"
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return data[0] if data else None
        except Exception as e:
            logger.warning(f"[Supabase] Falha ao obter projeto {pncp_id}: {e}")
        return None

    def upsert_projeto(self, dados: Dict[str, Any]) -> bool:
        if not self.is_configured:
            return False
        try:
            url = f"{self.url}/rest/v1/bid_projects?on_conflict=pncp_id,org_id"
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, headers=self._get_headers(upsert=True), json=dados)
                return resp.status_code in (200, 201, 204)
        except Exception as e:
            logger.error(f"[Supabase] Erro ao salvar projeto no Supabase: {e}")
            return False

    # =========================================================================
    # Operações em Editais
    # =========================================================================

    def obter_edital(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            return None
        try:
            url = f"{self.url}/rest/v1/editais?numero_controle_pncp=eq.{numero_controle_pncp}&select=*"
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return data[0] if data else None
        except Exception as e:
            logger.warning(f"[Supabase] Falha ao buscar edital {numero_controle_pncp}: {e}")
        return None

    def upsert_edital(self, dados: Dict[str, Any]) -> bool:
        if not self.is_configured:
            return False
        try:
            url = f"{self.url}/rest/v1/editais?on_conflict=numero_controle_pncp"
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, headers=self._get_headers(upsert=True), json=dados)
                return resp.status_code in (200, 201, 204)
        except Exception as e:
            logger.error(f"[Supabase] Erro ao salvar edital no Supabase: {e}")
            return False

    def listar_editais(self, limit: int = 100, busca: str = "") -> Optional[List[Dict[str, Any]]]:
        if not self.is_configured:
            return None
        try:
            url = f"{self.url}/rest/v1/editais?select=*&order=created_at.desc&limit={limit}"
            if busca:
                b = busca.strip()
                url += f"&or=(orgao.ilike.*{b}*,objeto_resumo.ilike.*{b}*,numero_controle_pncp.ilike.*{b}*)"
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning(f"[Supabase] Falha ao listar editais: {e}")
        return None
