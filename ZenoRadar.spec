# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('E:/zeno lic/src/desktop/ui/index.html', 'src/desktop/ui')]
binaries = []
hiddenimports = ['pystray._win32', 'PIL._imaging', 'apscheduler.triggers.interval', 'apscheduler.schedulers.background', 'src.utils.paths', 'src.licensing.validator', 'src.licensing.hwid', 'src.desktop.startup_manager', 'src.desktop.tray_app', 'src.desktop.api', 'src.mcp_server', 'src.mcp_server.zeno_mcp', 'src.intelligence.chat_assistant', 'src.intelligence.react_auditor', 'src.intelligence.pageindex_tree', 'src.parsers.pdf_router', 'src.parsers.paddle_ocr_worker', 'src.collectors.pncp_api', 'src.collectors.scrapling_fetcher', 'src.notifier.whatsapp_sender', 'src.database.repository', 'src.database.models', 'webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium']
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('apscheduler')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic_settings')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('mcp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['E:/zeno lic/src/desktop/tray_app.py'],
    pathex=['E:/zeno lic'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ZenoRadar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['E:/zeno lic/assets/zeno_radar.ico'],
)
