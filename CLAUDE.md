# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

키움증권 REST OpenAPI와 WebSocket을 활용한 **실시간 자동매매 시스템**입니다.
웹페이지에서 종목을 실시간으로 스크래핑하여 자동 매수하고, WebSocket으로 실시간 시세를 모니터링하여 목표 수익률 도달 시 자동으로 매도합니다.

### 주요 기능
- 🔍 **실시간 종목 감시**: Playwright로 웹페이지 스크래핑 (0.5초 주기)
- 💰 **자동 매수**: 시장가 즉시 매수 (REST API)
- 📈 **자동 익절**: 목표 수익률 도달 시 한 틱 아래 가격으로 지정가 매도 (100% 전량)
- 🚨 **자동 손절**: 손절 수익률 도달 시 시장가 즉시 매도 (100% 전량)
- ⏰ **일일 강제 청산**: 지정 시간(기본 15:19)에 수익/손실 관계없이 100% 전량 시장가 매도
- 🔄 **WebSocket 실시간 시세**: PING/PONG 처리로 무제한 연결 유지
- 🛡️ **안전장치**: 일일 1회 매수 제한, 중복 매도 방지
- 📊 **세션 복원**: 프로그램 재시작 시 계좌 잔고에서 매수 정보 복원
- 💯 **100% 전량 매도**: 계좌 잔고 실시간 조회로 수동 매수 포함 전량 매도

## Development Environment

**Package Manager**: UV (Python package manager)
- 이 프로젝트는 `uv`를 사용하여 의존성을 관리합니다
- `pyproject.toml`에 의존성이 정의되어 있습니다
- Windows (win_amd64) 환경에서 실행됩니다

**Python Version**: >=3.10

**주요 의존성**:
- `playwright>=1.55.0`: 브라우저 자동화
- `websockets>=12.0`: WebSocket 실시간 통신
- `requests>=2.31.0`: REST API 호출
- `python-dotenv>=1.0.1`: 환경 변수 관리
- `fastapi>=0.109.0`: API 서버 (unused)
- `beautifulsoup4>=4.12.0`: HTML 파싱 (unused)

## Common Commands

### 패키지 설치
```bash
uv sync
```

### 프로그램 실행
```bash
# 자동매매 시스템 시작
uv run python auto_trading.py

# 또는 일반 Python으로 실행
python auto_trading.py
```

### 의존성 추가
```bash
uv add <package-name>
```

## Environment Variables

프로젝트 루트에 `.env` 파일이 필요하며, 다음 환경변수를 설정해야 합니다:

```env
# 모의투자/실전투자 선택 (true: 모의투자, false: 실전투자)
USE_MOCK=false

# 디버그 모드 (true: 실시간 시세 계속 출력, false: 출력 안함)
DEBUG=true

# 실전투자 API KEY
KIWOOM_APP_KEY=your_real_app_key
KIWOOM_SECRET_KEY=your_real_secret_key

# 모의투자 API KEY
KIWOOM_MOCK_APP_KEY=your_mock_app_key
KIWOOM_MOCK_SECRET_KEY=your_mock_secret_key

# 자동매매 설정
ACCOUNT_NO=12345678-01       # 계좌번호
MAX_INVESTMENT=2000000       # 최대 투자금액 (원)
TARGET_PROFIT_RATE=1.0       # 목표 수익률 (%) - 기본값: 1.0%

# 서버 설정 (사용하지 않음 - unused)
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
WEBSOCKET_PORT=8765

# 모니터링 설정
MARKET_OPEN_TIME=09:00       # 장 시작 시간
MARKET_CLOSE_TIME=15:30      # 장 마감 시간

# 매수 가능 시간 설정
BUY_START_TIME=09:00         # 매수 시작 시간
BUY_END_TIME=09:10           # 매수 종료 시간

# 매도 모니터링 설정
ENABLE_SELL_MONITORING=true  # 자동 매도 모니터링 활성화 (true: 활성화, false: 비활성화)

# 손절 설정
ENABLE_STOP_LOSS=true        # 손절 모니터링 활성화 (true: 활성화, false: 비활성화)
STOP_LOSS_RATE=-2.5          # 손절 수익률 (%) - 기본값: -2.5%

# 일일 강제 청산 설정
ENABLE_DAILY_FORCE_SELL=true # 일일 강제 청산 활성화 (true: 활성화, false: 비활성화)
DAILY_FORCE_SELL_TIME=15:19  # 강제 청산 시간 (기본값: 15:19 - 장마감 11분 전)

# 실시간 체결 정보 검증 설정 (v1.3.0)
ENABLE_LAZY_VERIFICATION=false  # 실시간 체결 정보 자동 검증 (true: 개선 모드, false: 기존 모드)
```

**중요**: `.env` 파일은 `.gitignore`에 포함되어야 하며, 실제 키 값은 절대 커밋하지 마세요.

### 실시간 체결 정보 검증 (ENABLE_LAZY_VERIFICATION)

**목적**: 시장가 매수 시 추정값과 실제 체결가의 차이를 자동으로 보정

**기존 방식 (false - 기본값):**
- 시장가 주문 시점의 현재가를 매수가로 사용 (추정값)
- 빠른 매도 판단 가능 (즉시 모니터링)
- 실제 체결가와 차이 발생 가능 (보통 0.5~1% 차이)

**개선 방식 (true):**
- 첫 실시간 시세 수신 시 계좌 조회로 실제 체결가 확인
- 정확한 수익률 계산 (실제 평균 매입단가 기준)
- 약 0.2초 추가 지연 발생

**실측 데이터 (2025-10-23 테스트):**
```
테스트 종목: 043260 (1주 시장가 매수)
- 매수 주문 API 응답: 0.092초
- 체결 확인까지 총 시간: 0.776초
- 계좌 조회 API 응답: 0.183초
- 예상 매수가: 1,698원
- 실제 매입단가: 1,709원
- 가격 차이: +11원 (+0.65%)
```

**권장 설정:**
- **빠른 대응 우선** (단기 급등주, 빠른 매도 필요): `false`
- **정확한 수익률 우선** (목표 수익률 정확히 달성): `true`

## Architecture

### 프로젝트 구조

```
stock/
├── auto_trading.py          # 메인 자동매매 시스템
├── kiwoom_order.py          # REST API 주문 처리
├── kiwoom_websocket.py      # WebSocket 실시간 시세
├── .env                     # 환경 변수 (직접 작성 필요)
├── pyproject.toml           # 프로젝트 설정 및 의존성
├── README.md                # 프로젝트 설명서
├── CLAUDE.md                # 본 문서
│
├── daily_trading_lock.json  # 일일 매수 이력 (자동 생성)
├── trading_results/         # 매매 결과 기록 (자동 생성)
├── auto_trading.log         # 자동매매 로그 (자동 생성)
│
└── unused/                  # 사용하지 않는 레거시 코드
    ├── main.py              # 구 버전 1분봉 조회 스크립트
    ├── live_monitor.py      # 구 버전 실시간 모니터링
    ├── minute.py            # 구 버전 분봉 데이터 처리
    └── backend/             # 구 버전 백엔드 코드
```

### 주요 컴포넌트

#### 1. AutoTradingSystem (`auto_trading.py`)
전체 자동매매 프로세스를 오케스트레이션하는 메인 시스템

**주요 메서드**:
- `start_browser()`: Playwright 브라우저 시작 및 페이지 로드
- `check_stock_data()`: 웹페이지에서 종목 데이터 스크래핑
- `monitor_and_trade()`: 0.5초마다 종목 감시 (매수 전)
- `execute_auto_buy()`: 시장가 즉시 매수 주문
- `start_websocket_monitoring()`: WebSocket 실시간 시세 모니터링 시작
- `on_price_update()`: 실시간 시세 수신 콜백 (WebSocket)
- `execute_auto_sell()`: 목표 수익률 도달 시 자동 익절 (100% 전량 지정가 매도)
- `execute_stop_loss()`: 손절 수익률 도달 시 자동 손절 (100% 전량 시장가 매도)
- `execute_daily_force_sell()`: 일일 강제 청산 실행 (100% 전량 시장가 매도)
- `is_force_sell_time()`: 강제 청산 시간 도달 여부 확인
- `price_polling_loop()`: REST API로 1분마다 현재가 조회 (백업)
- `load_today_trading_info()`: 계좌 잔고에서 매수 정보 복원
- `cleanup()`: 리소스 정리 (WebSocket, 브라우저 종료)

**주요 속성**:
```python
self.buy_info = {
    "stock_code": None,                                    # 종목코드
    "stock_name": None,                                    # 종목명
    "buy_price": 0,                                        # 매수가
    "quantity": 0,                                         # 매수 수량
    "target_profit_rate": 0.01                             # 목표 수익률 (환경변수 TARGET_PROFIT_RATE에서 로드, 기본값: 1%)
}
```

#### 2. KiwoomOrderAPI (`kiwoom_order.py`)
REST API를 통한 주문 실행 및 인증 담당

**주요 메서드**:
- `get_access_token()`: OAuth2 토큰 발급
- `place_market_buy_order()`: 시장가 매수 주문
- `place_limit_buy_order()`: 지정가 매수 주문
- `place_limit_sell_order()`: 지정가 매도 주문 (익절용)
- `place_market_sell_order()`: 시장가 매도 주문 (손절용)
- `get_current_price()`: 현재가 조회 (REST API)
- `get_account_balance()`: 계좌 잔고 및 보유종목 조회
- `calculate_order_quantity()`: 최대 투자금액 기준 매수 수량 계산

**유틸리티 함수**:
- `parse_price_string()`: 가격 문자열을 정수로 변환 ("75,000원" → 75000)
- `get_tick_size()`: 주가별 호가 단위 계산 (1원 ~ 1000원)
- `calculate_sell_price()`: 목표 수익률 도달 시 매도가 계산 (한 틱 아래)

**호가 단위 (틱)**:
```python
price < 1,000      → 1원
price < 5,000      → 5원
price < 10,000     → 10원
price < 50,000     → 50원
price < 100,000    → 100원
price < 500,000    → 500원
price >= 500,000   → 1,000원
```

#### 3. KiwoomWebSocket (`kiwoom_websocket.py`)
WebSocket을 통한 실시간 시세 수신 및 연결 관리

**주요 메서드**:
- `connect()`: WebSocket 연결 및 로그인
- `register_stock()`: 실시간 시세 등록 (0A: 주식기세)
- `unregister_stock()`: 실시간 시세 해지
- `receive_loop()`: 데이터 수신 루프 + 자동 재연결
- `_handle_realtime_data()`: 실시간 데이터 파싱 및 콜백 호출
- `get_current_price()`: 캐시된 현재가 조회
- `close()`: WebSocket 연결 종료

**특징**:
- PING/PONG 처리로 연결 끊김 방지 (40초 타임아웃 해결)
- 자동 재연결 기능 (연결 끊김 시 2초 후 재시도)
- 종목별 콜백 함수 등록 가능
- 현재가 캐시 기능

### API Integration

#### REST API
- **Base URL (실전)**: `https://api.kiwoom.com`
- **Base URL (모의)**: `https://mockapi.kiwoom.com`
- **인증 방식**: OAuth2 Client Credentials (Bearer Token)

**주요 엔드포인트**:
- `POST /oauth2/token`: Access Token 발급
- `POST /api/dostk/ordr`: 주문 실행 (매수/매도)
  - `kt10000`: 주식 매수주문
  - `kt10001`: 주식 매도주문
- `POST /api/dostk/stkinfo`: 주식 현재가 조회 (ka10001)
- `POST /api/dostk/acnt`: 계좌 잔고 조회 (ka01690)

#### WebSocket
- **URL (실전)**: `wss://api.kiwoom.com:10000/api/dostk/websocket`
- **URL (모의)**: `wss://mockapi.kiwoom.com:10000/api/dostk/websocket`
- **인증**: Bearer Token (헤더)

**전문 타입**:
- `LOGIN`: 로그인
- `REG`: 실시간 시세 등록
- `REMOVE`: 실시간 시세 해지
- `REAL`: 실시간 데이터 수신
- `PING`: Heartbeat (연결 유지)
- `SYSTEM`: 시스템 메시지

**실시간 시세 타입**:
- `0A`: 주식기세 (현재가, 등락률 등)

### Data Flow

#### 매수 플로우
1. Playwright로 브라우저 시작 (`https://live.today-stock.kr/`)
2. 0.5초마다 웹페이지 스크래핑으로 종목 감시
3. 종목 포착 시 현재가 기준 매수 수량 계산
4. REST API로 시장가 매수 주문 실행
5. 매수 정보를 `daily_trading_lock.json`에 저장 (일일 1회 제한)
6. WebSocket으로 실시간 시세 모니터링 시작

#### 매도 플로우 (익절)
1. WebSocket 실시간 시세 수신 (콜백: `on_price_update()`)
2. 수익률 계산: `(현재가 - 매수가) / 매수가`
3. 목표 수익률 도달 확인 (환경변수 `TARGET_PROFIT_RATE`)
4. 계좌 잔고에서 실제 보유 수량 및 평균 매입단가 조회
5. 매도가 계산: `목표가 - 1틱`
6. REST API로 지정가 매도 주문 실행 (100% 전량)
7. 매도 결과를 `trading_results/` 디렉토리에 저장

#### 손절 플로우
1. WebSocket 실시간 시세 수신 (콜백: `on_price_update()`)
2. 손실률 계산: `(현재가 - 매수가) / 매수가`
3. 손절 수익률 도달 확인 (환경변수 `STOP_LOSS_RATE`, 기본값: -2.5%)
4. 계좌 잔고에서 실제 보유 수량 및 평균 매입단가 조회
5. REST API로 시장가 매도 주문 실행 (100% 전량 즉시 체결)
6. 손절 결과를 `trading_results/` 디렉토리에 저장

#### 일일 강제 청산 플로우
1. WebSocket 실시간 시세 수신 (콜백: `on_price_update()`)
2. 현재 시간 확인 및 강제 청산 시간 도달 체크 (환경변수 `DAILY_FORCE_SELL_TIME`, 기본값: 15:19)
3. 강제 청산 시간 도달 시 즉시 실행 (수익/손실 관계없이)
4. 계좌 잔고에서 실제 보유 수량 및 평균 매입단가 조회
5. REST API로 시장가 매도 주문 실행 (100% 전량 즉시 체결)
6. 현재가 조회 후 수익률 계산
7. 강제 청산 결과를 `trading_results/` 디렉토리에 저장

**우선순위**: 강제 청산 > 손절 > 익절 (시간 도달 시 무조건 강제 청산 실행)

#### 세션 복원 플로우
1. 프로그램 시작 시 계좌 잔고 조회 (REST API)
2. 보유 종목이 있으면 매수 정보 복원
3. 브라우저 없이 WebSocket 매도 모니터링만 시작
4. 목표 수익률 도달 시 자동 매도

## Important Notes

### 매수/매도 로직

**매수**:
- 시장가 주문으로 즉시 체결
- 최대 투자금액 기준으로 수량 계산
- 안전 마진 2% 적용 (시장가 체결가 변동 대비)
- 일일 1회만 매수 (날짜 기준)

**익절 매도**:
- 목표 수익률: 환경변수 `TARGET_PROFIT_RATE`로 설정 (기본값: 1.0%)
- 매도 방식: 지정가 (목표가에서 한 틱 아래)
- 100% 전량 매도: 계좌 잔고 실시간 조회로 수동 매수 포함
- 평균 매입단가 자동 반영
- 중복 매도 방지: `sell_executed` 플래그

**손절 매도**:
- 손절 수익률: 환경변수 `STOP_LOSS_RATE`로 설정 (기본값: -2.5%)
- 매도 방식: 시장가 (즉시 체결)
- 100% 전량 매도: 계좌 잔고 실시간 조회로 수동 매수 포함
- 평균 매입단가 자동 반영
- 우선순위: 손절이 익절보다 우선 체크
- 활성화/비활성화: 환경변수 `ENABLE_STOP_LOSS`로 제어

**일일 강제 청산**:
- 강제 청산 시간: 환경변수 `DAILY_FORCE_SELL_TIME`로 설정 (기본값: 15:19)
- 매도 방식: 시장가 (즉시 체결)
- 100% 전량 매도: 계좌 잔고 실시간 조회로 수동 매수 포함
- 평균 매입단가 자동 반영
- 우선순위: 강제 청산이 손절/익절보다 최우선
- 활성화/비활성화: 환경변수 `ENABLE_DAILY_FORCE_SELL`로 제어
- 목적: 장마감 전 안전한 포지션 청산으로 익일 갭 리스크 방지

**익절 예시**:
```
매수가: 10,000원
목표 수익률: 1%
목표가: 10,100원
호가 단위: 10원 (10,000원대)
매도 주문가: 10,090원 (목표가 - 1틱)
```

**손절 예시**:
```
평균 매입단가: 10,000원 (자동매수 100주 + 수동매수 50주)
현재가: 9,750원
손실률: -2.5%
손절 매도: 시장가 즉시 매도 (150주 전량)
```

**강제 청산 예시**:
```
현재 시각: 15:19
강제 청산 시간 도달
보유 종목: 테스트종목 (150주)
현재 수익률: +0.5% (목표 1% 미달)
→ 수익/손실 관계없이 시장가 즉시 매도 (150주 전량)
목적: 장마감 전 포지션 청산으로 익일 갭 리스크 방지
```

### WebSocket 연결 관리

**PING/PONG 처리**:
```python
if data.get("trnm") == "PING":
    await self.websocket.send(message)  # 그대로 응답
    logger.info("💓 PING 응답 전송 (연결 유지)")
```

**자동 재연결**:
- 연결 끊김 감지 시 2초 후 재연결
- 기존 등록된 종목 자동 재등록
- 콜백 함수 유지

**연결 설정**:
```python
self.websocket = await websockets.connect(
    self.ws_url,
    additional_headers={"authorization": f"Bearer {token}"},
    ping_interval=None,   # 클라이언트 측 ping 비활성화
    ping_timeout=None     # 서버 ping만 사용
)
```

### 안전장치

**1. 일일 1회 매수 제한**:
```json
// daily_trading_lock.json
{
  "last_trading_date": "20251020",
  "trading_time": "2025-10-20 09:02:15",
  "stock_code": "051780",
  "stock_name": "테스트종목",
  "buy_price": 10000,
  "quantity": 200
}
```

**2. 중복 매도 방지**:
```python
if self.sell_executed:
    return  # 이미 매도함

self.sell_executed = True  # 즉시 플래그 설정
# 매도 주문 실행
```

**3. 빈 계좌 잔고 처리**:
- API 응답에서 빈 딕셔너리 필터링
- 종목코드가 없는 항목 제외
- 안전한 타입 변환 (`int(value or 0)`)

### Error Handling

**REST API**:
- 요청 실패 시 `requests.raise_for_status()`로 예외 발생
- Access Token 발급 실패 시 `ValueError` 발생
- 모든 API 함수는 성공 여부를 포함한 딕셔너리 반환

**WebSocket**:
- 연결 끊김 시 자동 재연결 (최대 무제한)
- JSON 파싱 실패 시 로그만 남기고 계속 진행
- 중복 접속 감지 (R10001) 시 재연결 대기

**브라우저**:
- 페이지 닫힘 감지 시 모니터링 중단
- 데이터 조회 실패 시 예외 로깅 후 계속 진행

### 성능 최적화

**리소스 사용**:
- 웹 스크래핑: 0.5초 주기 (빠른 감지)
- 대기 로그: 10초마다 한 번만 출력 (로그 과다 방지)
- DEBUG 모드: 실시간 시세 1초마다 출력 (선택적)
- REST API 폴링: 1분마다 현재가 조회 (백업용)

**메모리 관리**:
- 브라우저: 매수 완료 후 종료 가능 (선택적)
- WebSocket: 캐시된 현재가만 저장
- 로그 파일: `auto_trading.log`에 기록

**병렬 처리**:
- WebSocket 수신: 백그라운드 태스크
- REST API 폴링: 백그라운드 태스크 (WebSocket 백업)

## Testing & Development

### 테스트 방법

**1. 모의투자 모드**:
```bash
# .env 파일 설정
USE_MOCK=true

# 실행
uv run python auto_trading.py
```

**2. 개별 컴포넌트 테스트**:
```bash
# 주문 API 테스트
uv run python kiwoom_order.py

# WebSocket 테스트
uv run python kiwoom_websocket.py
```

**3. DEBUG 모드**:
```bash
# .env 파일 설정
DEBUG=true

# 실시간 시세가 1초마다 콘솔에 출력됨
```

### 주요 로그 메시지

**매수 플로우**:
```
🚀 자동매매 시스템 시작...
📊 실제 계좌 잔고 조회 중...
ℹ️ 보유 종목이 없습니다.
✅ 페이지 로드 완료!
🔍 종목 감시 시작...
⏳ 종목 대기 중...
🎯 종목 포착! [종목명]
✅ 시장가 매수 주문 성공!
📈 WebSocket 실시간 시세 모니터링 시작 (목표: 1%)
```

**익절 플로우**:
```
📊 실시간 시세 정보 (WebSocket)
수익률: +1.05% (목표: +1.00%, 손절: -2.50%)
🎯 목표 수익률 1% 도달! 자동 매도를 시작합니다
📊 계좌 잔고에서 실제 보유 정보를 조회합니다...
✅ 실제 보유 정보 확인:
   보유 수량: 150주
   평균 매입단가: 10,667원
💰 매도 수량: 150주 (100% 전량)
💰 매도 주문가: 10,780원 (목표가에서 한 틱 아래)
✅ 지정가 매도 주문 성공!
✅ 자동 매도 완료!
```

**손절 플로우**:
```
📊 실시간 시세 정보 (WebSocket)
수익률: -2.71% (목표: +1.00%, 손절: -2.50%)
🚨 손절 조건 도달! (-2.5% 이하)
매수가: 10,000원
현재가: 9,730원
손실률: -2.71%
📊 계좌 잔고에서 실제 보유 수량을 조회합니다...
✅ 실제 보유 수량 확인: 150주
💰 손절 매도 수량: 150주 (100% 전량 시장가)
✅ 시장가 매도 주문 성공! (손절)
✅ 손절 매도 완료!
```

**강제 청산 플로우**:
```
📊 실시간 시세 정보 (WebSocket)
수익률: +0.50% (목표: +1.00%, 손절: -2.50%)
================================================================================
⏰ 강제 청산 시간 도달! (15:19)
💰 보유 종목을 100% 전량 시장가 매도합니다
================================================================================
📊 계좌 잔고에서 실제 보유 수량을 조회합니다...
✅ 실제 보유 정보 확인:
   보유 수량: 150주
   평균 매입단가: 10,000원
💰 강제 청산 수량: 150주 (100% 전량 시장가)
✅ 시장가 매도 주문 성공!
✅ 강제 청산 완료!
💾 강제 청산 결과 저장: trading_results/20251020_151900_테스트종목_강제청산결과.json
```

**재시작 (세션 복원)**:
```
📊 실제 계좌 잔고 조회 중...
📥 실제 계좌 보유 종목 확인
종목명: 테스트종목
종목코드: 051780
매입단가: 10,000원
보유수량: 200주
✅ 보유 종목이 있습니다. 매도 모니터링만 시작합니다.
📈 WebSocket 매도 모니터링 시작 (목표: 1%)
```

## Git Workflow

### .gitignore에 포함된 파일
```
# 환경 및 인증
.env

# 자동 생성 파일
daily_trading_lock.json
trading_results/
auto_trading.log

# Python 관련
__pycache__/
*.py[cod]
.venv/

# Playwright 관련
.playwright-mcp/
```

### 커밋하지 말아야 할 것
- API 키 및 Secret (`.env`)
- 계좌 번호
- 매매 기록 (`trading_results/`)
- 일일 매수 이력 (`daily_trading_lock.json`)
- 로그 파일 (`*.log`)

## Troubleshooting

### WebSocket 40초 후 연결 끊김
- **원인**: PING 메시지 미응답
- **해결**: `kiwoom_websocket.py:171-176`에서 PING/PONG 처리 구현됨

### 브라우저 닫으면 매도 안됨
- **원인**: 브라우저 종료 시 시스템 종료
- **해결**: 계좌 잔고에서 매수 정보 복원하여 WebSocket만으로 매도 모니터링

### 하루에 여러 번 매수됨
- **원인**: 매수 이력 추적 없음
- **해결**: `check_today_trading_done()` 함수로 일일 1회 체크

### 계좌 잔고 조회 실패 (빈 문자열 에러)
- **원인**: 보유종목이 없을 때 빈 딕셔너리 반환
- **해결**: `kiwoom_order.py:425-431`에서 종목코드 필터링 및 안전한 타입 변환

### 실시간 체결 정보 검증 오류 (`save_today_trading_lock` 없음)
- **원인**: 메서드 이름 불일치 (`save_today_trading_lock()` vs `record_today_trading()`)
- **해결**: `auto_trading.py:564`에서 올바른 메서드 `record_today_trading()` 호출로 수정

### 매도 타이밍 차이 (ENABLE_LAZY_VERIFICATION=true)
- **현상**: 추정값 기준으로는 목표 도달했지만 실제값으로는 미달
- **원인**: 시장가 체결가가 현재가보다 높음 (평균 0.5~1% 차이)
- **해결**: 필요시 `ENABLE_LAZY_VERIFICATION=false`로 변경하여 빠른 대응 우선

## Testing

### 매수 체결 시간 측정 테스트

**테스트 파일**: `test_buy_timing.py`

**실행 방법**:
```bash
uv run python test_buy_timing.py
```

**측정 항목**:
- Access Token 발급 시간
- 현재가 조회 시간
- 매수 주문 API 응답 시간
- 체결 확인까지 총 소요 시간
- 계좌 조회 API 응답 시간

**실측 결과 (2025-10-23)**:
```
테스트 종목: 043260 (1주 시장가 매수)
실행 시간: 15:15:12

타이밍 통계:
- Access Token 발급: 0.094초
- 현재가 조회: 0.104초
- 매수 주문 API 응답: 0.092초
- 체결 확인까지 총 시간: 0.776초 ⭐
- 계좌 조회 API 응답: 0.183초
- 총 시도 횟수: 1회

가격 정보:
- 예상 매수가: 1,698원 (현재가 조회 시점)
- 실제 매입단가: 1,709원 (체결가)
- 가격 차이: +11원 (+0.65%)
```

**핵심 인사이트**:
1. 체결 반영 속도: **약 0.8초** (매우 빠름)
2. 계좌 조회 시간: **0.183초** (ENABLE_LAZY_VERIFICATION=true 시 추가 지연)
3. 가격 슬리피지: **평균 0.5~1%** (시장가 특성상 불가피)

**ENABLE_LAZY_VERIFICATION 영향**:
- false (기존): 추정값 기준 즉시 판단 → 빠른 매도
- true (개선): 첫 시세에서 +0.183초 계좌 조회 → 정확한 수익률

## Security Notes

**절대 커밋하지 말 것**:
- `.env` 파일 (API 키 포함)
- `daily_trading_lock.json` (계좌 정보 포함)
- `trading_results/` (매매 기록)
- `*.log` 파일 (민감한 정보 포함 가능)

**API 키 관리**:
- 환경 변수로만 관리
- 코드에 하드코딩 금지
- 실전/모의투자 키 분리

## Additional Resources

- [README.md](README.md): 프로젝트 개요 및 사용법
- [키움증권 OpenAPI 문서](키움 REST API 문서.xlsx): API 명세서 (프로젝트 루트)
- `unused/`: 레거시 코드 (참고용)
