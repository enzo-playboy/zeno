# 🎯 Zeno Radar — Motor OaaS de Licitações Públicas com IA & MCP

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastMCP](https://img.shields.io/badge/Model_Context_Protocol-FastMCP-6366f1?style=for-the-badge)](https://modelcontextprotocol.io)
[![AgentScope](https://img.shields.io/badge/Multi--Agent-AgentScope-00b4d8?style=for-the-badge)](https://agentscope.io)
[![PyMuPDF](https://img.shields.io/badge/Parser-PyMuPDF_&_PaddleOCR-emerald?style=for-the-badge)](https://pymupdf.readthedocs.io)
[![SQLite](https://img.shields.io/badge/Database-SQLite_Persistente-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Licença](https://img.shields.io/badge/Proteção-HWID_Lock_+_Supabase-green?style=for-the-badge)](https://supabase.com)

> **Zeno Radar** é uma solução **OaaS (Operation as a Service)** para monitoramento contínuo, download furtivo, indexação hierárquica e auditoria jurídica automatizada de editais de licitações públicas brasileiras ([PNCP / Lei 14.133/2021](https://pncp.gov.br)).
> 
> O motor roda localmente na máquina do cliente através de um executável Windows (`.exe` com System Tray) a **custo zero de servidor**, integrando um **Chatbot Jurídico IA ReAct** e uma interface desktop nativa conectada via **Model Context Protocol (MCP)**.

---

## ⚡ Principais Capacidades

- 🏛️ **Coleta Furtiva no PNCP:** Consulta à API oficial do Portal Nacional de Contratações Públicas com suporte ao `Scrapling` para bypass de proteções e extração automática de anexos comprimidos (`.zip`, `.rar`).
- 📄 **Roteador Híbrido de Documentos:** Extração instantânea de texto e tabelas nativas com `PyMuPDF` (<= 50ms) e chaveamento automático para `PaddleOCR` sob demanda para páginas escaneadas.
- 🌳 **Vectorless RAG (PageIndex Tree):** Indexação hierárquica por cláusulas e seções (Habilitação, Termo de Referência, Sanções, Prazos) preservando a integridade de tabelas e citando **obrigatoriamente a página exata `[Pág. X]`**.
- 🧠 **Auditoria ReAct (AgentScope):** Detecção cirúrgica de armadilhas operacionais (*red flags*, prazos curtos, restrições de marcas, exigência de amostras) e cálculo de score de compatibilidade (0 a 10).
- 🔌 **Servidor Central MCP (`FastMCP`):** 100% das capacidades expostas como ferramentas e recursos do padrão Model Context Protocol (`buscar_e_auditar_licitacoes`, `consultar_clausula_edital`, `obter_metricas_dashboard`, `listar_editais_salvos`, `disparar_relatorio_whatsapp`).
- 💬 **Chatbot Jurídico IA Integrado:** Interface conversacional com seletor arredondado (`rounded-full`) de edital em foco, chips de ação rápida e execução de comandos ReAct em linguagem natural.
- 🖥️ **Desktop Windows Nativo (pywebview + pystray):** Dashboard visual moderno em Neon Lime e Dark Mode, ícone na bandeja do sistema, agendador em segundo plano (`APScheduler`) e inicialização automática no boot do Windows (`winreg`).
- 🔐 **Licenciamento HWID & Proxy Seguro:** Trava física de placa-mãe (UUID + SHA-256) via Supabase e Edge Function Proxy para blindar chaves de LLM contra engenharia reversa.
- 📲 **Alerta Executivo no WhatsApp:** Envio cirúrgico do relatório executivo formatado para tomada de decisão no celular.

---

## 🏛️ Arquitetura do Sistema

```
                      ┌──────────────────────────────────────────────┐
                      │    Zeno Radar — Desktop App (pywebview)      │
                      │  Dashboard Moderno + Chatbot Jurídico ReAct  │
                      └──────────────────────┬───────────────────────┘
                                             │ (Assíncrono / JS Bridge)
                      ┌──────────────────────▼───────────────────────┐
                      │         DesktopAPI (src/desktop/api.py)      │
                      └──────────────────────┬───────────────────────┘
                                             │
                      ┌──────────────────────▼───────────────────────┐
                      │    Servidor MCP Central (FastMCP / In-Process│
                      │         (src/mcp_server/zeno_mcp.py)         │
                      └──────┬───────────────┬───────────────┬───────┘
                             │               │               │
            ┌────────────────▼┐     ┌────────▼───────┐     ┌─▼──────────────┐
            │   Collectors    │     │   Parsers      │     │  Intelligence  │
            ├─────────────────┤     ├────────────────┤     ├────────────────┤
            │ • PNCP REST API │     │ • PyMuPDF      │     │ • PageIndex RAG│
            │ • Scrapling     │     │ • PaddleOCR    │     │ • ReAct Auditor│
            └────────┬────────┘     └────────┬───────┘     └─┬──────────────┘
                     │                       │               │
                     └───────────────────────┼───────────────┘
                                             │
                      ┌──────────────────────▼───────────────────────┐
                      │     Repositório SQLite Persistente           │
                      │   (%LOCALAPPDATA%\ZenoRadar\zeno_radar.db)   │
                      └──────────────────────────────────────────────┘
```

---

## 🛠️ Ferramentas MCP (Model Context Protocol)

O motor expõe nativamente as seguintes **MCP Tools** e **MCP Resources** através do módulo [`src/mcp_server/zeno_mcp.py`](file:///e:/zeno%20lic/src/mcp_server/zeno_mcp.py):

| Tool MCP | Descrição |
| :--- | :--- |
| `buscar_e_auditar_licitacoes(uf, termo_busca, limite)` | Aciona o PNCP e Scrapling, faz o download dos editais, processa páginas, constrói a árvore PageIndex, audita via ReAct e salva no SQLite. |
| `listar_editais_salvos(filtro_texto, score_minimo)` | Consulta os editais auditados no banco local, cruzando com arquivos físicos da pasta de downloads. |
| `obter_metricas_dashboard()` | Consolida métricas em tempo real (volume total, score médio, alta aderência, fatias semanais e categorias de nicho). |
| `consultar_clausula_edital(pncp_id, pergunta_juridica)` | Navega na árvore PageIndex do edital e responde a dúvidas jurídicas (amostras, atestados, prazos, índices) citando **`[Pág. X]`**. |
| `disparar_relatorio_whatsapp(pncp_id)` | Formata e envia o resumo executivo do edital para o número de WhatsApp configurado. |

### MCP Resources Expostos:
- `edital://{pncp_id}`: Dados estruturados do edital auditado (JSON).
- `metricas://dashboard`: Métricas consolidadas em tempo real do banco SQLite.

---

## 📂 Estrutura de Diretórios

```text
zeno-lic/
├── assets/                          # Ícones e identidades visuais (.ico, .png)
├── docs/                            # Documentação técnica e esquemas de arquitetura
├── src/
│   ├── collectors/
│   │   ├── pncp_api.py              # Cliente de alta performance da API do PNCP
│   │   ├── scrapling_fetcher.py     # Download furtivo de PDFs e descompactação (.zip/.rar)
│   │   └── tinyfish_agent.py        # Integração de coleta complementar externa
│   ├── database/
│   │   ├── models.py                # Contratos e modelos Pydantic (EditalAuditado, ItemLote)
│   │   └── repository.py            # Camada de persistência SQLite com idempotência
│   ├── desktop/
│   │   ├── api.py                   # Bridge assíncrona Desktop Python <-> pywebview
│   │   ├── startup_manager.py       # Registro automático no Windows Startup (Run key)
│   │   ├── tray_app.py              # Aplicação de bandeja Windows (pystray + APScheduler)
│   │   └── ui/
│   │       └── index.html           # Interface Gráfica completa (Dashboard, Editais, Chatbot)
│   ├── intelligence/
│   │   ├── chat_assistant.py        # Chatbot Jurídico ReAct conectado às ferramentas MCP
│   │   ├── pageindex_tree.py        # Vectorless RAG com preservação hierárquica de seções
│   │   └── react_auditor.py         # Agente de auditoria ReAct técnica e jurídica
│   ├── licensing/
│   │   ├── hwid.py                  # Identificação de hardware da placa-mãe (UUID + SHA-256)
│   │   └── validator.py             # Validador de licença remoto e local
│   ├── mcp_server/
│   │   ├── __init__.py              # Exportação central de ferramentas MCP
│   │   └── zeno_mcp.py              # Servidor MCP FastMCP com ferramentas e recursos
│   ├── notifier/
│   │   └── whatsapp_sender.py       # Despachante de relatórios executivos via WhatsApp
│   ├── parsers/
│   │   ├── paddle_ocr_worker.py     # OCR seletivo sob demanda para documentos escaneados
│   │   └── pdf_router.py            # Roteador híbrido PyMuPDF + PaddleOCR
│   ├── utils/
│   │   └── paths.py                 # Gestão de caminhos persistentes no %LOCALAPPDATA%
│   ├── config.py                    # Configurações globais e variáveis de ambiente
│   └── main.py                      # Orquestrador central em linha de comando / cron
├── supabase/
│   ├── schema_licenses.sql          # Esquema SQL do Supabase para gestão de licenças
│   └── functions/ai-proxy/          # Supabase Edge Function para mascaramento de chaves LLM
├── tests/
│   ├── test_engine.py               # Testes de unidade do motor de coleta e parsing
│   ├── test_phase2.py               # Testes de licenciamento, HWID e bandeja
│   └── test_mcp_integration.py      # Suíte de integração total do servidor MCP e Chatbot
├── build_installer.py               # Script de geração do executável PyInstaller
├── docker-compose.yml               # Configuração de containers para serviços auxiliares
├── requirements.txt                 # Dependências Python do ecossistema
└── ZenoRadar.spec                   # Especificação de empacotamento PyInstaller
```

---

## 🚀 Instalação e Execução

### Pré-requisitos
- **Windows 10 / 11** (64-bit)
- **Python 3.11+** ou gerenciador [`uv`](https://github.com/astral-sh/uv)

### 1. Clonar o Repositório
```bash
git clone https://github.com/enzo-playboy/zeno.git
cd zeno
```

### 2. Criar e Ativar Ambiente Virtual
```powershell
# Usando uv (Recomendado)
uv venv .venv
.\.venv\Scripts\activate

# Ou usando Python padrão
python -m venv .venv
.\.venv\Scripts\activate
```

### 3. Instalar Dependências
```bash
uv pip install -r requirements.txt
# ou: pip install -r requirements.txt
```

### 4. Configurar Variáveis de Ambiente
Copie o arquivo de exemplo e ajuste suas credenciais:
```bash
copy .env.example .env
```
Parâmetros principais:
- `TARGET_UF`: Estado alvo das licitações (padrão: `MT`).
- `TARGET_KEYWORDS`: Palavras-chave de nicho (ex: `computador,notebook,ti,software`).
- `OPENAI_API_KEY` / `GEMINI_API_KEY`: Chave da LLM para o auditor ReAct (opcional se rodar em modo heurístico local ou via Proxy Supabase).
- `WHATSAPP_API_URL` e `WHATSAPP_API_TOKEN`: Endpoint do gateway WhatsApp (Z-API / Evolution).

---

## 🖥️ Como Executar

### 1. Interface Gráfica com Bandeja (Recomendado)
```bash
python src/desktop/tray_app.py
```
- Abre a janela do **Zeno Radar** com a UI nativa.
- Mantém o ícone verde tático no **System Tray** perto do relógio do Windows.
- Ativa o agendamento em background para checagem a cada 2 horas.

### 2. Servidor MCP Standalone
Para conectar a clientes MCP externos (Claude Desktop, IDEs ou agentes externos):
```bash
python -m src.mcp_server.zeno_mcp
```

### 3. Execução em Linha de Comando (Modo CLI)
```bash
# Executar um único ciclo de varredura
python src/main.py --uf MT --limit 5

# Executar em modo contínuo (loop)
python src/main.py --uf MT --cron 60
```

---

## 🧪 Suíte de Testes

Para validar a integridade de todas as ferramentas MCP, banco de dados, parsing e Chatbot:

```bash
# Teste de Integração Total MCP + Chatbot + Engine
python tests/test_mcp_integration.py

# Testes de Licenciamento, HWID e System Tray
python tests/test_phase2.py
```

---

## 📦 Gerando o Executável (`ZenoRadar.exe`)

O empacotamento com **PyInstaller** foi planejado para rodar in-process, sem necessidade de Node.js, `npx` ou instaladores pesados:

```bash
python build_installer.py
```

O binário final e a pasta portátil serão criados em `dist/ZenoRadar/`.

---

## ⚖️ Conformidade Legal & Privacidade

- **Lei Geral de Proteção de Dados (LGPD):** Não armazena dados pessoais sensíveis além de contatos corporativos autorizados para alertas.
- **Lei 14.133/2021:** Em total conformidade com o princípio da publicidade administrativa e com os padrões abertos do **Portal Nacional de Contratações Públicas (PNCP)**.
- **Zero Servidor Volátil:** Os dados e PDFs são mantidos sob controle do usuário em `%LOCALAPPDATA%\ZenoRadar\`.

---

## 👨‍💻 Autor & Licença

Desenvolvido para operações de inteligência em licitações públicas com foco em máxima eficiência, baixo custo de infraestrutura e alta precisão jurídica.

Distribuído sob licença proprietária para uso em regime OaaS (Operation as a Service).
