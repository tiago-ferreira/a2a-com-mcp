# Desafio: A Ponte — Um Agente A2A com MCP por Dentro

Implementação de referência do desafio de arquitetura **A Ponte**, integrando um servidor **MCP (Model Context Protocol)** em Streamable HTTP e um agente **A2A (Agent-to-Agent)** em JSON-RPC 2.0 sobre HTTP, conectando o ciclo de elicitation/MRTR (Multi-Round Tool Result) à máquina de estados de tarefas do A2A.

---

## 1. Como rodar

A execução exige Python 3.10 ou superior. Não há dependências externas obrigatórias para os servidores em tempo de execução além da biblioteca padrão do Python. O projeto também inclui arquivos de dependências travadas (`requirements.txt`, `servidor-mcp/pyproject.toml` e `agente/pyproject.toml`) caso deseje utilizar ambiente virtual.

### Pré-requisito: Geração da chave secreta de integridade
Gere uma chave aleatória de no mínimo 32 bytes para o `REQUEST_STATE_SECRET`:
```bash
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

### Passo 1: Iniciar o Servidor MCP (Terminal 1)
O servidor MCP atende na porta `7301` no endpoint `/mcp` utilizando transporte Streamable HTTP:
```bash
export REQUEST_STATE_SECRET="sua-chave-secreta-gerada-com-mais-de-32-bytes"
python3 servidor-mcp/servidor.py
```
*O `stderr` exibirá os logs estruturados de cada chamada (`[MCP] method=... id=... traceparent=...`).*

### Passo 2: Iniciar o Agente A2A (Terminal 2)
O agente expõe o Agent Card em `/.well-known/agent-card.json` e a interface JSON-RPC em `/a2a` na porta `7300`:
```bash
python3 agente/agente.py
```

### Passo 3: Executar a suíte de validação (Terminal 3)
Com os dois processos recém-iniciados, execute o validador oficial:
```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

---

## 2. Onde a ponte acontece

A ponte entre os dois protocolos sem estado (stateless) reside em [`agente/agente.py`](agente/agente.py):

1. **De MCP `input_required` para A2A `TASK_STATE_INPUT_REQUIRED`**:
   No método `processar_send_message` (linhas 272 a 306), ao receber uma resposta com `resultType == "input_required"` da ferramenta `reservar_sala`, o agente extrai o schema com a lista ordenada de salas alternativas de `inputRequests[chave].params.requestedSchema.properties.sala.enum`, armazena isoladamente o token opaco `requestState` e a chave de elicitation na Task em memória (`nova_task["_requestState"]` e `nova_task["_chaveElicitation"]`), transita a Task para o estado `TASK_STATE_INPUT_REQUIRED` e emite a mensagem de texto exata `alternativas: <ids separados por vírgula e espaço>`. O `requestState` **nunca** é devolvido para o cliente externo A2A, mantendo sua opacidade.

2. **Do retorno da resposta do cliente para o retry do servidor MCP**:
   No método `processar_escolha` (linhas 347 a 428), quando o cliente A2A envia uma nova mensagem referenciando a Task com `escolha=<id>` ou `escolha=recusar`:
   - Se for uma recusa (`escolha=recusar`), o agente submete ao servidor MCP um retry com `action: "decline"`, transita a Task para `TASK_STATE_CANCELED` e conclui a operação sem erro.
   - Se for uma escolha válida dentre as alternativas oferecidas, o agente gera um **novo JSON-RPC ID** (garantindo id estritamente diferente da requisição inicial), constrói o payload com `inputResponses: {chave: {"action": "accept", "content": {"sala": valor_escolha}}}`, reenvia o `requestState` original íntegro e propaga o `traceparent`. Ao receber o retorno de sucesso do MCP, o agente transita a Task para `TASK_STATE_COMPLETED` e anexa o artifact `reserva` contendo o JSON com a reserva e a versão da política.

---

## 3. Decisões técnicas

### Proteção de integridade do `requestState`
O `requestState` é emitido pelo servidor MCP no formato:
$$\text{base64url}(\text{payload\_json}) \mathbin{\Vert} \text{"."} \mathbin{\Vert} \text{base64url}(\text{hmac\_sha256})$$
- O payload carrega os dados originais do pedido (`sala`, `inicio`, `fim`, `responsavel`), as alternativas calculadas, um timestamp de expiração (`exp`) e um `nonce` aleatório.
- A assinatura é gerada via **HMAC-SHA256** utilizando a chave fornecida pela variável de ambiente `REQUEST_STATE_SECRET` (nunca codificada no código).
- Qualquer modificação de caractere, adulteração de parâmetro ou assinatura inválida é sumariamente rejeitada com erro JSON-RPC `-32602`.
- Como o estado é auto-contido e assinado, ele independe de sessão em memória no servidor: um retry enviado após o reinício do processo do servidor MCP é verificado e processado com sucesso.
- No retry, os parâmetros informados no payload do cliente são ignorados em favor dos valores selados no token, impedindo divergências maliciosas.

### Tempo de expiração do `requestState`
O token é configurado com validade de **15 minutos** (definido dentro do intervalo estipulado de 5 a 30 minutos). O servidor valida `time.time() > exp` e rejeita tokens vencidos com `-32602`.

### Armazenamento e isolamento do estado das Tasks
No agente, o estado das tarefas é mantido em um dicionário em memória (`TASKS`), mapeando cada `taskId` único para sua estrutura de dados isolada.
- Isso assegura que duas ou mais Tasks pausadas ao mesmo tempo em `TASK_STATE_INPUT_REQUIRED` retenham seus respectivos `requestState` de maneira totalmente desacoplada e independente.
- Cada resposta ao cliente A2A passa pela função de sanitização `limpar_task_para_resposta`, que remove explicitamente qualquer metadado interno ou token MCP antes da serialização.

---

## 4. Saída do validador

Execução oficial de conformidade das 36 verificações:

```text
Servidor MCP iniciado na porta 7301
Agente A2A iniciado na porta 7300
trace-id desta execucao: 7b7edef3ffb5694f8aa3caa1ce9b1835
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

[MCP] method=tools/list id=f7054ac5aa0c
PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
[MCP] method=tools/call id=de8c1f2ee7a8
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
[MCP] method=tools/call id=942af4457000
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
[MCP] method=tools/call id=c77a23d3f969
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
[MCP] method=tools/call id=4fbff6ceabf2
PASS 06 tool inexistente e recusada, por -32602 ou por isError
[MCP] method=resources/read id=32e1efc509c0
PASS 07 resources/read de politica://uso devolve a politica
[MCP] method=resources/read id=c4832d89447c
PASS 08 resources/read de URI inexistente devolve -32602
[MCP] method=tools/call id=fd0964325357
PASS 09 sala inexistente devolve isError com a mensagem exata
[MCP] method=tools/call id=eeb9e28e11d4
PASS 10 fora da janela devolve isError com a mensagem exata
[MCP] method=tools/call id=75fd069b88db
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
[MCP] method=tools/call id=c2a11699bb04
PASS 12 intervalo invertido devolve isError com a mensagem exata
[MCP] method=tools/call id=53cad7cb231a traceparent=00-7b7edef3ffb5694f8aa3caa1ce9b1835-da0c7dab3d9c6ae2-01
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
[MCP] method=tools/call id=1b42e73bf0a0
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
[MCP] method=tools/call id=a592624186a6
[MCP] method=tools/call id=676da7f08b3d
PASS 16 retry com inputResponses e requestState conclui a reserva
[MCP] method=tools/call id=4994a79f7569
PASS 17 requestState adulterado e rejeitado com -32602
[MCP] method=tools/call id=0c5864e3e522
[MCP] method=tools/call id=3b82603cc510
[MCP] method=tools/call id=68e326d8b6e7
PASS 18 argumentos adulterados no retry nao tomam efeito
[MCP] method=tools/call id=9654876735de
PASS 19 recusa conclui sem reservar e sem isError
[MCP] method=tools/call id=a691eda2adbf
[MCP] method=tools/call id=5ff6afa9125e
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
[MCP] method=tools/list id=101
[MCP] method=resources/read id=102
[MCP] method=tools/call id=103 traceparent=00-7b7edef3ffb5694f8aa3caa1ce9b1835-da0c7dab3d9c6ae2-01
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
[MCP] method=tools/call id=104 traceparent=00-7b7edef3ffb5694f8aa3caa1ce9b1835-da0c7dab3d9c6ae2-01
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
[MCP] method=tools/call id=105 traceparent=00-7b7edef3ffb5694f8aa3caa1ce9b1835-da0c7dab3d9c6ae2-01
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
[MCP] method=tools/call id=106
[MCP] method=tools/call id=107
PASS 32 a recusa termina a Task em CANCELED
[MCP] method=tools/call id=108
[MCP] method=tools/call id=109
[MCP] method=tools/call id=110
[MCP] method=tools/call id=111
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
[MCP] method=tools/call id=112
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
[MCP] method=tools/call id=113
[MCP] method=tools/call id=114
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
