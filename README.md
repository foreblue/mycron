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

## Flow 실행 엔진 변경

`plan-flow`, `dev-flow-all` 작업은 Claude 또는 Codex 중 하나를 선택해 실행할 수 있습니다.
현재 기본 엔진은 `~/.mycron/flow-engine` 파일에서 매 실행마다 읽습니다.

```bash
# Codex로 전환
echo codex > ~/.mycron/flow-engine

# Claude로 전환
echo claude > ~/.mycron/flow-engine

# 현재 설정 확인
cat ~/.mycron/flow-engine
```

일회성으로만 바꾸려면 `FLOW_ENGINE` 환경변수를 붙여 실행합니다.

```bash
FLOW_ENGINE=claude mycron run plan-flow
FLOW_ENGINE=codex mycron run dev-flow-all
```

스케줄러 데몬은 시작 시점의 환경변수만 상속하므로, 정기 실행 엔진을 바꿀 때는 환경변수보다 `~/.mycron/flow-engine` 파일을 사용하는 것이 안전합니다.

`dev-flow-all`은 각 리포의 `dev-flow`를 배포 비활성화 모드로 실행해 PR 머지까지만 처리합니다. 머지된 PR이 있으면 기본 브랜치를 최신화한 뒤 QA 후보 위치(`.`, `web`, `frontend`, `client`, `app`, `apps/web`)를 검사하고, E2E/회귀 명령이 있는 경우 `qa-flow` 회귀 게이트를 실행합니다.

QA 게이트가 통과하면 리포 루트의 실행 가능한 `deploy.sh`를 실행합니다. QA 게이트가 실패하면 배포를 막고 `qa-record`, `qa-regression`, `qa-failure`, `release-blocker` 라벨이 붙은 GitHub 이슈를 생성합니다. 산출물은 기본적으로 `~/workspace/qa-artifacts`에 저장되고, 이슈에는 단일 QA 보고서 링크를 사용할 수 있도록 `report.html`과 `issue-body.md`가 생성됩니다.

```bash
# QA 게이트 비활성화
QA_FLOW_AFTER_DEV_FLOW=0 mycron run dev-flow-all

# QA 통과 후 배포만 비활성화
DEPLOY_AFTER_QA=0 mycron run dev-flow-all

# QA 산출물 경로/URL 변경
QA_ARTIFACT_ROOT=~/workspace/qa-artifacts \
QA_ARTIFACT_BASE_URL=https://artifacts.deepheart.duckdns.org \
  mycron run dev-flow-all
```

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
