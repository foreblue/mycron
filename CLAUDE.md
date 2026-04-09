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
