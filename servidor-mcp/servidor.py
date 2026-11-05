#!/usr/bin/env python3
"""Servidor MCP em Streamable HTTP para o domínio de reservas de salas da Hill Valley Tech.

Porta padrão: 7301
Endpoint: /mcp
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DADOS_DIR = ROOT_DIR / "dados"

SALAS_FILE = DADOS_DIR / "salas.json"
RESERVAS_FILE = DADOS_DIR / "reservas.json"
POLITICA_FILE = DADOS_DIR / "politica-de-uso.md"

PROTOCOLO_MCP = "2026-07-28"

ERRO_SALA_PREFIX = "Sala inexistente: "
ERRO_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
ERRO_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"
ERRO_INTERVALO = "Intervalo invalido: fim deve ser posterior a inicio"
ERRO_SEM_ALTERNATIVA = "Sem alternativas disponiveis no intervalo"

RESOLVER_KEY = "__main__:escolha_de_sala"


def carregar_dados_iniciais() -> tuple[list[dict], list[dict], str, str]:
    with open(SALAS_FILE, "r", encoding="utf-8") as f:
        salas = json.load(f)
    with open(RESERVAS_FILE, "r", encoding="utf-8") as f:
        reservas = json.load(f)
    with open(POLITICA_FILE, "r", encoding="utf-8") as f:
        politica_texto = f.read()

    versao_politica = "2026-11-01"
    for linha in politica_texto.splitlines():
        if linha.strip().startswith("versao:"):
            versao_politica = linha.split(":", 1)[1].strip()
            break

    return salas, reservas, politica_texto, versao_politica


SALAS, RESERVAS_INICIAIS, POLITICA_TEXTO, VERSAO_POLITICA = carregar_dados_iniciais()
# Estado em memória de reservas durante o processo
RESERVAS: list[dict] = list(RESERVAS_INICIAIS)
PROXIMO_ID_RESERVA = 1000
for r in RESERVAS:
    rid = r.get("id", "")
    if rid.startswith("res-"):
        try:
            num = int(rid.split("-")[1])
            if num >= PROXIMO_ID_RESERVA:
                PROXIMO_ID_RESERVA = num + 1
        except ValueError:
            pass


def get_secret() -> bytes:
    secret = os.environ.get("REQUEST_STATE_SECRET", "")
    if not secret:
        # Fallback de segurança para testes/desenvolvimento
        secret = "chave-padrao-com-no-minimo-32-bytes-de-extensao-para-hmac"
    return secret.encode("utf-8")


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def b64url_decode(s: str) -> bytes:
    padding = 4 - (len(s) % 4)
    if padding < 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s.encode("utf-8"))


def assinar_request_state(payload: dict) -> str:
    dados_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = b64url_encode(dados_bytes)
    sig = hmac.new(get_secret(), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = b64url_encode(sig)
    return f"{payload_b64}.{sig_b64}"


def verificar_request_state(token: str) -> dict | None:
    partes = token.split(".")
    if len(partes) != 2:
        return None
    payload_b64, sig_b64 = partes
    try:
        sig_esperada = hmac.new(get_secret(), payload_b64.encode("utf-8"), hashlib.sha256).digest()
        sig_recebida = b64url_decode(sig_b64)
        if not hmac.compare_digest(sig_esperada, sig_recebida):
            return None
        payload_bytes = b64url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
        # Checar expiração
        exp = payload.get("exp", 0)
        if time.time() > exp:
            return None
        return payload
    except Exception:
        return None


def parse_iso(dt_str: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(dt_str)


def validar_horarios_e_politica(sala_id: str, inicio_str: str, fim_str: str) -> str | None:
    # 1. Sala existe?
    if not any(s["id"] == sala_id for s in SALAS):
        return f"{ERRO_SALA_PREFIX}{sala_id}"

    # 2. Parse datas
    try:
        dt_inicio = parse_iso(inicio_str)
        dt_fim = parse_iso(fim_str)
    except Exception:
        return "Formato de data/hora invalido"

    # Fim posterior a inicio?
    if dt_fim <= dt_inicio:
        return ERRO_INTERVALO

    # Duração máxima 2 horas (7200 segundos)
    if (dt_fim - dt_inicio).total_seconds() > 7200:
        return ERRO_DURACAO

    # Janela de uso: 08:00 às 20:00 (horário de São Paulo, offset -03:00)
    # Converte ou confere hora e minuto
    tz_sp = datetime.timezone(datetime.timedelta(hours=-3))
    loc_inicio = dt_inicio.astimezone(tz_sp)
    loc_fim = dt_fim.astimezone(tz_sp)

    min_dia_inicio = loc_inicio.hour * 60 + loc_inicio.minute
    min_dia_fim = loc_fim.hour * 60 + loc_fim.minute

    # Início permitido a partir das 08:00 (480 min) e até 20:00 (1200 min)
    # Fim permitido até as 20:00 (1200 min) e a partir das 08:00
    if min_dia_inicio < 8 * 60 or min_dia_inicio > 20 * 60:
        return ERRO_JANELA
    if min_dia_fim < 8 * 60 or min_dia_fim > 20 * 60:
        return ERRO_JANELA

    return None


def checar_conflito(sala_id: str, inicio_str: str, fim_str: str) -> list[dict]:
    dt_inicio = parse_iso(inicio_str)
    dt_fim = parse_iso(fim_str)
    conflitos = []
    for r in RESERVAS:
        if r["sala"] == sala_id:
            r_inicio = parse_iso(r["inicio"])
            r_fim = parse_iso(r["fim"])
            if max(dt_inicio, r_inicio) < min(dt_fim, r_fim):
                conflitos.append(r)
    return conflitos


def calcular_alternativas(sala_pedida_id: str, inicio_str: str, fim_str: str) -> list[str]:
    sala_pedida = next((s for s in SALAS if s["id"] == sala_pedida_id), None)
    if not sala_pedida:
        return []
    capacidade_minima = sala_pedida["capacidade"]

    candidatas = []
    for s in SALAS:
        if s["id"] == sala_pedida_id:
            continue
        if s["capacidade"] >= capacidade_minima:
            conflitos = checar_conflito(s["id"], inicio_str, fim_str)
            if not conflitos:
                candidatas.append(s)

    # Ordenar por capacidade crescente e, em caso de empate, por id ASC
    candidatas.sort(key=lambda s: (s["capacidade"], s["id"]))
    # Máximo 3
    return [s["id"] for s in candidatas[:3]]


class MCPServerHandler(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        # Suprimir log HTTP padrão do BaseHTTPRequestHandler para manter stderr limpo
        pass

    def emitir_log(self, metodo: str, req_id: Any, traceparent: str | None):
        tp_str = f" traceparent={traceparent}" if traceparent else ""
        sys.stderr.write(f"[MCP] method={metodo} id={req_id}{tp_str}\n")
        sys.stderr.flush()

    def enviar_json(self, status_code: int, dados: dict):
        corpo = json.dumps(dados).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self):
        caminho_esperado = os.environ.get("MCP_PATH", "/mcp")
        if self.path != caminho_esperado:
            self.send_error(404, "Endpoint not found")
            return

        comprimento = int(self.headers.get("Content-Length", 0))
        corpo_bytes = self.rfile.read(comprimento)
        try:
            req = json.loads(corpo_bytes.decode("utf-8"))
        except Exception:
            self.enviar_json(400, {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"}
            })
            return

        req_id = req.get("id")
        metodo = req.get("method")
        params = req.get("params") or {}
        meta = params.get("_meta") or {}

        traceparent = meta.get("traceparent")
        self.emitir_log(metodo, req_id, traceparent)

        # 1. Validação de _meta: protocolVersion e clientCapabilities são obrigatórios
        protocol_version = meta.get("io.modelcontextprotocol/protocolVersion")
        client_capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")

        if protocol_version is None or client_capabilities is None:
            self.enviar_json(400, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": "Missing required _meta fields: io.modelcontextprotocol/protocolVersion or io.modelcontextprotocol/clientCapabilities"
                }
            })
            return

        # 2. Validação de headers espelhados
        hdr_version = self.headers.get("MCP-Protocol-Version")
        hdr_method = self.headers.get("Mcp-Method")
        hdr_name = self.headers.get("Mcp-Name")

        if hdr_version and hdr_version != protocol_version:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32020, "message": "MCP-Protocol-Version header mismatch"}
            })
            return

        if hdr_method and hdr_method != metodo:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32020, "message": "Mcp-Method header mismatch"}
            })
            return

        nome_esperado = params.get("name") if metodo == "tools/call" else (params.get("uri") if metodo == "resources/read" else None)
        if hdr_name and nome_esperado and hdr_name != nome_esperado:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32020, "message": "Mcp-Name header mismatch"}
            })
            return

        # 3. Despacho de métodos
        if metodo == "tools/list":
            self.processar_tools_list(req_id)
        elif metodo == "resources/read":
            self.processar_resources_read(req_id, params)
        elif metodo == "tools/call":
            self.processar_tools_call(req_id, params, client_capabilities)
        else:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {metodo}"}
            })

    def processar_tools_list(self, req_id: Any):
        resposta = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "listar_salas",
                        "description": "Lista todas as salas com capacidade e recursos.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False
                        }
                    },
                    {
                        "name": "consultar_disponibilidade",
                        "description": "Consulta a disponibilidade de uma sala em um intervalo especificado.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sala": {"type": "string", "description": "ID da sala"},
                                "inicio": {"type": "string", "description": "Inicio da reserva (ISO 8601)"},
                                "fim": {"type": "string", "description": "Fim da reserva (ISO 8601)"}
                            },
                            "required": ["sala", "inicio", "fim"],
                            "additionalProperties": False
                        }
                    },
                    {
                        "name": "reservar_sala",
                        "description": "Reserva uma sala de reuniao na Hill Valley Tech.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sala": {"type": "string", "description": "ID da sala"},
                                "inicio": {"type": "string", "description": "Inicio da reserva (ISO 8601)"},
                                "fim": {"type": "string", "description": "Fim da reserva (ISO 8601)"},
                                "responsavel": {"type": "string", "description": "Nome do responsavel"}
                            },
                            "required": ["sala", "inicio", "fim", "responsavel"],
                            "additionalProperties": False
                        }
                    }
                ]
            }
        }
        self.enviar_json(200, resposta)

    def processar_resources_read(self, req_id: Any, params: dict):
        uri = params.get("uri")
        if uri == "politica://uso":
            resposta = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "contents": [
                        {
                            "uri": "politica://uso",
                            "mimeType": "text/markdown",
                            "text": POLITICA_TEXTO
                        }
                    ]
                }
            }
            self.enviar_json(200, resposta)
        else:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Resource not found: {uri}"}
            })

    def processar_tools_call(self, req_id: Any, params: dict, client_capabilities: dict):
        nome_tool = params.get("name")
        args = params.get("arguments") or {}

        if nome_tool == "listar_salas":
            salas_json_str = json.dumps(SALAS, indent=2)
            resposta = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "isError": False,
                    "structuredContent": SALAS,
                    "content": [
                        {"type": "text", "text": salas_json_str}
                    ]
                }
            }
            self.enviar_json(200, resposta)

        elif nome_tool == "consultar_disponibilidade":
            sala = args.get("sala", "")
            inicio = args.get("inicio", "")
            fim = args.get("fim", "")

            erro = validar_horarios_e_politica(sala, inicio, fim)
            if erro:
                self.enviar_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": erro}]
                    }
                })
                return

            conflitos = checar_conflito(sala, inicio, fim)
            disponivel = len(conflitos) == 0
            estruturado = {
                "disponivel": disponivel,
                "sala": sala,
                "inicio": inicio,
                "fim": fim,
                "conflitos": conflitos
            }
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "isError": False,
                    "structuredContent": estruturado,
                    "content": [{"type": "text", "text": json.dumps(estruturado, indent=2)}]
                }
            })

        elif nome_tool == "reservar_sala":
            self.processar_reservar_sala(req_id, params, client_capabilities)

        else:
            # Tool inexistente
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Tool not found: {nome_tool}"}
            })

    def processar_reservar_sala(self, req_id: Any, params: dict, client_capabilities: dict):
        global PROXIMO_ID_RESERVA

        args = params.get("arguments") or {}
        input_responses = params.get("inputResponses")
        request_state_raw = params.get("requestState")

        # Cenário A: É um Retry de MRTR
        if request_state_raw is not None:
            # 1. Validar requestState
            sealed = verificar_request_state(request_state_raw)
            if sealed is None:
                self.enviar_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Invalid, tampered or expired requestState"}
                })
                return

            # Valores selados vencem argumentos não confiáveis
            inicio = sealed["inicio"]
            fim = sealed["fim"]
            responsavel = sealed["responsavel"]
            alternativas_validas = sealed.get("alternativas", [])

            # Obter a resposta da elicitation
            resp_item = (input_responses or {}).get(RESOLVER_KEY)
            if not resp_item and input_responses:
                resp_item = next(iter(input_responses.values()), {})
            if not resp_item:
                resp_item = {}

            action = resp_item.get("action")

            if action in ("decline", "cancel"):
                estruturado = {
                    "reserva": None,
                    "reservado": False,
                    "sala": None,
                    "inicio": None,
                    "fim": None,
                    "responsavel": None,
                    "politica": None,
                    "motivo": "recusado"
                }
                self.enviar_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "resultType": "complete",
                        "isError": False,
                        "structuredContent": estruturado,
                        "content": [{"type": "text", "text": json.dumps(estruturado, indent=2)}]
                    }
                })
                return

            sala_escolhida = (resp_item.get("content") or {}).get("sala")
            if sala_escolhida not in alternativas_validas:
                # Escolha inválida
                self.enviar_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Chosen room '{sala_escolhida}' is not among offered alternatives"}
                })
                return

            # Concluir reserva na sala escolhida
            reserva_id = f"res-{PROXIMO_ID_RESERVA:04d}"
            PROXIMO_ID_RESERVA += 1
            nova_reserva = {
                "id": reserva_id,
                "sala": sala_escolhida,
                "inicio": inicio,
                "fim": fim,
                "responsavel": responsavel
            }
            RESERVAS.append(nova_reserva)

            estruturado = {
                "reserva": reserva_id,
                "reservado": True,
                "sala": sala_escolhida,
                "inicio": inicio,
                "fim": fim,
                "responsavel": responsavel,
                "politica": VERSAO_POLITICA,
                "motivo": None
            }
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "isError": False,
                    "structuredContent": estruturado,
                    "content": [{"type": "text", "text": json.dumps(estruturado, indent=2)}]
                }
            })
            return

        # Cenário B: Pedido inicial de reserva
        sala = args.get("sala", "")
        inicio = args.get("inicio", "")
        fim = args.get("fim", "")
        responsavel = args.get("responsavel", "")

        # 1. Validar política e horários
        erro = validar_horarios_e_politica(sala, inicio, fim)
        if erro:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": erro}]
                }
            })
            return

        # 2. Checar se a sala pedida está livre
        conflitos = checar_conflito(sala, inicio, fim)

        if not conflitos:
            # Caminho feliz: sala livre
            reserva_id = f"res-{PROXIMO_ID_RESERVA:04d}"
            PROXIMO_ID_RESERVA += 1
            nova_reserva = {
                "id": reserva_id,
                "sala": sala,
                "inicio": inicio,
                "fim": fim,
                "responsavel": responsavel
            }
            RESERVAS.append(nova_reserva)

            estruturado = {
                "reserva": reserva_id,
                "reservado": True,
                "sala": sala,
                "inicio": inicio,
                "fim": fim,
                "responsavel": responsavel,
                "politica": VERSAO_POLITICA,
                "motivo": None
            }
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resultType": "complete",
                    "isError": False,
                    "structuredContent": estruturado,
                    "content": [{"type": "text", "text": json.dumps(estruturado, indent=2)}]
                }
            })
            return

        # Conflito detectado: ciclo MRTR
        # 3. Validar se o cliente declarou elicitation em form mode
        has_form_elicitation = bool(
            isinstance(client_capabilities, dict)
            and isinstance(client_capabilities.get("elicitation"), dict)
            and isinstance(client_capabilities.get("elicitation", {}).get("form"), dict)
        )

        if not has_form_elicitation:
            self.enviar_json(400, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32021,
                    "message": f"Client did not declare the form elicitation capability required by resolver '{RESOLVER_KEY}'",
                    "data": {
                        "requiredCapabilities": {
                            "elicitation": {
                                "form": {}
                            }
                        }
                    }
                }
            })
            return

        # 4. Calcular alternativas
        alternativas = calcular_alternativas(sala, inicio, fim)
        if not alternativas:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": ERRO_SEM_ALTERNATIVA}]
                }
            })
            return

        # 5. Gerar requestState selado
        payload = {
            "sala": sala,
            "inicio": inicio,
            "fim": fim,
            "responsavel": responsavel,
            "alternativas": alternativas,
            "exp": int(time.time()) + 900,  # 15 minutos
            "nonce": secrets.token_hex(16)
        }
        token = assinar_request_state(payload)

        # 6. Retornar input_required
        prop_sala = {
            "description": "Sala alternativa escolhida",
            "type": "string"
        }
        if len(alternativas) == 1:
            prop_sala["enum"] = alternativas
        else:
            prop_sala["enum"] = alternativas

        resposta = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resultType": "input_required",
                "inputRequests": {
                    RESOLVER_KEY: {
                        "method": "elicitation/create",
                        "params": {
                            "message": "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
                            "mode": "form",
                            "requestedSchema": {
                                "type": "object",
                                "properties": {
                                    "sala": prop_sala
                                },
                                "required": ["sala"]
                            }
                        }
                    }
                },
                "requestState": token
            }
        }
        self.enviar_json(200, resposta)


def run_server(porta: int = 7301):
    server_address = ("", porta)
    httpd = ThreadingHTTPServer(server_address, MCPServerHandler)
    sys.stderr.write(f"Servidor MCP iniciado na porta {porta}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    porta = int(os.environ.get("MCP_PORT", 7301))
    run_server(porta)
