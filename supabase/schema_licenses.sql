-- Schema de Licenças e Telemetria para o Motor OaaS de Licitações (Zeno Radar)
-- Executar no SQL Editor do Supabase

create table if not exists public.licenses (
  id uuid default gen_random_uuid() primary key,
  license_key text unique not null,
  client_name text not null,
  cnpj text,
  hwid text, -- Vinculado automaticamente no primeiro uso da máquina do cliente
  is_active boolean default true,
  expires_at timestamp with time zone not null,
  uf_target text default 'MT',
  keywords text[] default '{}',
  whatsapp_number text,
  whatsapp_webhook_url text,
  last_check_in timestamp with time zone,
  created_at timestamp with time zone default now()
);

-- Índice rápido por chave de licença para validação ultrarrápida
create index if not exists idx_licenses_key on public.licenses(license_key);

-- Comentários descritivos das colunas
comment on table public.licenses is 'Tabela de controle de licenças e bind de HWID para clientes do Zeno Radar OaaS';
comment on column public.licenses.hwid is 'Hash SHA-256 único da placa-mãe (UUID de hardware) vinculado no primeiro check-in';
comment on column public.licenses.uf_target is 'UF prioritária configurada para o cliente (ex: MT, SP, etc.)';
comment on column public.licenses.keywords is 'Lista de palavras-chave de interesse para a auditoria personalizada';

-- Exemplo de licença de teste para desenvolvimento (válida por 1 ano)
-- insert into public.licenses (license_key, client_name, cnpj, expires_at, uf_target, keywords, whatsapp_number)
-- values ('ZENO-DEV-TEST-2026', 'Cliente Piloto MT', '00.000.000/0001-91', now() + interval '365 days', 'MT', array['informatica', 'notebook', 'servidor', 'ti'], '5565999999999');
