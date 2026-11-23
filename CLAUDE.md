# mycron 프로젝트 규칙

## CLI 실행

가상환경 바이너리를 사용한다:

```
.venv/bin/mycron <command>
```

pipx로 설치된 경우 `mycron <command>`로 직접 사용 가능.

## 작업 등록

```bash
.venv/bin/mycron add --name <이름> --cron "<cron표현식>" --command "<커맨드>"
```

- 커맨드에 Python 스크립트가 포함되면 `/opt/homebrew/bin/python3`의 절대경로를 사용한다
- 스크립트 경로도 절대경로로 지정한다

### 작업 설명

작업을 등록할 때는 어떤 작업인지 한 줄 설명을 함께 넣는다 (40자 안팎, "무엇을 · 어디로"). 상태판 카드의 이름 아래에 보인다.

```bash
.venv/bin/mycron add --name <이름> --cron "<cron표현식>" --command "<커맨드>" --description "<설명>"
.venv/bin/mycron describe <이름> "<설명>"   # 나중에 붙이거나 고칠 때. 설명 생략 시 지금 값 출력
```

- 설명은 스케줄과 무관해서 데몬을 리로드하지 않는다
- 여러 작업을 한꺼번에 채우는 초안은 `tools/fill_descriptions.sh` (`--dry-run`, `--force`)

### 경고(ALERT) 종료 코드

감시 작업이 "실행은 정상인데 사람이 봐야 할 상태"(예: 디스크 부족)를 알릴 때는 `exit 10` 으로 끝낸다.
텔레그램에 FAILED 대신 `⚠️ ... ALERT` 와 stdout 내용이 전송되고, `mycron logs` 에는 `ALERT` 로 표시된다.

## 스케줄러 데몬 관리

```bash
.venv/bin/mycron start    # 백그라운드 데몬 시작
.venv/bin/mycron status   # 실행 여부 확인
.venv/bin/mycron stop     # 데몬 중지
```

- 작업을 추가/변경한 뒤에는 데몬이 자동으로 SIGHUP을 받아 리로드된다
- 데몬이 꺼져 있으면 등록된 스케줄이 동작하지 않는다
- 시스템 재시작 후 데몬을 수동으로 다시 기동해야 한다

## 코드 수정 후 할 일

pipx로 설치되어 있으므로 재설치 후 데몬 재시작이 필요하다:

```bash
pipx install --force /Users/dysim/workspace/mycron
mycron stop && mycron start
```

## 로그 확인

```bash
.venv/bin/mycron logs <이름>          # 실행 로그 (기본 20건)
.venv/bin/mycron logs <이름> --limit 50
```

데몬 운영 로그: `~/.mycron/daemon.log`

## 웹 상태판 (읽기 전용)

```bash
.venv/bin/mycron web                 # http://127.0.0.1:8765/ (포그라운드)
mycron web-install                   # LaunchAgent com.dysim.mycron-web 로 상시 실행
mycron web-uninstall
```

- 코드는 `src/mycron/web.py`(서버 · 데이터), `webui.py`(화면), `redact.py`(가림), `crontext.py`(cron 식 풀이)
- DB 는 `database.connect_readonly()` 로만 연다. 쓰기 경로를 추가하지 않는다
- 헬스 체크는 `/healthz`. 게이트웨이 이름으로 붙일 때는 `--allow-host <이름>` 이 필요하다
- 로그: `~/.mycron/web-stdout.log`, `~/.mycron/web-stderr.log` (LaunchAgent 로 띄웠을 때)

## 텔레그램 알림 설정

`~/.mycron/config.toml` 파일에 설정:

```toml
[telegram]
bot_token = "..."
chat_id = "..."
```

## 런타임 파일 위치

| 파일 | 설명 |
|---|---|
| `~/.mycron/mycron.db` | 작업 정의 + 실행 로그 |
| `~/.mycron/mycron.pid` | 데몬 PID |
| `~/.mycron/daemon.log` | 데몬 운영 로그 |
| `~/.mycron/config.toml` | 텔레그램 등 설정 |
