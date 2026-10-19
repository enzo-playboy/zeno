import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
import httpx
from pydantic import BaseModel, Field

from src.licensing.hwid import get_hwid
from src.config import settings, BASE_DIR

logger = logging.getLogger(__name__)

class ClientLicenseConfig(BaseModel):
    """
    Configuração e permissões concedidas pela licença do cliente.
    """
    license_key: str
    client_name: str = "Cliente Zeno Radar"
    cnpj: Optional[str] = None
    uf_target: str = "MT"
    keywords: List[str] = Field(default_factory=lambda: ["informatica", "notebook", "servidor", "ti"])
    whatsapp_number: Optional[str] = None
    whatsapp_webhook_url: Optional[str] = None
    is_active: bool = True
    expires_at: Optional[datetime] = None
    hwid_bound: Optional[str] = None

class LicenseValidator:
    """
    Validador de licença com trava de hardware (HWID Lock) e integração ao Supabase.
    """

    def __init__(self, key_path: Optional[Path] = None):
        if key_path:
            self.key_path = key_path
        else:
            from src.utils.paths import get_license_path
            self.key_path = get_license_path()

        self.current_hwid = get_hwid()

    def get_or_prompt_license_key(self, interactive: bool = True) -> Optional[str]:
        """
        Lê a chave salva em 'license.key'. Se não existir e for interativo,
        exibe uma caixa de diálogo gráfica para o usuário inserir.
        """
        if self.key_path.exists():
            try:
                key = self.key_path.read_text(encoding="utf-8").strip()
                if key:
                    return key
            except Exception as e:
                logger.error(f"Erro ao ler arquivo de licença: {e}")

        # Se não encontrou no arquivo, verificar variável de ambiente
        env_key = os.getenv("ZENO_LICENSE_KEY")
        if env_key:
            self.salvar_chave_local(env_key.strip())
            return env_key.strip()

        if not interactive:
            return None

        # Exibir caixa de diálogo simples do Tkinter
        return self._prompt_gui_dialog()

    def _prompt_gui_dialog(self) -> Optional[str]:
        """Solicita a chave graficamente sem poluir o console."""
        try:
            import tkinter as tk
            from tkinter import simpledialog, messagebox

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            key = simpledialog.askstring(
                title="Ativação do Zeno Radar",
                prompt="Insira a sua Chave de Licença de Ativação:",
                parent=root
            )
            root.destroy()

            if key and key.strip():
                clean_key = key.strip()
                self.salvar_chave_local(clean_key)
                return clean_key
            return None
        except Exception as e:
            logger.warning(f"Interface gráfica Tkinter indisponível para prompt de licença: {e}")
            return None

    def salvar_chave_local(self, key: str):
        """Salva a chave no arquivo local."""
        try:
            self.key_path.write_text(key.strip(), encoding="utf-8")
            logger.info(f"Chave de licença gravada em: {self.key_path}")
        except Exception as e:
            logger.error(f"Falha ao salvar licença em {self.key_path}: {e}")

    def validar_licenca(self, license_key: Optional[str] = None, interactive_ui: bool = True) -> ClientLicenseConfig:
        """
        Valida a licença no Supabase com trava de HWID.
        Em ambiente de teste/dev sem Supabase configurado, permite fallback seguro.
        """
        key = license_key or self.get_or_prompt_license_key(interactive=interactive_ui)
        if not key:
            self._notificar_erro("Licença Ausente", "Nenhuma chave de licença foi informada para o Zeno Radar.", interactive_ui)
            raise PermissionError("Chave de licença não informada.")

        # Modo Dev / Fallback Local (quando Supabase não estiver conectado)
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY or key.startswith("ZENO-DEV-"):
            logger.warning("Supabase não configurado ou chave de desenvolvimento detectada. Ativando modo local...")
            return ClientLicenseConfig(
                license_key=key,
                client_name="Cliente Local / Desenvolvimento",
                uf_target=settings.TARGET_UF,
                keywords=settings.target_keywords_list,
                whatsapp_number=settings.WHATSAPP_RECIPIENT_PHONE,
                is_active=True,
                hwid_bound=self.current_hwid
            )

        # Consulta REST à tabela licenses no Supabase
        url = f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1/licenses"
        headers = {
            "apikey": settings.SUPABASE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        params = {
            "license_key": f"eq.{key}",
            "select": "*"
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.get(url, headers=headers, params=params)
                if res.status_code != 200:
                    msg = f"Falha na comunicação com o servidor de licenciamento (Status: {res.status_code})."
                    self._notificar_erro("Erro de Comunicação", msg, interactive_ui)
                    raise ConnectionError(msg)

                records = res.json()
                if not records:
                    self._notificar_erro("Licença Inválida", "A chave informada não existe ou foi revogada.", interactive_ui)
                    raise PermissionError("Chave de licença não encontrada no banco de dados.")

                rec = records[0]

                # 1. Verificar se está ativa
                if not rec.get("is_active", True):
                    self._notificar_erro("Licença Inativa", "Esta licença foi suspensa ou desativada.", interactive_ui)
                    raise PermissionError("Licença inativa.")

                # 2. Verificar expiração
                exp_str = rec.get("expires_at")
                if exp_str:
                    try:
                        exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                        if exp_dt < datetime.now(timezone.utc):
                            self._notificar_erro("Licença Expirada", "Sua assinatura do Zeno Radar expirou. Contate o suporte.", interactive_ui)
                            raise PermissionError("Licença expirada.")
                    except ValueError:
                        pass

                # 3. Trava de Hardware (HWID Lock)
                db_hwid = rec.get("hwid")
                if not db_hwid:
                    # Primeiro uso: Vincula HWID da máquina atual
                    logger.info(f"Primeiro check-in: Vinculando HWID {self.current_hwid} à licença {key}...")
                    patch_res = client.patch(
                        f"{url}?id=eq.{rec['id']}",
                        headers=headers,
                        json={"hwid": self.current_hwid, "last_check_in": datetime.now(timezone.utc).isoformat()}
                    )
                    if patch_res.status_code not in (200, 204):
                        logger.warning(f"Aviso ao registrar HWID: {patch_res.text}")
                elif db_hwid != self.current_hwid:
                    # Conflito de HWID!
                    msg_bloqueio = "Esta licença já está vinculada a outro computador físico (Trava de HWID)."
                    self._notificar_erro("Licença Bloqueada", msg_bloqueio, interactive_ui)
                    raise PermissionError(msg_bloqueio)
                else:
                    # HWID coincide perfeitamente: Atualiza último check-in
                    client.patch(
                        f"{url}?id=eq.{rec['id']}",
                        headers=headers,
                        json={"last_check_in": datetime.now(timezone.utc).isoformat()}
                    )

                keywords_data = rec.get("keywords") or settings.target_keywords_list
                if isinstance(keywords_data, str):
                    keywords_data = [k.strip() for k in keywords_data.split(",") if k.strip()]

                return ClientLicenseConfig(
                    license_key=key,
                    client_name=rec.get("client_name", "Cliente"),
                    cnpj=rec.get("cnpj"),
                    uf_target=rec.get("uf_target") or settings.TARGET_UF,
                    keywords=keywords_data,
                    whatsapp_number=rec.get("whatsapp_number") or settings.WHATSAPP_RECIPIENT_PHONE,
                    whatsapp_webhook_url=rec.get("whatsapp_webhook_url"),
                    is_active=rec.get("is_active", True),
                    expires_at=datetime.fromisoformat(exp_str.replace("Z", "+00:00")) if exp_str else None,
                    hwid_bound=self.current_hwid
                )

        except (PermissionError, ConnectionError):
            raise
        except Exception as e:
            logger.error(f"Erro inesperado durante validação de licença: {e}")
            raise

    def _notificar_erro(self, titulo: str, mensagem: str, interactive: bool):
        logger.error(f"[{titulo}] {mensagem}")
        if interactive:
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                messagebox.showerror(titulo, mensagem, parent=root)
                root.destroy()
            except Exception:
                pass
