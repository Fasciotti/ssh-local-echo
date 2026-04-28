# ssh-local-echo

Wrapper de SSH com **echo local** para mitigar latência em servidores que não têm `mosh-server` instalado. Funciona sobre uma conexão SSH padrão, sem nenhuma modificação no servidor remoto.

Cada tecla aparece imediatamente no terminal local. As bytes só são enviadas ao servidor em triggers específicos (Enter, Tab, Ctrl-C/D/Z/L, setas).

## Requisitos

- Linux ou WSL
- Python 3.8+ (usa só stdlib: `pty`, `termios`, `select`, `fcntl`, `signal`)
- `ssh` no PATH

## Uso

```bash
# torne executável (uma vez)
chmod +x ssh_local_echo.py

# uso típico — passa argumentos direto pro ssh
./ssh_local_echo.py -p 2220 bandit4@bandit.labs.overthewire.org

# ou via python
python3 ssh_local_echo.py user@host
```

## Comportamento por tecla (modo `buffered`, padrão)

| Tecla            | Ação                                                                 |
|------------------|----------------------------------------------------------------------|
| Letras/símbolos  | Echo local + acumula no buffer (NÃO envia)                           |
| Backspace        | Apaga do buffer local se não vazio. Se vazio (pós-Tab/pós-Enter), encaminha pro servidor |
| Enter            | Envia buffer + `\r` ao servidor, limpa buffer, suprime echo de volta |
| Tab              | Envia buffer + `\t`, limpa buffer, deixa servidor exibir completion  |
| Ctrl-C           | Limpa buffer visualmente, envia `\x03`                               |
| Ctrl-D           | Envia buffer + `\x04`                                                |
| Ctrl-Z           | Limpa buffer visualmente, envia `\x1a`                               |
| Ctrl-L           | Envia `\x0c` (limpa tela), buffer preservado                         |
| Setas / F-keys   | Limpa buffer visualmente, envia sequência de escape                  |
| **Ctrl-O**       | Alterna entre `buffered` e `passthrough`                             |

### Modo `passthrough`

Tudo que você digita vai direto pro servidor, sem buffering nem echo local. Use para:

- **Aplicações fullscreen**: vim, less, top, htop, nano
- **Prompts de senha**: sudo, su, ssh dentro do ssh (caso contrário a senha aparece no terminal local)
- **Editores e jogos** que dependem de keystroke individual

`Ctrl-O` é a única tecla interceptada nesse modo (pra você conseguir voltar pro buffered).

### Filtro de BEL

Por padrão, o wrapper descarta bytes BEL (`\x07`) "soltos" da saída do servidor em modo buffered, pra silenciar os beeps do `readline` em tab completion (que ocorrem mesmo em completion única, dependendo do termcap do servidor). BELs dentro de OSC sequences (ex: `\e]0;titulo\a` que define o título da janela) são preservados — só BEL fora de OSC é dropado.

Use `--keep-bell` se quiser que todos os BELs passem direto.

## Como funciona

```
[Terminal local]  <-stdin/stdout->  [wrapper Python]  <-master PTY->  [ssh subprocess]  <-rede->  [servidor remoto]
```

1. `pty.fork()` cria um PTY mestre/escravo e bifurca o processo. O filho `exec()` o `ssh`.
2. O terminal local entra em `raw mode` (`tty.setraw`) — todas as teclas viram bytes brutos pro nosso loop.
3. `select()` multiplexa stdin (teclado do usuário) e o PTY mestre (saída do ssh).
4. Input do usuário é parseado byte a byte:
   - Bytes "normais" → buffer + echo local
   - Triggers → flush do buffer + tecla pra master_fd
5. Output do ssh é filtrado pra remover o **echo do servidor** das linhas que acabamos de mandar (o servidor reecoa o que enviamos; sem filtragem, cada char apareceria duas vezes).

### Supressão de echo

Quando enviamos `cd Documents\r`, registramos isso como `expected_echo`. Quando bytes chegam do servidor, fazemos *match* dos primeiros bytes contra `expected_echo` e descartamos os que casarem (com tolerância pra conversões `\r`/`\n`). Se houver divergência (ex: tab completion injetou bytes diferentes), abandonamos o match e exibimos tudo — melhor uma duplicação visual ocasional do que estado preso.

## Limitações conhecidas

- **Senhas dentro da sessão SSH**: se um comando remoto pedir senha (`sudo`, `passwd`, etc.), os caracteres digitados aparecerão no seu terminal local. **Use Ctrl-O pra entrar em passthrough antes de digitar a senha.** A própria autenticação inicial do `ssh` também tem esse problema; se for autenticação por senha, conecte primeiro em passthrough (`--passthrough`) ou prefira chave SSH.

- **Backspace pós-Tab**: depois de uma completion, o buffer local está vazio e o backspace é encaminhado ao servidor (que apaga via `readline`). Funciona, mas vai sentir uma latência igual ao SSH cru *só* nesses backspaces; os backspaces dos chars que você digitou *após* a completion ainda são instantâneos (apagados localmente).

- **Histórico do shell (setas ↑/↓)**: as setas são tratadas server-side. O wrapper limpa o buffer local visualmente antes de enviar a seta — o servidor então redesenha a linha do histórico. Funciona, mas você perde o que tava digitando localmente.

- **Aplicações fullscreen**: detecção automática de modo raw não está implementada. Use Ctrl-O pra entrar/sair de passthrough manualmente quando abrir/fechar `vim`, `less`, etc.

- **Multibyte / UTF-8**: chars multibyte funcionam pro envio, mas o backspace local apaga *um byte* do buffer, não um *codepoint*. Apagar um caractere acentuado pode exigir múltiplos backspaces e pode bagunçar o display momentaneamente.

## Teste rápido

```bash
./ssh_local_echo.py -p 2220 bandit0@bandit.labs.overthewire.org
# senha do bandit0 é "bandit0" — entre em passthrough antes de digitar:
# Ctrl-O, digite a senha, Enter, Ctrl-O de volta pra buffered
```

Depois de logado, digite `ls` devagar — você verá os caracteres aparecerem instantaneamente em vez de esperar o roundtrip da rede.

## Estrutura do repositório

```
ssh-local-echo/
├── ssh_local_echo.py        # wrapper (~480 linhas, só stdlib)
├── README.md
├── LICENSE                   # MIT
├── .gitignore
└── tests/
    ├── test_units.py         # testes unitários das funções puras
    ├── drive_test.py         # E2E contra bandit0 (login + comandos)
    ├── probe_tab.py          # captura bytes do servidor após Tab
    └── probe_backspace.py    # verifica forwarding de backspace pós-Tab
```

## Testes

```bash
# unitários (parser de escape, supressão de echo, filtro BEL)
python3 tests/test_units.py

# E2E contra bandit0 (precisa de rede pro overthewire.org)
python3 tests/drive_test.py
```

## Licença

MIT — ver [LICENSE](LICENSE).
