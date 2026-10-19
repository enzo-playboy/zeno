# -*- coding: utf-8 -*-
"""Módulo do Servidor MCP (Model Context Protocol) Central do Zeno Radar."""
from .zeno_mcp import (
    mcp,
    buscar_e_auditar_licitacoes,
    listar_editais_salvos,
    obter_metricas_dashboard,
    consultar_clausula_edital,
    disparar_relatorio_whatsapp,
)

__all__ = [
    "mcp",
    "buscar_e_auditar_licitacoes",
    "listar_editais_salvos",
    "obter_metricas_dashboard",
    "consultar_clausula_edital",
    "disparar_relatorio_whatsapp",
]
