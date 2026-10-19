// Supabase Edge Function: ai-proxy
// Valida a licença e o HWID do cliente antes de repassar a requisição para a LLM,
// garantindo que as chaves de API nunca fiquem expostas no executável do cliente final.

import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-license-key, x-hwid",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

serve(async (req: Request) => {
  // Tratamento de CORS Preflight
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const licenseKey = req.headers.get("x-license-key");
    const hwid = req.headers.get("x-hwid");

    if (!licenseKey || !hwid) {
      return new Response(
        JSON.stringify({ error: "Cabeçalhos de autenticação ausentes (x-license-key e x-hwid são obrigatórios)." }),
        { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Inicializar cliente Supabase com Service Role Key para leitura protegida da tabela licenses
    const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
    const supabaseServiceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

    if (!supabaseUrl || !supabaseServiceKey) {
      return new Response(
        JSON.stringify({ error: "Configuração do Supabase incompleta no servidor (URL ou Service Role ausente)." }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const supabase = createClient(supabaseUrl, supabaseServiceKey);

    // 1. Validar Licença no Banco
    const { data: license, error: dbError } = await supabase
      .from("licenses")
      .select("id, client_name, hwid, is_active, expires_at")
      .eq("license_key", licenseKey)
      .single();

    if (dbError || !license) {
      return new Response(
        JSON.stringify({ error: "Chave de licença não encontrada ou inválida." }),
        { status: 403, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (!license.is_active) {
      return new Response(
        JSON.stringify({ error: "Esta licença foi suspensa ou desativada pelo administrador." }),
        { status: 403, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (new Date(license.expires_at) <= new Date()) {
      return new Response(
        JSON.stringify({ error: "Sua licença expirou. Entre em contato com o suporte para renovação." }),
        { status: 403, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Trava de Hardware (HWID Lock)
    if (license.hwid && license.hwid !== hwid) {
      return new Response(
        JSON.stringify({ error: "Licença já vinculada a outro computador físico (HWID Mismatch)." }),
        { status: 403, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Se ainda não tinha HWID gravado, grava o atual
    if (!license.hwid) {
      await supabase
        .from("licenses")
        .update({ hwid: hwid, last_check_in: new Date().toISOString() })
        .eq("id", license.id);
    } else {
      await supabase
        .from("licenses")
        .update({ last_check_in: new Date().toISOString() })
        .eq("id", license.id);
    }

    // 2. Extrair payload da requisição de auditoria
    const body = await req.json();
    const prompt = body.prompt;
    const model = body.model || "gpt-4o-mini";

    if (!prompt) {
      return new Response(
        JSON.stringify({ error: "Campo 'prompt' é obrigatório no corpo da requisição." }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // 3. Chamada à API da OpenAI (ou Gemini) com chave protegida no servidor
    const openAiApiKey = Deno.env.get("OPENAI_API_KEY");
    if (!openAiApiKey) {
      return new Response(
        JSON.stringify({ error: "OPENAI_API_KEY não configurada no servidor Supabase." }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const aiResponse = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${openAiApiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: model,
        messages: [{ role: "user", content: prompt }],
        response_format: { type: "json_object" },
        temperature: 0.1,
      }),
    });

    if (!aiResponse.ok) {
      const errText = await aiResponse.text();
      return new Response(
        JSON.stringify({ error: `Erro retornado pelo provedor de IA: ${errText}` }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const aiData = await aiResponse.json();
    const content = aiData.choices?.[0]?.message?.content ?? "{}";

    return new Response(content, {
      status: 200,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (err: any) {
    return new Response(
      JSON.stringify({ error: `Erro interno no Proxy de IA: ${err.message}` }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
