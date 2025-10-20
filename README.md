# 🤖 키움증권 자동매매 시스템

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

키움증권 OpenAPI를 활용한 국내 주식 자동매매 시스템입니다. Playwright로 종목을 선정하고, REST API로 주문을 실행하며, WebSocket으로 실시간 시세를 모니터링하여 자동으로 매도합니다.

## ✨ 주요 기능

- 🔍 **실시간 종목 스크래핑**: Playwright를 이용한 장 시작 후 실시간 모니터링 (09:00~09:10)
- 📊 **자동 매수**: 매수 조건 충족 시 시장가 주문 자동 실행
- 💰 **자동 매도**: 목표 수익률 도달 시 자동 매도 (환경변수로 설정 가능, 기본값: 1.0%)
- 🔄 **무한 연결 유지**: WebSocket PING/PONG 처리로 연결 끊김 없음
- 🛡️ **안전장치**: 일일 1회 매수 제한, 중복 매도 방지
- 📱 **브라우저 독립**: 매수 후 브라우저 없이도 매도 모니터링 가능

## 🚀 빠른 시작

### 1. 설치

```bash
# 저장소 클론
git clone https://github.com/ralph0830/kiwoom-stock.git
cd kiwoom-stock

# UV 패키지 매니저 설치 (선택사항)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 의존성 설치
uv sync
```

### 2. 환경 변수 설정

`.env` 파일을 생성하고 키움증권 API 키를 입력하세요:

```bash
# 모의투자/실전투자 선택
USE_MOCK=false

# 실전투자 API 키
KIWOOM_APP_KEY=your_real_app_key
KIWOOM_SECRET_KEY=your_real_secret_key

# 모의투자 API 키
KIWOOM_MOCK_APP_KEY=your_mock_app_key
KIWOOM_MOCK_SECRET_KEY=your_mock_secret_key

# 계좌 정보
ACCOUNT_NO=12345678-01
MAX_INVESTMENT=100000
TARGET_PROFIT_RATE=1.0       # 목표 수익률 (%) - 기본값: 1.0%
```

### 3. 실행

```bash
# 자동매매 시작
uv run python auto_trading.py

# 또는 일반 Python으로 실행
python auto_trading.py
```

## 📖 사용 방법

### 기본 워크플로우

1. **시스템 시작** (09:00)
   - 브라우저 자동 실행
   - 오늘 매수 이력 확인

2. **매수 모니터링** (09:00~09:10)
   - 0.5초마다 종목 데이터 스크래핑
   - 매수가 도달 시 자동 주문

3. **매도 모니터링** (무제한)
   - WebSocket 실시간 시세 수신
   - 목표 수익률 도달 시 자동 매도 (기본값: 1.0%, 설정 가능)

### 재시작 시 (매수 완료 후)

시스템이 종료되어도 `daily_trading_lock.json`에서 매수 정보를 복원하여 자동으로 매도 모니터링을 재개합니다.

```bash
# 브라우저 없이 매도 모니터링만 시작
uv run python auto_trading.py
```

## 🏗️ 시스템 구조

```
kiwoom-stock/
├── auto_trading.py          # 메인 자동매매 시스템
├── kiwoom_order.py          # REST API 주문 처리
├── kiwoom_websocket.py      # WebSocket 실시간 시세
├── daily_trading_lock.json  # 일일 매수 이력 (자동 생성)
├── trading_results/         # 매매 결과 기록 (자동 생성)
├── .env                     # 환경 변수 (직접 작성 필요)
├── .gitignore               # Git 제외 파일
├── pyproject.toml           # 프로젝트 설정
└── README.md                # 본 문서
```

## 🔧 핵심 컴포넌트

### AutoTradingSystem (`auto_trading.py`)

전체 자동매매 프로세스를 오케스트레이션합니다.

```python
# 주요 메서드
- start_auto_trading()        # 브라우저 + WebSocket 매매 시작
- start_sell_monitoring()     # WebSocket 전용 매도 모니터링
- execute_auto_buy()          # 자동 매수 주문
- execute_auto_sell()         # 자동 매도 주문
- on_price_update()           # 실시간 시세 콜백
```

### KiwoomOrderAPI (`kiwoom_order.py`)

REST API를 통한 주문 실행 및 인증을 담당합니다.

```python
# 주요 메서드
- get_access_token()          # OAuth2 토큰 발급
- place_market_buy()          # 시장가 매수
- place_market_sell()         # 시장가 매도
```

### KiwoomWebSocket (`kiwoom_websocket.py`)

WebSocket을 통한 실시간 시세 수신 및 연결 관리를 담당합니다.

```python
# 주요 메서드
- connect()                   # WebSocket 연결 및 로그인
- register_stock()            # 실시간 시세 등록
- receive_loop()              # 데이터 수신 + 자동 재연결
```

## 📊 매매 흐름도

```
[시스템 시작]
    ↓
[매수 이력 확인]
    ↓
┌─────────┬─────────┐
│ 매수 O  │ 매수 X  │
└─────────┴─────────┘
    ↓           ↓
[WebSocket] [Playwright]
    ↓           ↓
[매도 감시] [매수 감시]
    ↓           ↓
    │       [주문 실행]
    │           ↓
    └───────[WebSocket]
                ↓
            [매도 감시]
                ↓
            [목표 달성]
                ↓
            [매도 실행]
                ↓
            [시스템 종료]
```

## 🛡️ 안전장치

### 1. 일일 1회 매수 제한

```python
# daily_trading_lock.json으로 날짜 추적
{
  "last_trading_date": "20251017",
  "stock_code": "051780",
  "buy_price": 1625,
  "quantity": 1230
}
```

### 2. 중복 매도 방지

```python
# 이중 플래그 시스템
if self.sell_executed:
    return  # 이미 매도함

self.sell_executed = True  # 즉시 플래그 설정
# 매도 주문 실행
```

### 3. WebSocket 연결 안정성

```python
# PING 응답 처리
if data.get("trnm") == "PING":
    await self.websocket.send(message)

# 자동 재연결
if not self.is_connected:
    await asyncio.sleep(2)
    await self.connect()
```

## ⚙️ 환경별 설정

### 모의투자

```bash
USE_MOCK=true
```

- URL: `https://mockapi.kiwoom.com`
- WebSocket: `wss://mockapi.kiwoom.com:10000`
- 지원 거래소: KRX만

### 실전투자

```bash
USE_MOCK=false
```

- URL: `https://api.kiwoom.com`
- WebSocket: `wss://api.kiwoom.com:10000`
- 지원 거래소: 전체

## 📈 성능 지표

| 항목 | 값 |
|------|-----|
| 웹 스크래핑 주기 | 0.5초 |
| WebSocket 연결 유지 | 무제한 |
| 재연결 대기 시간 | 2초 |
| API 응답 시간 | ~100ms |
| 주문 실행 시간 | ~200ms |

## 🐛 트러블슈팅

### WebSocket 40초 후 연결 끊김

**원인**: PING 메시지 미응답

**해결**: 이미 PING/PONG 처리 구현되어 있음 (kiwoom_websocket.py:171-176)

### 브라우저 닫으면 매도 안됨

**원인**: 브라우저 종료 시 시스템 종료

**해결**: `daily_trading_lock.json`에서 매수 정보 복원하여 WebSocket만으로 매도 모니터링

### 하루에 여러 번 매수됨

**원인**: 매수 이력 추적 없음

**해결**: `check_today_trading_done()` 함수로 일일 1회 체크

## 📚 상세 문서

더 자세한 기술 문서는 [PRD.md](PRD.md)를 참고하세요.

- API 명세
- 데이터 구조
- 알고리즘 상세
- 에러 처리
- 성능 최적화

## ⚠️ 주의사항

### 투자 경고

> **본 시스템은 교육 및 연구 목적으로 제작되었습니다.**
>
> - 실제 투자 시 발생하는 모든 손실은 사용자 본인의 책임입니다
> - 자동매매는 예상치 못한 손실을 초래할 수 있습니다
> - 충분한 테스트 없이 실전투자 사용을 권장하지 않습니다
> - **모의투자로 충분히 검증한 후 실전투자를 고려하세요**

### 법적 고지

- 키움증권 API 이용약관 준수 필수
- 과도한 API 호출 자제
- 개인정보 보호법 준수 (API 키 노출 금지)
- `.env` 파일을 절대 공개하지 마세요

## 🔐 보안

### API 키 관리

```bash
# .env 파일은 절대 Git에 커밋하지 마세요
# .gitignore에 이미 포함되어 있습니다

.env
daily_trading_lock.json
trading_results/
*.log
```

### 민감 정보 제외

- API 키 및 Secret
- 계좌 번호
- 매매 기록
- 로그 파일

## 🛠️ 개발 환경

```toml
[project]
name = "kiwoom-stock"
version = "1.0.0"
requires-python = ">=3.10"

dependencies = [
    "playwright>=1.42.0",
    "websockets>=12.0",
    "requests>=2.31.0",
    "python-dotenv>=1.0.0",
]
```

## 🔄 업데이트 방법

프로젝트가 업데이트되면 다음 명령어로 최신 버전을 받을 수 있습니다:

```bash
# 1. 현재 변경사항 확인 (선택사항)
git status

# 2. 로컬 변경사항이 있다면 스태시에 저장 (선택사항)
git stash

# 3. 최신 코드 가져오기
git pull origin master

# 4. 의존성 업데이트 (필요시)
uv sync

# 5. 스태시한 변경사항 복원 (2번을 실행했다면)
git stash pop
```

### 업데이트 시 주의사항

- `.env` 파일은 업데이트되지 않습니다 (개인 설정이므로 안전함)
- 새로운 환경변수가 추가된 경우 `.env` 파일에 수동으로 추가해야 합니다
- `daily_trading_lock.json`과 `trading_results/`는 업데이트에 영향받지 않습니다

### 특정 버전으로 되돌리기

```bash
# 커밋 히스토리 확인
git log --oneline

# 특정 커밋으로 되돌리기 (예: abc1234)
git checkout abc1234

# 최신 버전으로 돌아가기
git checkout master
```

## 🤝 기여하기

버그 리포트나 기능 제안은 [Issues](https://github.com/ralph0830/kiwoom-stock/issues)에 등록해 주세요.

## 📄 라이선스

MIT License - 자유롭게 사용, 수정, 배포 가능합니다.

## 🙏 감사의 말

- [키움증권](https://www.kiwoom.com/) - OpenAPI 제공
- [Playwright](https://playwright.dev/) - 브라우저 자동화
- [websockets](https://websockets.readthedocs.io/) - WebSocket 라이브러리

---

**개발**: Ralph
**도움**: Claude Code
**마지막 업데이트**: 2025-10-17
**버전**: 1.0.0
