# Windows MCP GPT — Operação resiliente

## Arquitetura operacional

O runtime persistente utiliza uma única cadeia operacional:

`Tarefa Agendada -> Supervisor -> tunnel-client -> python -m windows_mcp serve --transport stdio`

A tarefa agendada `Windows-MCP-GPT-Supervisor` inicia o supervisor sem janela. O supervisor mantém exatamente um túnel, valida a cadeia MCP real e persiste o estado no diretório local `.orquestrador`, que não deve ser versionado.

O perfil do túnel deve executar diretamente:

`D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio`

Não usar `uv run` no perfil operacional. A sincronização automática do `uv` pode tentar substituir executáveis em uso e impedir a inicialização do MCP.

## Saúde e recuperação

O supervisor considera o runtime saudável somente quando todas as condições são verdadeiras:

- existe exatamente um processo `tunnel-client` do perfil gerenciado;
- existe processo MCP descendente com o comando direto esperado;
- `/healthz` retorna `live`;
- `/readyz` retorna `ready`;
- `/api/status` e `/api/system` confirmam o canal principal;
- o polling do plano de controle é recente;
- não há evento fatal recente de `502`, `504`, EOF ou pipe STDIO fechado.

Após falhas consecutivas, o supervisor encerra somente a cadeia defeituosa e cria um novo túnel com backoff e limite de reinícios. Reinícios bem-sucedidos zeram o histórico após ciclos saudáveis.

## Persistência

A fila usa caixa de entrada atômica, arquivos de especificação, resultados e evidências. Estados possíveis:

- `pending`
- `running`
- `completed`
- `failed`
- `interrupted`

Trabalhos longos são executados por processo-filho independente. O heartbeat e a recuperação do túnel continuam ativos durante testes e builds. Após reinício do supervisor, resultados persistidos são reconciliados sem repetir tarefas concluídas.

Gravações JSON usam arquivos temporários únicos, `fsync`, substituição atômica e retentativa. Bloqueios transitórios do Windows geram `persistence_retry`; não encerram o supervisor nem descartam a fila em memória.

## Isolamento de ferramentas

### Screenshot

O Screenshot permanece em quarentena por padrão no transporte STDIO. A captura gráfica é executada em processo isolado quando habilitada. Falha, timeout ou bloqueio de desktop deve afetar somente a chamada de Screenshot; as demais ferramentas e a sessão MCP permanecem disponíveis.

### SystemQuery

`SystemQuery` é somente leitura e não aceita comandos arbitrários. Consultas externas permitidas usam processo isolado, sem shell e sem pipes herdados, com prazo rígido e encerramento da árvore completa.

`git_status` não inicia o Git. Ele lê localmente `.git/HEAD` e o índice Git, compara metadados dos arquivos rastreados e omite a varredura de arquivos não rastreados. Isso evita que um `git status` lento atinja o TTL do túnel e feche o STDIO.

## Comandos operacionais

Enfileirar uma validação integral:

```powershell
& "D:\Projetos\WINDOWS-MCP-TEST\.venv\Scripts\python.exe" `
  "D:\Projetos\WINDOWS-MCP-TEST\scripts\enqueue_supervisor_task.py" `
  validate_project --timeout-seconds 1800
```

Consultar heartbeat:

```powershell
Get-Content "D:\Projetos\WINDOWS-MCP-TEST\.orquestrador\supervisor\heartbeat.json" -Raw
```

Consultar fila:

```powershell
Get-Content "D:\Projetos\WINDOWS-MCP-TEST\.orquestrador\supervisor\queue.json" -Raw
```

## Evidências aprovadas em 31/07/2026

- validação integral: lint, 442 testes, wheel e sdist;
- 500 chamadas concorrentes, concorrência 20, watchdog desligado;
- 500 chamadas concorrentes, concorrência 20, watchdog ligado;
- chamadas mistas de Health, SystemQuery date_time e SystemQuery git_status;
- rejeição de ação perigosa sem perda da sessão;
- Screenshot em quarentena seguido de Health no mesmo processo MCP;
- recuperação controlada do túnel em 12,723 segundos;
- recuperação controlada após encerramento exclusivo do processo MCP filho em 11,94 segundos;
- processo MCP antigo encerrado sem órfãos, novo MCP saudável e exatamente um túnel após a recuperação;
- 60 segundos de estabilidade, 55 amostras e nenhum túnel duplicado;
- bloqueio real de queue.json absorvido com retentativa, sem queda do supervisor e sem perda de tarefa.

## Critério de conclusão prolongada

A observação de 15 horas deve ser iniciada somente após validação integral aprovada. O resultado só pode ser considerado aprovado quando a tarefa de observação terminar sem amostras falhas no período pós-correção. Interrupções anteriores não contam como prova final.
## Pacote local legado

O diretório local WINDOWS-MCP-GPT não é versionado. Quando utilizado, sua configuração deve iniciar diretamente .venv/Scripts/python.exe -m windows_mcp serve --transport stdio; uv run é proibido no runtime persistente.
