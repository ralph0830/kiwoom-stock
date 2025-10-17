# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

키움증권 REST OpenAPI를 사용하여 특정 종목이 장 시작 후 (09:00~09:05) 매수가에 도달한 시각을 자동으로 확인하는 Python 스크립트입니다.

## Development Environment

**Package Manager**: UV (Python package manager)
- 이 프로젝트는 `uv`를 사용하여 의존성을 관리합니다
- `pyproject.toml`에 의존성이 정의되어 있습니다
- Windows (win_amd64) 환경에서 실행됩니다

**Python Version**: >=3.10

## Common Commands

### 패키지 설치
```bash
uv sync
```

### 프로그램 실행
```bash
uv run python main.py
```

### 의존성 추가
```bash
uv add <package-name>
```

## Environment Variables

프로젝트 루트에 `.env` 파일이 필요하며, 다음 환경변수를 설정해야 합니다:

```
KIWOOM_APP_KEY=<your_app_key>
KIWOOM_SECRET_KEY=<your_secret_key>
```

**중요**: `.env` 파일은 `.gitignore`에 포함되어야 하며, 실제 키 값은 절대 커밋하지 마세요.

## Architecture

### 단일 파일 구조
- `main.py`: 모든 로직이 포함된 단일 스크립트
  - `get_access_token()`: OAuth2 Client Credentials 방식으로 Access Token 발급
  - `get_minute_chart()`: 키움 API를 통해 1분봉 데이터 조회
  - `find_reach_time()`: 특정 가격대 도달 시각 검색
  - `main()`: 사용자 입력 처리 및 실행

### API Integration
- **Base URL**: `https://openapi.kiwoom.com`
- **인증 방식**: OAuth2 Client Credentials (Bearer Token)
- **주요 엔드포인트**:
  - `/oauth2/token`: Access Token 발급
  - `/uapi/domestic-stock/v1/quotations/inquire-time-itemchart`: 분봉 데이터 조회 (TR_ID: FHKST03010200)

### Data Flow
1. 환경변수에서 APP_KEY, SECRET_KEY 로드
2. OAuth2 인증으로 Access Token 발급
3. 종목코드, 날짜, 시간 범위로 1분봉 데이터 요청
4. 응답 데이터에서 목표 가격대 도달 시각 검색
5. 결과 출력 (도달 시각 또는 미도달 메시지)

## Important Notes

### API 응답 구조
- API 응답의 데이터 구조는 `output2` 또는 `output` 필드에 배열로 제공됩니다
- 각 분봉 데이터 포인트:
  - `stck_cntg_hour`: 시각 (HHMM 형식)
  - `stck_hgpr`: 고가
  - `stck_lwpr`: 저가

### 가격 도달 검증 로직
- 목표 가격이 해당 분봉의 저가와 고가 사이에 있는지 확인: `low <= target_price <= high`
- 첫 번째 매칭되는 시각을 반환

### Error Handling
- API 요청 실패 시 `requests.raise_for_status()`로 예외 발생
- Access Token 발급 실패 시 `ValueError` 발생
- `main()` 함수에서 전체 프로세스를 try-except로 감싸서 사용자 친화적 오류 메시지 제공
