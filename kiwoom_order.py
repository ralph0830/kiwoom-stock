"""
키움증권 REST API를 이용한 주식 주문 모듈

실시간 종목 포착 시 자동으로 매수 주문을 실행합니다.
"""

import os
import requests
from datetime import datetime
from typing import Dict, Optional
from dotenv import load_dotenv
import logging

# 환경변수 로드
load_dotenv()

logger = logging.getLogger(__name__)


class KiwoomOrderAPI:
    """키움증권 주식 주문 API 클래스"""

    def __init__(self):
        # 모의투자 여부 확인 (USE_MOCK=true면 모의투자, false면 실전)
        use_mock = os.getenv("USE_MOCK", "false").lower() == "true"

        if use_mock:
            # 모의투자 설정
            self.app_key = os.getenv("KIWOOM_MOCK_APP_KEY")
            self.secret_key = os.getenv("KIWOOM_MOCK_SECRET_KEY")
            self.base_url = "https://mockapi.kiwoom.com"  # 모의투자 서버
            logger.info("🧪 모의투자 모드로 설정되었습니다")
        else:
            # 실전투자 설정
            self.app_key = os.getenv("KIWOOM_APP_KEY")
            self.secret_key = os.getenv("KIWOOM_SECRET_KEY")
            self.base_url = "https://api.kiwoom.com"  # 실전투자 서버
            logger.info("💰 실전투자 모드로 설정되었습니다")

        self.access_token: Optional[str] = None

        if not self.app_key or not self.secret_key:
            raise ValueError(f"환경변수에 API KEY가 설정되어 있지 않습니다. (모의투자: {use_mock})")

    def get_access_token(self) -> str:
        """Access Token 발급 (OAuth2)"""
        url = f"{self.base_url}/oauth2/token"

        headers = {"Content-Type": "application/json;charset=UTF-8"}

        data = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.secret_key
        }

        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()
            # 키움 API는 'token' 필드에 토큰 반환
            access_token = result.get("token")

            if not access_token:
                raise ValueError(f"Access Token을 발급받지 못했습니다. 응답: {result}")

            self.access_token = access_token
            logger.info("✅ Access Token 발급 완료")
            logger.info(f"토큰 만료일: {result.get('expires_dt', 'N/A')}")
            return access_token

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Access Token 발급 실패: {e}")
            raise

    def place_market_buy_order(
        self,
        stock_code: str,
        quantity: int,
        account_no: str
    ) -> Dict:
        """
        시장가 매수 주문

        Args:
            stock_code: 종목코드 (6자리)
            quantity: 매수 수량
            account_no: 계좌번호 (사용하지 않음 - 토큰에 포함됨)

        Returns:
            주문 결과 딕셔너리
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/api/dostk/ordr"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "kt10000",  # 주식 매수주문 TR
        }

        # 주문 데이터
        body = {
            "dmst_stex_tp": "KRX",     # 거래소 구분 (KRX: 한국거래소)
            "stk_cd": stock_code,      # 종목코드
            "ord_qty": str(quantity),  # 주문 수량 (문자열)
            "ord_uv": "",              # 주문 단가 (시장가는 빈값)
            "trde_tp": "3",            # 매매 구분 (3: 시장가)
            "cond_uv": ""              # 조건 단가 (빈값)
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            # 응답에서 주문번호 확인
            ord_no = result.get("ord_no", "")
            dmst_stex_tp = result.get("dmst_stex_tp", "")

            if ord_no:
                logger.info(f"✅ 시장가 매수 주문 성공!")
                logger.info(f"종목코드: {stock_code}")
                logger.info(f"주문수량: {quantity}주")
                logger.info(f"주문번호: {ord_no}")
                logger.info(f"거래소: {dmst_stex_tp}")

                return {
                    "success": True,
                    "order_no": ord_no,
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "order_type": "시장가",
                    "exchange": dmst_stex_tp,
                    "message": "주문이 완료되었습니다"
                }
            else:
                logger.error(f"❌ 시장가 매수 주문 실패")
                logger.error(f"응답: {result}")
                return {
                    "success": False,
                    "message": f"주문 실패: {result}",
                    "stock_code": stock_code,
                    "quantity": quantity
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 시장가 매수 주문 요청 실패: {e}")
            return {
                "success": False,
                "message": str(e),
                "stock_code": stock_code,
                "quantity": quantity
            }

    def place_limit_buy_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        account_no: str
    ) -> Dict:
        """
        지정가 매수 주문

        Args:
            stock_code: 종목코드 (6자리)
            quantity: 매수 수량
            price: 지정가격
            account_no: 계좌번호 (사용하지 않음)

        Returns:
            주문 결과 딕셔너리
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/api/dostk/ordr"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "kt10000",  # 주식 매수주문 TR
        }

        # 주문 데이터
        body = {
            "dmst_stex_tp": "KRX",     # 거래소 구분
            "stk_cd": stock_code,      # 종목코드
            "ord_qty": str(quantity),  # 주문 수량
            "ord_uv": str(price),      # 주문 단가
            "trde_tp": "0",            # 매매 구분 (0: 보통/지정가)
            "cond_uv": ""              # 조건 단가
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            ord_no = result.get("ord_no", "")
            dmst_stex_tp = result.get("dmst_stex_tp", "")

            if ord_no:
                logger.info(f"✅ 지정가 매수 주문 성공!")
                logger.info(f"종목코드: {stock_code}")
                logger.info(f"주문수량: {quantity}주")
                logger.info(f"주문가격: {price:,}원")
                logger.info(f"주문번호: {ord_no}")

                return {
                    "success": True,
                    "order_no": ord_no,
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "price": price,
                    "order_type": "지정가",
                    "exchange": dmst_stex_tp,
                    "message": "주문이 완료되었습니다"
                }
            else:
                logger.error(f"❌ 지정가 매수 주문 실패")
                logger.error(f"응답: {result}")
                return {
                    "success": False,
                    "message": f"주문 실패: {result}",
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "price": price
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 지정가 매수 주문 요청 실패: {e}")
            return {
                "success": False,
                "message": str(e),
                "stock_code": stock_code,
                "quantity": quantity,
                "price": price
            }

    def place_limit_sell_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        account_no: str
    ) -> Dict:
        """
        지정가 매도 주문

        Args:
            stock_code: 종목코드 (6자리)
            quantity: 매도 수량
            price: 지정가격
            account_no: 계좌번호 (사용하지 않음)

        Returns:
            주문 결과 딕셔너리
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/api/dostk/ordr"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "kt10001",  # 주식 매도주문 TR
        }

        # 주문 데이터
        body = {
            "dmst_stex_tp": "KRX",     # 거래소 구분
            "stk_cd": stock_code,      # 종목코드
            "ord_qty": str(quantity),  # 주문 수량
            "ord_uv": str(price),      # 주문 단가
            "trde_tp": "0",            # 매매 구분 (0: 보통/지정가)
            "cond_uv": ""              # 조건 단가
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            ord_no = result.get("ord_no", "")
            dmst_stex_tp = result.get("dmst_stex_tp", "")

            if ord_no:
                logger.info(f"✅ 지정가 매도 주문 성공!")
                logger.info(f"종목코드: {stock_code}")
                logger.info(f"주문수량: {quantity}주")
                logger.info(f"주문가격: {price:,}원")
                logger.info(f"주문번호: {ord_no}")

                return {
                    "success": True,
                    "order_no": ord_no,
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "price": price,
                    "order_type": "지정가 매도",
                    "exchange": dmst_stex_tp,
                    "message": "매도 주문이 완료되었습니다"
                }
            else:
                logger.error(f"❌ 지정가 매도 주문 실패")
                logger.error(f"응답: {result}")
                return {
                    "success": False,
                    "message": f"매도 주문 실패: {result}",
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "price": price
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 지정가 매도 주문 요청 실패: {e}")
            return {
                "success": False,
                "message": str(e),
                "stock_code": stock_code,
                "quantity": quantity,
                "price": price
            }

    def calculate_order_quantity(
        self,
        buy_price: int,
        max_investment: int = 1000000
    ) -> int:
        """
        매수 수량 계산

        Args:
            buy_price: 매수가격
            max_investment: 최대 투자금액 (기본: 100만원)

        Returns:
            매수 가능 수량
        """
        if buy_price <= 0:
            return 0

        quantity = max_investment // buy_price
        return quantity


def parse_price_string(price_str: str) -> int:
    """
    가격 문자열을 정수로 변환
    예: "75,000원" -> 75000
    """
    if not price_str or price_str == '-':
        return 0

    # 쉼표, 원 제거 후 정수 변환
    clean_str = price_str.replace(',', '').replace('원', '').strip()

    try:
        return int(clean_str)
    except ValueError:
        return 0


def get_tick_size(price: int) -> int:
    """
    주가에 따른 호가 단위(틱) 계산

    Args:
        price: 현재 주가

    Returns:
        호가 단위 (1틱)
    """
    if price < 1000:
        return 1
    elif price < 5000:
        return 5
    elif price < 10000:
        return 10
    elif price < 50000:
        return 50
    elif price < 100000:
        return 100
    elif price < 500000:
        return 500
    else:
        return 1000


def calculate_sell_price(buy_price: int, profit_rate: float = 0.02) -> int:
    """
    목표 수익률 도달 시 매도가 계산 (한 틱 아래)

    Args:
        buy_price: 매수 가격
        profit_rate: 목표 수익률 (기본: 0.02 = 2%)

    Returns:
        매도 주문가 (목표가에서 한 틱 아래)
    """
    # 목표 수익률 가격
    target_price = int(buy_price * (1 + profit_rate))

    # 목표가 기준 틱 크기
    tick_size = get_tick_size(target_price)

    # 한 틱 아래 가격
    sell_price = target_price - tick_size

    return sell_price


if __name__ == "__main__":
    # 테스트 코드
    logging.basicConfig(level=logging.INFO)

    # API 인스턴스 생성
    api = KiwoomOrderAPI()

    # Access Token 발급 테스트
    try:
        token = api.get_access_token()
        print(f"✅ 토큰 발급 성공: {token[:20]}...")
    except Exception as e:
        print(f"❌ 토큰 발급 실패: {e}")
