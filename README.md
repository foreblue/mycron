# mycron

GUI 없이 동작하는 CLI cron 스타일 작업 스케줄러.

- cron 표현식으로 쉘 커맨드 스케줄 등록
- 실행 로그 SQLite 저장
- 성공/실패 결과 텔레그램 알림

## 설치

```bash
pipx install /path/to/mycron
```

## 빠른 시작

```bash
# 작업 등록
mycron add --name backup --cron "0 3 * * *" --command "/path/to/backup.sh"

# 스케줄러 시작
mycron start

# 상태 확인
mycron status
```

## 텔레그램 알림 설정

1. `@BotFather`에서 봇 생성 → `bot_token` 획득
2. 봇에게 메시지를 보낸 뒤 `chat_id` 확인:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. 설정 파일 생성:
   ```bash
   mkdir -p ~/.mycron
   cp config.example.toml ~/.mycron/config.toml
   # 편집기로 bot_token, chat_id 입력
   ```

## CLI 명령어

### 작업 관리

| 명령어 | 설명 |
|---|---|
| `mycron add --name NAME --cron EXPR --command CMD [--timeout SECONDS]` | 작업 등록 |
| `mycron remove NAME` | 작업 삭제 |
| `mycron list` | 활성 작업 목록 |
| `mycron list --all` | 전체 작업 목록 (비활성 포함) |
| `mycron enable NAME` | 작업 활성화 |
| `mycron disable NAME` | 작업 비활성화 |
| `mycron set-timeout NAME --timeout SECONDS` | 작업별 실행 제한 시간 변경 |

작업 timeout 기본값은 3600초입니다.

### 실행 및 로그

| 명령어 | 설명 |
|---|---|
| `mycron run NAME` | 즉시 실행 (테스트용) |
| `mycron logs` | 전체 실행 로그 |
| `mycron logs NAME` | 특정 작업 로그 |
| `mycron logs NAME --limit 50` | 로그 수 지정 |

### 데몬

| 명령어 | 설명 |
|---|---|
| `mycron start` | 백그라운드 데몬 시작 |
| `mycron start --foreground` | 포그라운드 실행 (디버그) |
| `mycron stop` | 데몬 중지 |
| `mycron status` | 데몬 상태 확인 |
| `mycron install` | macOS LaunchAgent 등록 (재시작 후 자동 실행) |
| `mycron uninstall` | LaunchAgent 해제 |

### 웹 상태판 (읽기 전용)

| 명령어 | 설명 |
|---|---|
| `mycron web` | 작업 · 실행 상태를 브라우저로 보는 웹서비스 실행 (기본 `127.0.0.1:8765`) |
| `mycron web --host HOST --port PORT` | 바인드 주소 · 포트 변경 |
| `mycron web --allow-host NAME` | 게이트웨이 이름처럼 IP · localhost 가 아닌 Host 로 접속할 때 허용 (여러 번 지정 가능) |
| `mycron web-install [--host --port --allow-host]` | 웹서비스를 LaunchAgent(`com.dysim.mycron-web`)로 등록 |
| `mycron web-uninstall` | 웹서비스 LaunchAgent 해제 |

- 보여 주는 것: 작업 목록(cron 식 풀이 · 활성 여부 · 옵션 · 커맨드), 다음 실행 예정, 마지막 실행 결과, 최근 실패, 지금 실행 중인 작업, 작업별 실행 기록과 출력 꼬리, 데몬 상태
- 경로: `/` 목록 · `/jobs/<이름>` 상세(`?n=50` 으로 기록 수 변경) · `/healthz` 헬스 체크(JSON) · `/api/status` 요약(JSON)
- 읽기 전용이다. DB 는 sqlite `mode=ro` 로만 열고, GET/HEAD 외 요청은 405 로 거절한다. 작업을 켜고 끄거나 실행하는 기능은 없다
- 커맨드와 출력에서 토큰 · 키처럼 보이는 값(`KEY=...`, `Bearer ...`, 긴 hex · base64 등)은 `[가림]` 으로 바꿔 보여 준다. 40자리 git SHA 같은 무해한 값도 가려질 수 있다
- "지금 실행 중" 은 데몬 로그(`daemon.log`)의 시작 · 종료 기록으로 판단한다. `mycron run` 으로 직접 돌린 실행은 잡히지 않는다
- 바깥 CDN · 글꼴 · 스크립트를 쓰지 않는다 (CSS · JS 인라인)
- 포트가 이미 쓰이고 있으면 띄우지 않고 오류로 끝난다

## Cron 표현식 예시

```
* * * * *        매분
0 7 * * *        매일 07:00
0 9 * * 1-5      평일 09:00
0 */6 * * *      6시간마다
30 18 * * 5      매주 금요일 18:30
```

## 파일 위치

| 파일 | 설명 |
|---|---|
| `~/.mycron/config.toml` | 텔레그램 등 설정 |
| `~/.mycron/mycron.db` | 작업 정의 + 실행 로그 |
| `~/.mycron/daemon.log` | 데몬 운영 로그 |
| `~/.mycron/mycron.pid` | 데몬 PID |

## 자동 시작 (macOS)

`mycron install`을 실행하면 macOS LaunchAgent로 등록되어 시스템 재시작 후에도 자동으로 실행됩니다.

```bash
mycron install      # LaunchAgent 등록
mycron uninstall    # LaunchAgent 해제
```

- `RunAtLoad`: 로그인 시 자동 시작
- `KeepAlive`: 비정상 종료 시 자동 재시작
- plist 위치: `~/Library/LaunchAgents/com.dysim.mycron.plist`

## 코드 수정 후

```bash
pipx install --force /path/to/mycron
mycron uninstall && mycron install   # LaunchAgent 재등록
```
