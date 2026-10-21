-- ==============================================================================
-- Schema Corporativo Completo para Zeno Radar no Supabase
-- Executar no SQL Editor do Painel do Supabase
-- ==============================================================================

-- Habilitar extensão pgcrypto para geração de UUIDs se necessário
create extension if not exists "pgcrypto";

-- 1. Tabela de Licenças e HWID Lock
create table if not exists public.licenses (
  id uuid default gen_random_uuid() primary key,
  license_key text unique not null,
  client_name text not null,
  cnpj text,
  hwid text,
  is_active boolean default true,
  expires_at timestamp with time zone not null,
  uf_target text default 'MT',
  keywords text[] default '{}',
  negative_keywords text[] default '{}',
  valor_minimo numeric,
  valor_maximo numeric,
  modalidades text[] default '{}',
  municipio_filtro text,
  score_minimo integer,
  whatsapp_number text,
  whatsapp_webhook_url text,
  last_check_in timestamp with time zone,
  created_at timestamp with time zone default now()
);
create index if not exists idx_licenses_key on public.licenses(license_key);

-- 2. Tabela de Organizações (Empresas Clientes Titulares do Plano Corporativo)
create table if not exists public.organizations (
  id text primary key,
  name text not null,
  cnpj text,
  plan_tier text default 'CORPORATE',
  max_seats integer default 4,
  uf_target text default 'MT',
  keywords text[] default '{}',
  negative_keywords text[] default '{}',
  valor_minimo numeric,
  valor_maximo numeric,
  modalidades text[] default '{}',
  created_at timestamp with time zone default now()
);

-- 3. Tabela de Postos de Trabalho / Funcionários (User Seats)
create table if not exists public.user_seats (
  id bigint generated always as identity primary key,
  org_id text not null references public.organizations(id) on delete cascade,
  user_name text not null,
  role text default 'analyst',
  hwid text not null,
  is_collector_node boolean default false,
  created_at timestamp with time zone default now(),
  last_active_at timestamp with time zone default now(),
  constraint uq_user_seats_org_hwid unique (org_id, hwid)
);
create index if not exists idx_user_seats_org on public.user_seats(org_id);
create index if not exists idx_user_seats_hwid on public.user_seats(hwid);

-- 4. Tabela de Editais Auditados e Idempotência
create table if not exists public.editais (
  id bigint generated always as identity primary key,
  numero_controle_pncp text unique not null,
  hash_identificador text unique,
  orgao text,
  modalidade text,
  data_disputa text,
  portal_disputa text,
  objeto_resumo text,
  valor_total_estimado numeric,
  score_aderencia numeric default 0.0,
  recomendacao_executiva text,
  itens_json jsonb default '[]'::jsonb,
  exigencias_json jsonb default '[]'::jsonb,
  armadilhas_json jsonb default '[]'::jsonb,
  raw_pncp_json jsonb default '{}'::jsonb,
  status_processamento text default 'PENDENTE',
  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);
create index if not exists idx_editais_pncp on public.editais(numero_controle_pncp);

-- 5. Tabela de Projetos Colaborativos da Empresa (Kanban / Workflow de Licitações)
create table if not exists public.bid_projects (
  id bigint generated always as identity primary key,
  pncp_id text not null,
  org_id text not null references public.organizations(id) on delete cascade,
  audit_json jsonb default '{}'::jsonb,
  workflow_status text default 'TRIAGEM',
  assigned_to_user text,
  internal_notes text,
  checklist_docs jsonb default '{}'::jsonb,
  updated_by text,
  updated_at timestamp with time zone default now(),
  created_at timestamp with time zone default now(),
  constraint uq_bid_projects_pncp_org unique (pncp_id, org_id)
);
create index if not exists idx_bid_projects_org on public.bid_projects(org_id);
create index if not exists idx_bid_projects_pncp on public.bid_projects(pncp_id);

-- 6. Tabela de Travas Distribuídas (Anti-colisão de scraping e auditoria concorrente)
create table if not exists public.processing_locks (
  pncp_id text not null,
  org_id text not null references public.organizations(id) on delete cascade,
  locked_by_user text not null,
  locked_at timestamp with time zone default now(),
  expires_at timestamp with time zone not null,
  primary key (pncp_id, org_id)
);

-- 7. Tabela de Alertas Enviados (WhatsApp / Webhook)
create table if not exists public.alertas (
  id bigint generated always as identity primary key,
  numero_controle_pncp text,
  destinatario text,
  score numeric,
  mensagem_status text,
  enviado_em timestamp with time zone default now()
);

-- Inserir organização default se não existir
insert into public.organizations (id, name, cnpj, plan_tier, max_seats, uf_target, keywords)
values ('org_default', 'Zeno Corporate Enterprise', '00.000.000/0001-99', 'CORPORATE', 4, 'MT', array['informatica', 'notebook', 'servidor', 'ti'])
on conflict (id) do nothing;
