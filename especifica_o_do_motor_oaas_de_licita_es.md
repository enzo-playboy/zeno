# Plano de Execução para o Agente (Antigravity) — Fase 2: Empacotamento Desktop, Licenciamento HWID e Proxy de IA

## 1. Contexto Atual do Projeto (`e:\zeno lic\`)
O núcleo do motor OaaS de Licitações (**Fase 1**) já está **100% construído, testado e operacional** na máquina local:
- `src/collectors/pncp_api.py` e `scrapling_fetcher.py`: Coletando editais e anexos reais do PNCP em Mato Grosso (`uf=MT`).
- `src/parsers/pdf_router.py` e `paddle_ocr_worker.py`: Extraindo texto nativo via PyMuPDF e OCR seletivo via PaddleOCR.
- `src/intelligence/pageindex_tree.py` e `react_auditor.py`: Indexando em árvore (Vectorless RAG) e gerando auditoria com citações `[Pág. X]`.
- `src/notifier/whatsapp_sender.py` e `src/database/repository.py`: Salvando com idempotência no SQLite e formatando o relatório OaaS.

---

## 2. Objetivo Desta Tarefa (O que o Antigravity deve construir agora)
Transformar o motor atual em um **Aplicativo Desktop Windows Instalável (`.exe`)** que roda silenciosamente em segundo plano (System Tray) na máquina do cliente final (Custo Zero de Servidor para nós), protegido por:
1. **Sistema de Licenciamento Remoto + Trava de Hardware (HWID Lock)** via Supabase.
2. **Proxy Seguro de IA (Supabase Edge Function)** para que a chave da LLM (`OPENAI_API_KEY` / `GEMINI_API_KEY`) **nunca** fique exposta dentro do `.exe` do cliente.
3. **Agente de Bandeja do Windows (`pystray` + `APScheduler`)** com inicialização automática no boot do Windows (`winreg`) e notificações nativas.
4. **Script de Build (`build_exe.py` / `PyInstaller`)** para gerar o executável único.

---

## 3. Estrutura de Arquivos a Adicionar / Atualizar em `e:\zeno lic\`

```text
e:\zeno lic\
├── supabase/
│   ├── schema_licenses.sql          # [NOVO] SQL para criar a tabela de licenças e telemetria no Supabase
│   └── functions/
│       └── ai-proxy/
│           └── index.ts             # [NOVO] Edge Function que valida a licença e chama a LLM sem expor a API Key no cliente
├── src/
│   ├── licensing/
│   │   ├── __init__.py              # [NOVO]
│   │   ├── hwid.py                  # [NOVO] Coleta o UUID físico da placa-mãe do Windows + hash SHA-256
│   │   └── validator.py             # [NOVO] Valida chave no Supabase, faz bind do HWID e puxa filtros (UF, palavras-chave)
│   ├── intelligence/
│   │   └── react_auditor.py         # [ATUALIZAR] Trocar chamada direta de API Key local por chamada via Proxy Seguro (com fallback local em dev)
│   ├── desktop/
│   │   ├── __init__.py              # [NOVO]
│   │   ├── startup_manager.py       # [NOVO] Registro automático no Windows Startup (HKEY_CURRENT_USER\...\Run)
│   │   └── tray_app.py              # [NOVO] Ícone na bandeja do sistema, menu interativo e scheduler em background
│   └── main.py                      # [ATUALIZAR] Expor função limpa `run_cycle(config_cliente)` para ser chamada pelo Tray App
└── build_installer.py               # [NOVO] Script automatizado do PyInstaller para gerar o ZenoRadar.exe
```

---

## 4. Especificação Detalhada de Cada Arquivo (Instruções para o Antigravity)

### Passo 1: Criar o Schema SQL do Supabase (`supabase/schema_licenses.sql`)
Criar o arquivo com a estrutura da tabela `licenses`:
```sql
create table if not exists public.licenses (
  id uuid default gen_random_uuid() primary key,
  license_key text unique not null,
  client_name text not null,
  cnpj text,
  hwid text, -- Vinculado automaticamente no primeiro uso do cliente
  is_active boolean default true,
  expires_at timestamp with time zone not null,
  uf_target text default 'MT',
  keywords text[] default '{}',
  whatsapp_number text,
  whatsapp_webhook_url text,
  last_check_in timestamp with time zone,
  created_at timestamp with time zone default now()
);

-- Índice rápido por chave de licença
create index if not exists idx_licenses_key on public.licenses(license_key);
```

### Passo 2: Implementar Coleta de HWID e Validador (`src/licensing/hwid.py` e `src/licensing/validator.py`)
1. Em `src/licensing/hwid.py`:
   - Obter o identificador único do hardware no Windows executando `wmic csproduct get uuid` (ou `powershell -Command "(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID"` como fallback moderno no Windows 11).
   - Retornar o hash `SHA-256` limpo dessa string.
2. Em `src/licensing/validator.py`:
   - Ler `license.key` na pasta raiz do executável (se não existir, abrir um prompt simples `tkinter.simpledialog.askstring` pedindo para o usuário colar a chave de ativação).
   - Consultar a tabela `licenses` no Supabase via `httpx`.
   - Verificar:
     - Se `is_active == True`.
     - Se `expires_at > datetime.now(timezone.utc)`.
     - Se `hwid` no banco estiver vazio (`None`), fazer `PATCH` gravando o HWID da máquina atual.
     - Se `hwid` no banco for diferente do HWID atual, bloquear a execução e exibir alerta de erro (`messagebox.showerror`: *"Licença já vinculada a outro computador"*).
   - Atualizar `last_check_in` e retornar um objeto Pydantic `ClientLicenseConfig` contendo `uf_target`, `keywords`, `whatsapp_number` e `license_key`.

### Passo 3: Criar a Supabase Edge Function Proxy de IA (`supabase/functions/ai-proxy/index.ts`)
Para impedir engenharia reversa da nossa chave de LLM no PC do cliente:
- Criar a Edge Function em TypeScript (Deno) que recebe no header `x-license-key` e `x-hwid`.
- A função verifica no Supabase se a licença está ativa e se o HWID bate.
- Se válido, repassa o prompt/árvore do `PageIndex` para a API da OpenAI/Gemini usando a chave secreta armazenada apenas nos *Secrets* do Supabase, devolvendo o JSON auditado para o cliente.
- Atualizar `src/intelligence/react_auditor.py` para suportar `USE_CLOUD_PROXY=True` (produção no cliente) ou `USE_CLOUD_PROXY=False` (desenvolvimento local com `.env`).

### Passo 4: Construir o Aplicativo de Bandeja do Windows (`src/desktop/startup_manager.py` e `src/desktop/tray_app.py`)
1. Em `src/desktop/startup_manager.py`:
   - Função `register_startup(app_name="ZenoRadarOaaS")` que adiciona o caminho do `sys.executable` na chave de registro `Software\Microsoft\Windows\CurrentVersion\Run` quando estiver empacotado (`getattr(sys, 'frozen', False)`).
2. Em `src/desktop/tray_app.py`:
   - Usar `pystray` + `Pillow` para desenhar um ícone moderno verde na bandeja do Windows (perto do relógio).
   - Usar `APScheduler` (`BackgroundScheduler`) para rodar `run_pipeline()` automaticamente a cada **2 horas** usando a UF e as palavras-chave vindas do Supabase daquele cliente.
   - Menu do clique direito no ícone da bandeja:
     - `Status: Ativo ({client_name})` (desabilitado, apenas informativo)
     - `UF Monitorada: {uf_target}` (desabilitado)
     - `Executar Varredura Agora` (dispara thread imediata)
     - `Abrir Pasta de Relatórios / Logs` (abre o diretório local)
     - `Sair` (encerra o scheduler e o ícone)

### Passo 5: Script de Empacotamento (`build_installer.py`)
- Instalar dependências adicionais no `.venv`: `pip install pystray pillow apscheduler pyinstaller`.
- Criar script `build_installer.py` que executa o PyInstaller gerando o executável sem janela de console (`--noconsole --onefile --name ZenoRadar`), incluindo os dados necessários.

---

## 5. Critérios de Aceite (Teste que o Antigravity deve rodar)
1. Todos os novos módulos criados e tipados sem quebrar os testes existentes em `tests/test_engine.py`.
2. O comando `python src/desktop/tray_app.py` em modo de desenvolvimento deve iniciar o ícone na bandeja do Windows, validar a licença (com modo mock/dev se o Supabase ainda não estiver preenchido no `.env`) e disparar um ciclo de auditoria em background thread sem travar a interface.
3. O script `build_installer.py` deve estar pronto para gerar o binário `dist/ZenoRadar.exe`.