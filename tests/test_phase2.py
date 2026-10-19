import sys
import os
from pathlib import Path

# Garantir UTF-8 no Windows Console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.licensing.hwid import get_hwid, get_raw_hardware_id
from src.licensing.validator import LicenseValidator, ClientLicenseConfig
from src.desktop.startup_manager import is_startup_registered, register_startup, unregister_startup
from src.desktop.tray_app import ZenoRadarTrayApp
from src.main import run_cycle

def test_phase2_suite():
    print("\n--- [TESTE 1] Geração de HWID e Hash SHA-256 ---")
    raw_id = get_raw_hardware_id()
    hwid = get_hwid()
    print(f"Raw Hardware ID obtido: {raw_id}")
    print(f"Hash SHA-256 (64 hex): {hwid}")
    assert raw_id and len(raw_id) > 5, "Hardware ID bruto não pode ser vazio"
    assert len(hwid) == 64, "HWID SHA-256 deve ter exatamente 64 caracteres hexadecimais"
    print("✔ Teste de HWID aprovado!")

    print("\n--- [TESTE 2] Validador de Licença (Modo Dev / Fallback) ---")
    validator = LicenseValidator()
    cfg = validator.validar_licenca(license_key="ZENO-DEV-TEST-MT", interactive_ui=False)
    assert isinstance(cfg, ClientLicenseConfig), "Deve retornar uma instância de ClientLicenseConfig"
    assert cfg.license_key == "ZENO-DEV-TEST-MT"
    assert cfg.is_active is True
    assert cfg.hwid_bound == hwid
    print(f"Configuração do cliente carregada: {cfg.client_name} (UF: {cfg.uf_target})")
    print("✔ Teste de Validador de Licença aprovado!")

    print("\n--- [TESTE 3] Gerenciador de Inicialização Windows (Startup Manager) ---")
    reg_status = is_startup_registered("ZenoRadarOaaS")
    print(f"Status de inicialização no registro: {reg_status}")
    assert isinstance(reg_status, bool), "is_startup_registered deve retornar um booleano"
    print("✔ Teste de Startup Manager aprovado!")

    print("\n--- [TESTE 4] Geração de Ícone e Setup do System Tray ---")
    tray_app = ZenoRadarTrayApp()
    icon_image = tray_app.create_tray_icon_image(64, 64)
    assert icon_image.size == (64, 64), "Ícone do Tray deve ter dimensão 64x64"
    assert icon_image.mode == "RGBA", "Ícone deve ter canal alfa RGBA"
    print("✔ Teste de Ícone do Tray aprovado!")

    print("\n--- [TESTE 5] Assinatura e Compatibilidade de run_cycle ---")
    import inspect
    sig = inspect.signature(run_cycle)
    assert "config_cliente" in sig.parameters, "run_cycle deve receber config_cliente"
    print("✔ Teste de compatibilidade de run_cycle aprovado!")

    print("\n🎉 TODOS OS TESTES DA FASE 2 PASSARAM COM SUCESSO!\n")

if __name__ == "__main__":
    test_phase2_suite()
