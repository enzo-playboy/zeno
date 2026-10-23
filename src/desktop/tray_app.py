import os
import sys
import threading
import logging
import time
from pathlib import Path
from typing import Optional

# Garantir que a raiz do projeto esteja no sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Configuração de Logs para arquivo na pasta do app
log_dir = BASE_DIR / "logs"
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "zeno_radar.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("ZenoRadarTray")

from PIL import Image, ImageDraw
import pystray
from apscheduler.schedulers.background import BackgroundScheduler

from src.licensing.validator import LicenseValidator, ClientLicenseConfig
from src.desktop.startup_manager import register_startup
from src.desktop.api import DesktopAPI
from src.main import run_cycle
from src.config import settings
from src.utils.paths import get_ui_entrypoint


# Tenta carregar pywebview para a interface nativa
HAS_WEBVIEW = False
try:
    import webview
    HAS_WEBVIEW = True
except Exception as e:
    logger.warning(f"pywebview não disponível ({e}). Ativando modo silencioso de bandeja.")
    HAS_WEBVIEW = False

class ZenoRadarTrayApp:
    """
    Aplicativo de Bandeja do Windows com Dashboard Visual Nativo e Chatbot IA.
    Executa varreduras periódicas em background com APScheduler.
    """

    def __init__(self):
        self.validator = LicenseValidator()
        self.license_config: Optional[ClientLicenseConfig] = None
        self.scheduler: Optional[BackgroundScheduler] = None
        self.icon: Optional[pystray.Icon] = None
        self.window = None
        self.api = None
        self.is_running_scan = False

    def create_tray_icon_image(self, width: int = 64, height: int = 64) -> Image.Image:
        """
        Gera dinamicamente um ícone vetorial moderno verde estilo radar tático.
        """
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        # Fundo circular escuro moderno
        draw.ellipse([2, 2, width - 2, height - 2], fill=(15, 23, 42, 255), outline=(16, 185, 129, 255), width=2)
        
        # Círculos concêntricos do radar
        center_x, center_y = width // 2, height // 2
        draw.ellipse([center_x - 18, center_y - 18, center_x + 18, center_y + 18], outline=(5, 150, 105, 180), width=1)
        draw.ellipse([center_x - 10, center_y - 10, center_x + 10, center_y + 10], outline=(16, 185, 129, 200), width=1)

        # Retículo central em cruz
        draw.line([center_x, 6, center_x, height - 6], fill=(16, 185, 129, 120), width=1)
        draw.line([6, center_y, width - 6, center_y], fill=(16, 185, 129, 120), width=1)

        # Ponto luminoso de detecção
        draw.ellipse([center_x - 3, center_y - 3, center_x + 3, center_y + 3], fill=(52, 211, 153, 255))
        draw.ellipse([center_x + 8, center_y - 8, center_x + 12, center_y - 4], fill=(52, 211, 153, 255))

        return image

    def start(self, headless: bool = False):
        """Inicializa a validação de licença, o scheduler, a UI e o ícone de bandeja."""
        logger.info("Iniciando Zeno Radar Desktop Agent & Dashboard...")

        # 1. Registrar no Windows Startup se for executável congelado
        register_startup(app_name="ZenoRadarOaaS")

        # 2. Validar licença de uso
        try:
            self.license_config = self.validator.validar_licenca(interactive_ui=True)
            logger.info(f"Licença ativada para: {self.license_config.client_name} (UF: {self.license_config.uf_target})")
        except Exception as e:
            logger.error(f"Não foi possível ativar a licença: {e}")
            return

        self.api = DesktopAPI(license_config=self.license_config)

        # 3. Inicializar Agendador APScheduler (intervalo de 2 horas)
        self.scheduler = BackgroundScheduler(daemon=True)
        self.scheduler.add_job(
            func=self._executar_varredura_job,
            trigger="interval",
            hours=2,
            id="zeno_scan_job",
            replace_existing=True
        )
        self.scheduler.start()
        logger.info("Agendador em segundo plano ativado (Ciclo: a cada 2 horas).")

        # 4. Criar menu de contexto do Tray
        menu = pystray.Menu(
            pystray.MenuItem(
                f"Status: Ativo ({self.license_config.client_name[:20]})",
                lambda: None,
                enabled=False
            ),
            pystray.MenuItem(
                f"UF Monitorada: {self.license_config.uf_target}",
                lambda: None,
                enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "⚡ Abrir Painel / Dashboard",
                self.on_abrir_dashboard_click,
                default=True
            ),
            pystray.MenuItem(
                "⚡ Executar Varredura Agora",
                self.on_executar_agora_click
            ),
            pystray.MenuItem(
                "📁 Abrir Pasta de Relatórios",
                self.on_abrir_pasta_click
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "❌ Sair",
                self.on_sair_click
            )
        )

        # 5. Criar ícone da bandeja
        icon_img = self.create_tray_icon_image()
        self.icon = pystray.Icon(
            name="ZenoRadar",
            icon=icon_img,
            title=f"Zeno Radar — Monitoramento OaaS ({self.license_config.uf_target})",
            menu=menu
        )

        # Dispara thread inicial de varredura leve para aquecer o banco
        threading.Thread(target=self._executar_varredura_job, daemon=True).start()

        # 6. Decidir modo de execução (com UI nativa ou apenas bandeja)
        ui_html_path = get_ui_entrypoint()
        target_url = str(ui_html_path)

        # Detecta se o Vite Dev Server está ativo na porta 5173
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                if s.connect_ex(("127.0.0.1", 5173)) == 0:
                    target_url = "http://localhost:5173"
                    logger.info("⚡ Vite Dev Server ativo detectado na porta 5173! Carregando com Hot-Reload.")
        except Exception:
            pass

        logger.info(f"Interface gráfica resolvida em: {target_url}")

        if HAS_WEBVIEW and not headless and (ui_html_path.exists() or target_url.startswith("http")):
            # Inicia o ícone do tray em background thread
            tray_thread = threading.Thread(target=self.icon.run, daemon=True)
            tray_thread.start()

            # Cria a janela desktop nativa
            self.window = webview.create_window(
                title=f"Zeno Radar — Painel de Licitações ({self.license_config.uf_target})",
                url=target_url,
                js_api=self.api,
                width=1280,
                height=820,
                min_size=(1040, 700),
                background_color="#F4F6F9"
            )

            # Notificação amigável
            if self.icon:
                try:
                    self.icon.notify(
                        title="Zeno Radar Ativado",
                        message=f"Painel aberto e monitoramento ativo para {self.license_config.client_name}."
                    )
                except Exception:
                    pass

            # Loop principal da janela de interface gráfica
            webview.start(debug=False)
        else:
            # Modo apenas Tray
            self.icon.run()

    def on_abrir_dashboard_click(self, icon=None, item=None):
        """Reabre ou traz a janela do painel para o primeiro plano."""
        if self.window:
            try:
                self.window.show()
                self.window.restore()
                return
            except Exception:
                pass

        ui_html_path = get_ui_entrypoint()

        if HAS_WEBVIEW and ui_html_path.exists():
            try:
                self.window = webview.create_window(
                    title=f"Zeno Radar — Painel de Licitações ({self.license_config.uf_target})",
                    url=str(ui_html_path),
                    js_api=self.api,
                    width=1280,
                    height=820,
                    min_size=(1040, 700)
                )
            except Exception as e:
                logger.error(f"Erro ao reabrir janela: {e}")
        else:
            # Fallback navegador
            import webbrowser
            webbrowser.open(str(ui_html_path))

    def on_executar_agora_click(self, icon=None, item=None):
        """Handler do menu: dispara varredura imediata em background thread."""
        if self.is_running_scan:
            if self.icon:
                self.icon.notify("Zeno Radar", "Já existe uma varredura em andamento no momento.")
            return

        threading.Thread(target=self._executar_varredura_job, daemon=True).start()

    def _executar_varredura_job(self):
        """Rotina assíncrona de varredura executada em thread."""
        if self.is_running_scan:
            return

        self.is_running_scan = True
        logger.info("Iniciando ciclo de varredura em segundo plano...")
        if self.icon:
            try:
                self.icon.notify("Zeno Radar", f"Iniciando varredura de editais no PNCP ({self.license_config.uf_target})...")
            except Exception:
                pass

        try:
            total_auditados = run_cycle(config_cliente=self.license_config, max_editais=5)
            logger.info(f"Ciclo de varredura finalizado. {total_auditados} editais novos auditados.")
            if self.icon:
                try:
                    if total_auditados > 0:
                        self.icon.notify(
                            "Oportunidades Encontradas!",
                            f"{total_auditados} novos editais em conformidade foram auditados e salvos."
                        )
                    else:
                        self.icon.notify("Zeno Radar", "Varredura concluída. Sistema em dia!")
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Erro durante ciclo de varredura: {e}")
            if self.icon:
                try:
                    self.icon.notify("Aviso Zeno Radar", f"Erro no ciclo: {str(e)[:80]}")
                except Exception:
                    pass
        finally:
            self.is_running_scan = False

    def on_abrir_pasta_click(self, icon=None, item=None):
        """Abre o diretório de downloads/relatórios no Windows Explorer."""
        pasta = Path(settings.DOWNLOAD_DIR)
        os.makedirs(pasta, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(pasta))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(pasta)])
        except Exception as e:
            logger.error(f"Erro ao abrir pasta: {e}")

    def on_sair_click(self, icon=None, item=None):
        """Encerra com segurança o agendador e o ícone de bandeja."""
        logger.info("Encerrando Zeno Radar Desktop Agent...")
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass
        if self.icon:
            self.icon.stop()
        sys.exit(0)

def main():
    app = ZenoRadarTrayApp()
    app.start()

if __name__ == "__main__":
    main()
