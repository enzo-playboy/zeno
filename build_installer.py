import os
import sys
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent

def generate_app_icon(icon_path: Path):
    """
    Gera um ícone .ico profissional em múltiplos tamanhos com tema verde radar.
    """
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    images = []

    for width, height in sizes:
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Fundo circular escuro moderno
        draw.ellipse([2, 2, width - 2, height - 2], fill=(15, 23, 42, 255), outline=(16, 185, 129, 255), width=max(1, width // 24))
        
        # Círculos do radar
        cx, cy = width // 2, height // 2
        r1 = int(width * 0.3)
        r2 = int(width * 0.16)
        draw.ellipse([cx - r1, cy - r1, cx + r1, cy + r1], outline=(5, 150, 105, 180), width=1)
        draw.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], outline=(16, 185, 129, 200), width=1)

        # Retículo em cruz
        draw.line([cx, 4, cx, height - 4], fill=(16, 185, 129, 140), width=1)
        draw.line([4, cy, width - 4, cy], fill=(16, 185, 129, 140), width=1)

        # Pontos luminosos
        draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(52, 211, 153, 255))
        blip_x = int(cx + width * 0.15)
        blip_y = int(cy - height * 0.15)
        draw.ellipse([blip_x - 2, blip_y - 2, blip_x + 2, blip_y + 2], fill=(52, 211, 153, 255))

        images.append(img)

    images[-1].save(
        icon_path,
        format="ICO",
        sizes=sizes
    )
    print(f"✔ Ícone gerado com sucesso em: {icon_path}")

def build_executable():
    """
    Executa o PyInstaller para empacotar o Zeno Radar em executável Windows único.
    """
    print("\n" + "=" * 60)
    print("🚀 INICIANDO BUILD DO ZENO RADAR (.EXE) — FASE 2")
    print("=" * 60 + "\n")

    entrypoint = BASE_DIR / "src" / "desktop" / "tray_app.py"
    if not entrypoint.exists():
        print(f"❌ Erro: Ponto de entrada não encontrado em {entrypoint}")
        sys.exit(1)

    assets_dir = BASE_DIR / "assets"
    icon_path = assets_dir / "zeno_radar.ico"
    generate_app_icon(icon_path)

    # Parâmetros do PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=ZenoRadar",
        "--onefile",
        "--noconsole",
        f"--icon={icon_path}",
        f"--paths={BASE_DIR}",
        "--hidden-import=pystray._win32",
        "--hidden-import=PIL._imaging",
        "--hidden-import=apscheduler.triggers.interval",
        "--hidden-import=apscheduler.schedulers.background",
        "--hidden-import=src.utils.paths",
        "--hidden-import=src.licensing.validator",
        "--hidden-import=src.licensing.hwid",
        "--hidden-import=src.desktop.startup_manager",
        "--hidden-import=src.desktop.tray_app",
        "--hidden-import=src.desktop.api",
        "--hidden-import=src.mcp_server",
        "--hidden-import=src.mcp_server.zeno_mcp",
        "--hidden-import=src.intelligence.chat_assistant",
        "--hidden-import=src.intelligence.react_auditor",
        "--hidden-import=src.intelligence.pageindex_tree",
        "--hidden-import=src.parsers.pdf_router",
        "--hidden-import=src.parsers.paddle_ocr_worker",
        "--hidden-import=src.collectors.pncp_api",
        "--hidden-import=src.collectors.scrapling_fetcher",
        "--hidden-import=src.notifier.whatsapp_sender",
        "--hidden-import=src.database.repository",
        "--hidden-import=src.database.models",
        "--hidden-import=webview",
        "--hidden-import=webview.platforms.winforms",
        "--hidden-import=webview.platforms.edgechromium",
        f"--add-data={BASE_DIR / 'src' / 'desktop' / 'ui' / 'index.html'};src/desktop/ui",
    ]

    # Compilar frontend Vite se existir
    frontend_dir = BASE_DIR / "frontend"
    if (frontend_dir / "package.json").exists():
        try:
            print("📦 Compilando interface gráfica via Vite...")
            subprocess.run(["npm.cmd" if sys.platform == "win32" else "npm", "run", "build"], cwd=str(frontend_dir), check=False)
        except Exception as e:
            print(f"ℹ Aviso: Falha ao compilar frontend automaticamente: {e}")

    # Incluir pasta de build compilada (dist) se existir
    ui_dist = BASE_DIR / "src" / "desktop" / "ui" / "dist"
    if ui_dist.exists() and (ui_dist / "index.html").exists():
        cmd.append(f"--add-data={ui_dist};src/desktop/ui/dist")
        print(f"✔ Empacotando build otimizado da interface: {ui_dist}")
    else:
        print("ℹ Build dist não detectado. Utilizando src/desktop/ui/index.html.")

    cmd.extend([
        "--collect-all=webview",
        "--collect-all=clr_loader",
        "--collect-all=pythonnet",
        "--collect-all=pystray",
        "--collect-all=apscheduler",
        "--collect-all=pydantic",
        "--collect-all=pydantic_settings",
        "--collect-all=mcp",
        "--clean",
        str(entrypoint)
    ])

    print("Executando PyInstaller...")
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=str(BASE_DIR))

    if result.returncode == 0:
        exe_path = BASE_DIR / "dist" / "ZenoRadar.exe"
        print("\n" + "=" * 60)
        print("🎉 BUILD CONCLUÍDO COM SUCESSO!")
        print(f"📦 Executável gerado: {exe_path}")
        print("=" * 60 + "\n")
    else:
        print(f"\n❌ Falha durante a compilação do PyInstaller (Código: {result.returncode})")
        sys.exit(result.returncode)

if __name__ == "__main__":
    build_executable()
