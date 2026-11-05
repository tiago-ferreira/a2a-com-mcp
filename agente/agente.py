#!/usr/bin/env python3
"""Agente A2A e host MCP para o domínio de reserva de salas da Hill Valley Tech.

Porta padrão: 7300
Endpoints:
  GET /.well-known/agent-card.json
  POST /a2a
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

A2A_PROTOCOLO = "1.0"
MCP_PROTOCOLO = "2026-07-28"

MCP_BASE_URL = os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")

# Armazenamento em memória das Tasks
TASKS: dict[str, dict] = {}

# Contador de IDs MCP para garantir IDs únicos
MCP_ID_COUNTER = 100

# Cache de ferramentas descobertas e versão da política
FERRAMENTAS_DESCOBERTAS: list[str] = []
VERSAO_POLITICA: str = "2026-11-01"


def chamar_mcp(metodo: str, params: dict, nome: str | None = None, traceparent: str | None = None) -> tuple[int, dict]:
    global MCP_ID_COUNTER
    MCP_ID_COUNTER += 1
    req_id = MCP_ID_COUNTER

    meta = {
        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOLO,
        "io.modelcontextprotocol/clientInfo": {
            "name": "agente-central-de-salas",
            "version": "1.0.0"
        },
        "io.modelcontextprotocol/clientCapabilities": {
            "elicitation": {
                "form": {}
            }
        }
    }
    if traceparent:
        meta["traceparent"] = traceparent

    params_com_meta = dict(params)
    params_com_meta["_meta"] = meta

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOLO,
        "Mcp-Method": metodo,
    }
    if nome:
        headers["Mcp-Name"] = nome

    corpo = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": metodo,
        "params": params_com_meta
    }

    req = urllib.request.Request(
        MCP_BASE_URL,
        data=json.dumps(corpo).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
            resposta = json.loads(r.read().decode("utf-8"))
            return status, resposta
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            resposta = json.loads(e.read().decode("utf-8"))
        except Exception:
            resposta = {"error": {"code": -32000, "message": str(e)}}
        return status, resposta
    except Exception as e:
        return 500, {"error": {"code": -32000, "message": f"Erro de comunicacao com servidor MCP: {e}"}}


def inicializar_host_mcp():
    global FERRAMENTAS_DESCOBERTAS, VERSAO_POLITICA
    # 1. Descoberta dinâmica via tools/list
    _, resp_tools = chamar_mcp("tools/list", {})
    tools = (resp_tools.get("result") or {}).get("tools", [])
    FERRAMENTAS_DESCOBERTAS = [t.get("name") for t in tools]

    # 2. Leitura da versão no resource politica://uso
    _, resp_res = chamar_mcp("resources/read", {"uri": "politica://uso"}, nome="politica://uso")
    contents = (resp_res.get("result") or {}).get("contents") or []
    if contents:
        texto_politica = contents[0].get("text", "")
        for linha in texto_politica.splitlines():
            if linha.strip().startswith("versao:"):
                VERSAO_POLITICA = linha.split(":", 1)[1].strip()
                break


def parse_pedido(texto: str) -> dict[str, str]:
    padroes = {
        "sala": r"sala=([^\s]+)",
        "inicio": r"inicio=([^\s]+)",
        "fim": r"fim=([^\s]+)",
        "responsavel": r"responsavel=([^\s]+)"
    }
    resultado = {}
    for k, pat in padroes.items():
        m = re.search(pat, texto)
        if m:
            resultado[k] = m.group(1)
    return resultado


def limpar_task_para_resposta(task: dict) -> dict:
    # Retorna uma cópia limpa da Task, sem campos internos (_requestState, etc.)
    return {
        "id": task["id"],
        "contextId": task["contextId"],
        "status": task["status"],
        "history": task["history"],
        "artifacts": task["artifacts"]
    }


class A2AAgentHandler(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        pass

    def enviar_json(self, status_code: int, dados: dict):
        corpo = json.dumps(dados).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path == "/.well-known/agent-card.json":
            self.servir_agent_card()
        else:
            self.send_error(404, "Not Found")

    def servir_agent_card(self):
        host = self.headers.get("Host", "127.0.0.1:7300")
        card = {
            "name": "Central de Salas",
            "description": "Reserva salas de reuniao da Hill Valley Tech.",
            "provider": {
                "organization": "Hill Valley Tech",
                "url": "https://hillvalley.example"
            },
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": f"http://{host}/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0"
                }
            ],
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "extendedAgentCard": False
            },
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "reservar-sala",
                    "name": "Reservar sala",
                    "description": "Reserva uma sala em um intervalo. Se houver conflito, pergunta qual alternativa usar.",
                    "tags": ["salas", "agenda"],
                    "inputModes": ["text/plain"],
                    "outputModes": ["text/plain"],
                    "examples": [
                        "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
                    ]
                }
            ]
        }
        self.enviar_json(200, card)

    def do_POST(self):
        caminho_esperado = os.environ.get("A2A_PATH", "/a2a")
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
        traceparent = self.headers.get("traceparent")

        # Assegurar descoberta do host MCP
        if not FERRAMENTAS_DESCOBERTAS:
            try:
                inicializar_host_mcp()
            except Exception:
                pass

        if metodo == "SendMessage":
            self.processar_send_message(req_id, params, traceparent)
        elif metodo == "GetTask":
            self.processar_get_task(req_id, params)
        else:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {metodo}"}
            })

    def processar_get_task(self, req_id: Any, params: dict):
        task_id = params.get("id")
        task = TASKS.get(task_id)
        if not task:
            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32001, "message": f"Task nao encontrada: {task_id}"}
            })
            return

        self.enviar_json(200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "task": limpar_task_para_resposta(task)
            }
        })

    def processar_send_message(self, req_id: Any, params: dict, traceparent: str | None):
        mensagem = params.get("message") or {}
        task_id = mensagem.get("taskId")
        partes = mensagem.get("parts") or []
        texto_msg = " ".join(p.get("text", "") for p in partes).strip()

        # Cenário 1: Mensagem para Task existente (continuação da ponte)
        if task_id:
            task = TASKS.get(task_id)
            if not task:
                self.enviar_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32001, "message": f"Task nao encontrada: {task_id}"}
                })
                return

            estado_atual = task["status"]["state"]
            if estado_atual in ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"):
                # Rejeitar mensagem para Task em estado terminal
                self.enviar_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32002, "message": f"Task em estado terminal: {estado_atual}"}
                })
                return

            # Registrar mensagem do usuário no histórico
            msg_usuario = {
                "messageId": mensagem.get("messageId") or f"msg-{secrets.token_hex(6)}",
                "role": "ROLE_USER",
                "parts": [{"text": texto_msg}],
                "taskId": task_id
            }
            task["history"].append(msg_usuario)

            # Analisar resposta de escolha
            if texto_msg.startswith("escolha="):
                valor_escolha = texto_msg.split("=", 1)[1].strip()
                self.processar_escolha(req_id, task, valor_escolha, traceparent)
            else:
                # Entrada não reconhecida
                self.enviar_json(200, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"task": limpar_task_para_resposta(task)}
                })
            return

        # Cenário 2: Novo pedido (abre nova Task)
        novo_task_id = f"task-{secrets.token_hex(6)}"
        context_id = f"ctx-{secrets.token_hex(6)}"

        dados_pedido = parse_pedido(texto_msg)
        sala = dados_pedido.get("sala", "")
        inicio = dados_pedido.get("inicio", "")
        fim = dados_pedido.get("fim", "")
        responsavel = dados_pedido.get("responsavel", "")

        msg_usuario = {
            "messageId": mensagem.get("messageId") or f"msg-{secrets.token_hex(6)}",
            "role": "ROLE_USER",
            "parts": [{"text": texto_msg}]
        }

        nova_task = {
            "id": novo_task_id,
            "contextId": context_id,
            "status": {
                "state": "TASK_STATE_WORKING",
                "message": None
            },
            "history": [msg_usuario],
            "artifacts": [],
            "_requestState": None,
            "_chaveElicitation": None,
            "_alternativas": [],
            "_originalArgs": dados_pedido,
            "_traceparent": traceparent
        }
        TASKS[novo_task_id] = nova_task

        # Chamar ferramenta MCP reservar_sala
        argumentos = {
            "sala": sala,
            "inicio": inicio,
            "fim": fim,
            "responsavel": responsavel
        }
        _, resposta_mcp = chamar_mcp("tools/call", {
            "name": "reservar_sala",
            "arguments": argumentos
        }, nome="reservar_sala", traceparent=traceparent)

        resultado = resposta_mcp.get("result") or {}
        erro_rpc = resposta_mcp.get("error")

        if erro_rpc or resultado.get("isError"):
            # Falha de execução da tool
            msg_erro = ""
            if resultado.get("content"):
                msg_erro = " ".join(c.get("text", "") for c in resultado.get("content", []))
            elif erro_rpc:
                msg_erro = erro_rpc.get("message", "Erro na execucao da tool")

            msg_agente = {
                "messageId": f"msg-{secrets.token_hex(6)}",
                "role": "ROLE_AGENT",
                "parts": [{"text": msg_erro}],
                "taskId": novo_task_id,
                "contextId": context_id
            }
            nova_task["status"]["state"] = "TASK_STATE_FAILED"
            nova_task["status"]["message"] = msg_agente
            nova_task["history"].append(msg_agente)

            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"task": limpar_task_para_resposta(nova_task)}
            })
            return

        result_type = resultado.get("resultType")

        if result_type == "input_required":
            # Conflito: transição para INPUT_REQUIRED
            input_requests = resultado.get("inputRequests") or {}
            chave_elicitation = next(iter(input_requests), None)
            request_state = resultado.get("requestState")

            alternativas = []
            if chave_elicitation:
                params_elicitation = (input_requests[chave_elicitation].get("params") or {})
                schema = params_elicitation.get("requestedSchema") or {}
                prop_sala = (schema.get("properties") or {}).get("sala", {})
                alternativas = prop_sala.get("enum") or ([prop_sala["const"]] if "const" in prop_sala else [])

            # Salvar estado opaco na Task
            nova_task["_requestState"] = request_state
            nova_task["_chaveElicitation"] = chave_elicitation
            nova_task["_alternativas"] = alternativas

            texto_alternativas = f"alternativas: {', '.join(alternativas)}"
            msg_agente = {
                "messageId": f"msg-{secrets.token_hex(6)}",
                "role": "ROLE_AGENT",
                "parts": [{"text": texto_alternativas}],
                "taskId": novo_task_id,
                "contextId": context_id
            }
            nova_task["status"]["state"] = "TASK_STATE_INPUT_REQUIRED"
            nova_task["status"]["message"] = msg_agente
            nova_task["history"].append(msg_agente)

            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"task": limpar_task_para_resposta(nova_task)}
            })
            return

        # Conclusão com sucesso direto (sala livre)
        estruturado = resultado.get("structuredContent") or {}
        reserva_id = estruturado.get("reserva")
        sala_confirmada = estruturado.get("sala")

        msg_agente = {
            "messageId": f"msg-{secrets.token_hex(6)}",
            "role": "ROLE_AGENT",
            "parts": [{"text": f"Reserva {reserva_id} confirmada na {sala_confirmada}."}],
            "taskId": novo_task_id,
            "contextId": context_id
        }
        nova_task["status"]["state"] = "TASK_STATE_COMPLETED"
        nova_task["status"]["message"] = msg_agente
        nova_task["history"].append(msg_agente)

        # Artifact com o JSON da reserva criada
        artifact_json = json.dumps({
            "reserva": reserva_id,
            "sala": sala_confirmada,
            "inicio": estruturado.get("inicio"),
            "fim": estruturado.get("fim"),
            "responsavel": estruturado.get("responsavel"),
            "politica": estruturado.get("politica") or VERSAO_POLITICA
        })
        nova_task["artifacts"] = [
            {
                "artifactId": f"art-{secrets.token_hex(6)}",
                "name": "reserva",
                "parts": [{"text": artifact_json}]
            }
        ]

        self.enviar_json(200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"task": limpar_task_para_resposta(nova_task)}
        })

    def processar_escolha(self, req_id: Any, task: dict, valor_escolha: str, traceparent: str | None):
        task_id = task["id"]
        context_id = task["contextId"]
        alternativas = task["_alternativas"]
        tp = traceparent or task.get("_traceparent")

        # 1. Recusa
        if valor_escolha == "recusar":
            chave = task["_chaveElicitation"]
            request_state = task["_requestState"]
            orig = task["_originalArgs"]

            params_retry = {
                "name": "reservar_sala",
                "arguments": orig,
                "inputResponses": {
                    chave: {"action": "decline"}
                },
                "requestState": request_state
            }
            chamar_mcp("tools/call", params_retry, nome="reservar_sala", traceparent=tp)

            msg_agente = {
                "messageId": f"msg-{secrets.token_hex(6)}",
                "role": "ROLE_AGENT",
                "parts": [{"text": "Reserva cancelada pelo usuario."}],
                "taskId": task_id,
                "contextId": context_id
            }
            task["status"]["state"] = "TASK_STATE_CANCELED"
            task["status"]["message"] = msg_agente
            task["history"].append(msg_agente)

            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"task": limpar_task_para_resposta(task)}
            })
            return

        # 2. Escolha fora do enum: mantém TASK_STATE_INPUT_REQUIRED
        if valor_escolha not in alternativas:
            texto_alternativas = f"alternativas: {', '.join(alternativas)}"
            msg_agente = {
                "messageId": f"msg-{secrets.token_hex(6)}",
                "role": "ROLE_AGENT",
                "parts": [{"text": texto_alternativas}],
                "taskId": task_id,
                "contextId": context_id
            }
            task["status"]["state"] = "TASK_STATE_INPUT_REQUIRED"
            task["status"]["message"] = msg_agente
            task["history"].append(msg_agente)

            self.enviar_json(200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"task": limpar_task_para_resposta(task)}
            })
            return

        # 3. Escolha válida: executa retry MCP com ID novo
        chave = task["_chaveElicitation"]
        request_state = task["_requestState"]
        orig = task["_originalArgs"]

        params_retry = {
            "name": "reservar_sala",
            "arguments": orig,
            "inputResponses": {
                chave: {
                    "action": "accept",
                    "content": {"sala": valor_escolha}
                }
            },
            "requestState": request_state
        }
        _, resp_retry = chamar_mcp("tools/call", params_retry, nome="reservar_sala", traceparent=tp)

        resultado = resp_retry.get("result") or {}
        estruturado = resultado.get("structuredContent") or {}
        reserva_id = estruturado.get("reserva")
        sala_confirmada = estruturado.get("sala") or valor_escolha

        msg_agente = {
            "messageId": f"msg-{secrets.token_hex(6)}",
            "role": "ROLE_AGENT",
            "parts": [{"text": f"Reserva {reserva_id} confirmada na {sala_confirmada}."}],
            "taskId": task_id,
            "contextId": context_id
        }
        task["status"]["state"] = "TASK_STATE_COMPLETED"
        task["status"]["message"] = msg_agente
        task["history"].append(msg_agente)

        artifact_json = json.dumps({
            "reserva": reserva_id,
            "sala": sala_confirmada,
            "inicio": estruturado.get("inicio") or orig.get("inicio"),
            "fim": estruturado.get("fim") or orig.get("fim"),
            "responsavel": estruturado.get("responsavel") or orig.get("responsavel"),
            "politica": estruturado.get("politica") or VERSAO_POLITICA
        })
        task["artifacts"] = [
            {
                "artifactId": f"art-{secrets.token_hex(6)}",
                "name": "reserva",
                "parts": [{"text": artifact_json}]
            }
        ]

        self.enviar_json(200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"task": limpar_task_para_resposta(task)}
        })


def run_agent(porta: int = 7300):
    server_address = ("", porta)
    httpd = ThreadingHTTPServer(server_address, A2AAgentHandler)
    sys.stderr.write(f"Agente A2A iniciado na porta {porta}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    porta = int(os.environ.get("A2A_PORT", 7300))
    run_agent(porta)
