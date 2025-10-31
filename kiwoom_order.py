"""
키움증권 REST API를 이용한 주식 주문 모듈

실시간 종목 포착 시 자동으로 매수 주문을 실행합니다.
"""

import os
import re
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
        account_no: str,
        retry_on_insufficient_funds: bool = True
    ) -> Dict:
        """
        시장가 매수 주문

        Args:
            stock_code: 종목코드 (6자리)
            quantity: 매수 수량
            account_no: 계좌번호 (사용하지 않음 - 토큰에 포함됨)
            retry_on_insufficient_funds: 증거금 부족 시 자동 재시도 여부 (기본: True)

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
                # 증거금 부족 에러 처리
                return_msg = result.get("return_msg", "")
                return_code = result.get("return_code")

                # 증거금 부족 에러인지 확인하고 매수 가능 수량 추출
                if retry_on_insufficient_funds and return_code == 20:
                    available_qty = self._parse_available_quantity(return_msg)

                    if available_qty and available_qty > 0 and available_qty < quantity:
                        logger.warning(f"⚠️ 증거금 부족! 요청 수량: {quantity}주, 매수 가능: {available_qty}주")
                        logger.info(f"🔄 매수 가능 수량({available_qty}주)으로 재시도합니다...")

                        # 매수 가능 수량으로 재귀 호출 (재시도 방지 플래그 전달)
                        return self.place_market_buy_order(
                            stock_code=stock_code,
                            quantity=available_qty,
                            account_no=account_no,
                            retry_on_insufficient_funds=False  # 재시도 방지
                        )

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

    def _parse_available_quantity(self, error_message: str) -> int | None:
        """
        에러 메시지에서 매수 가능 수량 파싱

        예: '[2000](855056:매수증거금이 부족합니다. 777주 매수가능)' -> 777

        Args:
            error_message: API 에러 메시지

        Returns:
            매수 가능 수량 또는 None
        """
        # 정규표현식으로 "숫자주 매수가능" 패턴 추출
        match = re.search(r'(\d+)주\s*매수가능', error_message)

        if match:
            available_qty = int(match.group(1))
            logger.info(f"📊 매수 가능 수량 파싱: {available_qty}주")
            return available_qty

        return None

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

    def place_market_sell_order(
        self,
        stock_code: str,
        quantity: int,
        account_no: str
    ) -> Dict:
        """
        시장가 매도 주문 (손절용)

        Args:
            stock_code: 종목코드 (6자리)
            quantity: 매도 수량
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

        # 주문 데이터 (시장가)
        body = {
            "dmst_stex_tp": "KRX",     # 거래소 구분
            "stk_cd": stock_code,      # 종목코드
            "ord_qty": str(quantity),  # 주문 수량
            "ord_uv": "",              # 주문 단가 (시장가는 빈값)
            "trde_tp": "3",            # 매매 구분 (3: 시장가)
            "cond_uv": ""              # 조건 단가
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            ord_no = result.get("ord_no", "")
            dmst_stex_tp = result.get("dmst_stex_tp", "")

            if ord_no:
                logger.info(f"✅ 시장가 매도 주문 성공! (손절)")
                logger.info(f"종목코드: {stock_code}")
                logger.info(f"주문수량: {quantity}주")
                logger.info(f"주문번호: {ord_no}")

                return {
                    "success": True,
                    "order_no": ord_no,
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "order_type": "시장가 매도 (손절)",
                    "exchange": dmst_stex_tp,
                    "message": "손절 매도 주문이 완료되었습니다"
                }
            else:
                logger.error(f"❌ 시장가 매도 주문 실패")
                logger.error(f"응답: {result}")
                return {
                    "success": False,
                    "message": f"매도 주문 실패: {result}",
                    "stock_code": stock_code,
                    "quantity": quantity
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 시장가 매도 주문 요청 실패: {e}")
            return {
                "success": False,
                "message": str(e),
                "stock_code": stock_code,
                "quantity": quantity
            }

    def get_current_price(self, stock_code: str) -> Dict:
        """
        현재가 조회 (ka10001 - 주식현재가)

        Args:
            stock_code: 종목코드 (6자리)

        Returns:
            현재가 정보 딕셔너리
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/api/dostk/stkinfo"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "ka10001",  # 주식현재가 TR (OPT10001)
        }

        body = {
            "stk_cd": stock_code  # 종목코드
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            # 현재가 추출 (cur_prc 필드)
            cur_prc_str = result.get("cur_prc", "0")

            # +/- 기호 제거 후 정수 변환
            cur_prc_str = cur_prc_str.replace("+", "").replace("-", "").replace(",", "")
            current_price = int(cur_prc_str) if cur_prc_str.isdigit() else 0

            return {
                "success": True,
                "stock_code": stock_code,
                "current_price": current_price,
                "data": result
            }

        except Exception as e:
            logger.error(f"❌ 현재가 조회 실패: {e}")
            return {
                "success": False,
                "stock_code": stock_code,
                "current_price": 0,
                "message": str(e)
            }

    def get_account_balance(self, query_date: str = None) -> Dict:
        """
        계좌 잔고 및 보유종목 조회 (ka01690)

        Args:
            query_date: 조회일자 (YYYYMMDD 형식, 기본값: 오늘)

        Returns:
            계좌 잔고 정보 딕셔너리
        """
        if not self.access_token:
            self.get_access_token()

        # 조회일자가 없으면 오늘 날짜 사용
        if not query_date:
            query_date = datetime.now().strftime("%Y%m%d")

        url = f"{self.base_url}/api/dostk/acnt"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "ka01690",  # 일별잔고수익률 TR
        }

        # JSON body로 전송
        body = {
            "qry_dt": query_date
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            # 보유종목 리스트 추출
            raw_holdings = result.get("day_bal_rt", [])

            # 실제 보유종목만 필터링 (종목코드가 있는 항목만)
            holdings = [
                holding for holding in raw_holdings
                if holding.get("stk_cd", "").strip()  # 종목코드가 있는 경우만
            ]

            if holdings:
                logger.info(f"✅ 계좌 잔고 조회 성공! (보유종목 {len(holdings)}개)")

                # 보유종목 정보 로깅
                for holding in holdings:
                    stock_code = holding.get("stk_cd", "")
                    stock_name = holding.get("stk_nm", "")

                    # 안전한 정수 변환 (빈 문자열 처리)
                    quantity = int(holding.get("rmnd_qty") or 0)  # 보유수량 (rmnd_qty)
                    buy_price = int(holding.get("buy_uv") or 0)  # 매입단가
                    current_price = int(holding.get("cur_prc") or 0)  # 현재가 (cur_prc)
                    profit_loss = int(holding.get("evltv_prft") or 0)  # 평가손익 (evltv_prft)

                    # 안전한 실수 변환
                    profit_rate_str = holding.get("prft_rt", "0")
                    profit_rate = float(profit_rate_str) if profit_rate_str else 0.0  # 수익률 (prft_rt)

                    logger.info(f"  📊 [{stock_name}({stock_code})] 보유수량: {quantity}주, 매입단가: {buy_price:,}원, 현재가: {current_price:,}원, 평가손익: {profit_loss:+,}원 ({profit_rate:+.2f}%)")

                return {
                    "success": True,
                    "holdings": holdings,
                    "total_holdings": len(holdings),
                    "data": result
                }
            else:
                logger.info("ℹ️ 보유종목이 없습니다")
                return {
                    "success": True,
                    "holdings": [],
                    "total_holdings": 0,
                    "data": result
                }

        except Exception as e:
            logger.error(f"❌ 계좌 잔고 조회 실패: {e}")
            return {
                "success": False,
                "holdings": [],
                "message": str(e)
            }

    def get_outstanding_orders(self, query_date: str = None) -> Dict:
        """
        미체결 주문 조회 (ka10075)

        Args:
            query_date: 조회일자 (YYYYMMDD 형식, 기본값: 오늘)

        Returns:
            미체결 주문 목록 딕셔너리
        """
        if not self.access_token:
            self.get_access_token()

        # 조회일자가 없으면 오늘 날짜 사용
        if not query_date:
            query_date = datetime.now().strftime("%Y%m%d")

        url = f"{self.base_url}/api/dostk/acnt"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "ka10075",  # 미체결요청 TR
        }

        # JSON body로 전송
        body = {
            "qry_dt": query_date
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            # 미체결 주문 리스트 추출 (실제 필드명은 API 응답에 따라 조정 필요)
            # 예상 필드명: outstanding_orders, unexecuted_orders, 또는 특정 키
            outstanding_orders = result.get("outstanding_orders", result.get("orders", []))

            if outstanding_orders:
                logger.info(f"⚠️ 미체결 주문 {len(outstanding_orders)}건 발견")

                # 미체결 주문 정보 로깅
                for order in outstanding_orders:
                    ord_no = order.get("ord_no", "")
                    stock_code = order.get("stk_cd", "")
                    stock_name = order.get("stk_nm", "")
                    ord_qty = order.get("ord_qty", "0")
                    rmndr_qty = order.get("rmndr_qty", ord_qty)  # 미체결수량
                    ord_uv = order.get("ord_uv", "0")

                    logger.info(f"  📋 주문번호: {ord_no}, 종목: {stock_name}({stock_code}), 미체결수량: {rmndr_qty}주, 주문가: {ord_uv}원")

                return {
                    "success": True,
                    "outstanding_orders": outstanding_orders,
                    "total_count": len(outstanding_orders),
                    "data": result
                }
            else:
                logger.info("✅ 미체결 주문이 없습니다")
                return {
                    "success": True,
                    "outstanding_orders": [],
                    "total_count": 0,
                    "data": result
                }

        except Exception as e:
            logger.error(f"❌ 미체결 주문 조회 실패: {e}")
            return {
                "success": False,
                "outstanding_orders": [],
                "message": str(e)
            }

    def cancel_order(
        self,
        order_no: str,
        stock_code: str,
        quantity: int
    ) -> Dict:
        """
        주문 취소 (kt10003 - 주식취소주문)

        Args:
            order_no: 원주문번호
            stock_code: 종목코드
            quantity: 취소 수량

        Returns:
            취소 결과 딕셔너리
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/api/dostk/ordr"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "kt10003",  # 주식취소주문 TR
        }

        # 주문 취소 데이터 (kt10003 스펙)
        body = {
            "dmst_stex_tp": "KRX",          # 거래소 구분
            "orig_ord_no": order_no,        # 원주문번호
            "stk_cd": stock_code,           # 종목코드
            "cncl_qty": str(quantity),      # 취소 수량 ('0' 입력 시 잔량 전부 취소)
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            cncl_ord_no = result.get("ord_no", "")

            if cncl_ord_no:
                logger.info(f"✅ 주문 취소 성공!")
                logger.info(f"원주문번호: {order_no}")
                logger.info(f"취소주문번호: {cncl_ord_no}")
                logger.info(f"취소수량: {quantity}주")

                return {
                    "success": True,
                    "cancel_order_no": cncl_ord_no,
                    "original_order_no": order_no,
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "message": "주문 취소가 완료되었습니다"
                }
            else:
                logger.error(f"❌ 주문 취소 실패")
                logger.error(f"응답: {result}")
                return {
                    "success": False,
                    "message": f"주문 취소 실패: {result}",
                    "original_order_no": order_no
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 주문 취소 요청 실패: {e}")
            return {
                "success": False,
                "message": str(e),
                "original_order_no": order_no
            }

    def check_order_execution(self, order_no: str) -> Dict:
        """
        특정 주문번호의 체결 여부 확인

        Args:
            order_no: 확인할 주문번호

        Returns:
            체결 상태 딕셔너리
            - is_executed: 체결 완료 여부
            - remaining_qty: 미체결 수량 (체결 완료 시 0)
        """
        outstanding_result = self.get_outstanding_orders()

        if not outstanding_result["success"]:
            return {
                "success": False,
                "is_executed": False,
                "message": "미체결 조회 실패"
            }

        outstanding_orders = outstanding_result["outstanding_orders"]

        # 해당 주문번호가 미체결 목록에 있는지 확인
        for order in outstanding_orders:
            if order.get("ord_no") == order_no:
                remaining_qty = int(order.get("rmndr_qty", order.get("ord_qty", "0")))
                return {
                    "success": True,
                    "is_executed": False,
                    "remaining_qty": remaining_qty,
                    "order_info": order
                }

        # 미체결 목록에 없으면 체결 완료
        return {
            "success": True,
            "is_executed": True,
            "remaining_qty": 0
        }

    def calculate_order_quantity(
        self,
        buy_price: int,
        max_investment: int = 1000000
    ) -> int:
        """
        매수 수량 계산 (100% 투자)

        증거금 부족 시 API가 매수 가능 수량을 자동으로 알려주므로
        100% 투자금을 사용합니다. (자동 재시도 로직으로 안전하게 처리)

        Args:
            buy_price: 매수가격 (현재가)
            max_investment: 최대 투자금액 (기본: 100만원)

        Returns:
            매수 가능 수량 (100% 투자)
        """
        if buy_price <= 0:
            return 0

        # 100% 투자 (증거금 부족 시 API가 자동으로 가능 수량 알려줌)
        quantity = max_investment // buy_price

        logger.info(f"💰 매수 수량 계산: 투자금 {max_investment:,}원 / 현재가 {buy_price:,}원 = {quantity}주")

        return quantity

    def get_realtime_stock_ranking(self, qry_tp: str = '4', cont_yn: str = None, next_key: str = None) -> Dict:
        """
        실시간종목조회순위 조회 (ka00198) - 연속조회 지원

        Args:
            qry_tp: 구분 (1:1분, 2:10분, 3:1시간, 4:당일 누적, 5:30초)
            cont_yn: 연속조회여부 (Y: 다음 페이지 조회)
            next_key: 연속조회키 (이전 응답의 next-key 값)

        Returns:
            {
                'success': bool,
                'data': dict,
                'message': str,
                'cont_yn': str,  # 다음 페이지 존재 여부
                'next_key': str  # 다음 페이지 키
            }
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/api/dostk/stkinfo"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "ka00198",  # 실시간종목조회순위
        }

        # 연속조회 헤더 추가
        if cont_yn == 'Y' and next_key:
            headers["cont-yn"] = cont_yn
            headers["next-key"] = next_key

        body = {
            "qry_tp": qry_tp
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            if result.get('return_code') == 0:
                # 응답 헤더에서 연속조회 정보 추출
                response_headers = response.headers
                cont_yn_response = response_headers.get('cont-yn', 'N')
                next_key_response = response_headers.get('next-key', '')

                page_info = f"(페이지: {'다음' if cont_yn == 'Y' else '첫'})"
                logger.info(f"✅ 실시간종목조회순위 조회 성공 (구분: {qry_tp}) {page_info}")

                return {
                    "success": True,
                    "data": result,
                    "message": result.get('return_msg', '성공'),
                    "cont_yn": cont_yn_response,
                    "next_key": next_key_response
                }
            else:
                logger.error(f"❌ 실시간종목조회순위 조회 실패: {result}")
                return {
                    "success": False,
                    "data": {},
                    "message": result.get('return_msg', '알 수 없는 오류'),
                    "cont_yn": "N",
                    "next_key": ""
                }

        except Exception as e:
            logger.error(f"❌ 실시간종목조회순위 조회 실패: {e}")
            return {
                "success": False,
                "data": {},
                "message": str(e),
                "cont_yn": "N",
                "next_key": ""
            }

    def get_daily_chart(self, stock_code: str, period: int = 120, base_dt: str = None) -> Dict:
        """
        주식일봉차트 조회 (ka10081)

        Args:
            stock_code: 종목코드
            period: 조회 기간 (일 수, 기본 120일)
            base_dt: 기준일자 (YYYYMMDD, 기본값: 오늘 날짜)

        Returns:
            {
                'success': bool,
                'data': list,  # 일봉 데이터 리스트
                'message': str
            }
        """
        if not self.access_token:
            self.get_access_token()

        # base_dt가 없으면 오늘 날짜 사용
        if base_dt is None:
            from datetime import datetime
            base_dt = datetime.now().strftime("%Y%m%d")

        url = f"{self.base_url}/api/dostk/chart"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "ka10081",  # 주식일봉차트조회요청
        }

        body = {
            "stk_cd": stock_code,
            "base_dt": base_dt,  # 기준일자 (필수, YYYYMMDD)
            "upd_stkpc_tp": "1"  # 수정주가구분 (필수, 0: 무수정, 1: 수정주가)
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            if result.get('return_code') == 0:
                # 실제 API 응답 필드: stk_dt_pole_chart_qry
                chart_data = result.get('stk_dt_pole_chart_qry', [])

                # period 개수만큼 자르기 (최신순으로 정렬되어 있음)
                chart_data = chart_data[:period] if len(chart_data) > period else chart_data

                logger.info(f"✅ [{stock_code}] 일봉 차트 조회 성공 ({len(chart_data)}개)")
                return {
                    "success": True,
                    "data": chart_data,
                    "message": "성공"
                }
            else:
                logger.error(f"❌ [{stock_code}] 일봉 차트 조회 실패: {result}")
                return {
                    "success": False,
                    "data": [],
                    "message": result.get('return_msg', '알 수 없는 오류')
                }

        except Exception as e:
            logger.error(f"❌ [{stock_code}] 일봉 차트 조회 실패: {e}")
            return {
                "success": False,
                "data": [],
                "message": str(e)
            }

    def get_minute_chart(self, stock_code: str, minute: int = 1, period: int = 60) -> Dict:
        """
        주식분봉차트 조회 (ka10080)

        Args:
            stock_code: 종목코드
            minute: 분봉 단위 (1, 3, 5, 10, 15, 30, 60)
            period: 조회 개수 (기본 60개)

        Returns:
            {
                'success': bool,
                'data': list,  # 분봉 데이터 리스트
                'message': str
            }
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/api/dostk/chart"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": "ka10080",  # 주식분봉차트조회요청
        }

        body = {
            "stk_cd": stock_code,
            "odr_tp": "1",  # 정순(오름차순)
            "inq_size": str(period),  # 조회 개수
            "bng_tp": str(minute)  # 분봉 단위
        }

        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()

            result = response.json()

            if result.get('return_code') == 0:
                chart_data = result.get('stk_min_chart', [])
                logger.info(f"✅ [{stock_code}] {minute}분봉 차트 조회 성공 ({len(chart_data)}개)")
                return {
                    "success": True,
                    "data": chart_data,
                    "message": "성공"
                }
            else:
                logger.error(f"❌ [{stock_code}] {minute}분봉 차트 조회 실패: {result}")
                return {
                    "success": False,
                    "data": [],
                    "message": result.get('return_msg', '알 수 없는 오류')
                }

        except Exception as e:
            logger.error(f"❌ [{stock_code}] {minute}분봉 차트 조회 실패: {e}")
            return {
                "success": False,
                "data": [],
                "message": str(e)
            }


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


def calculate_sell_price(current_price: int, buy_price: int = None, profit_rate: float = None) -> int:
    """
    매도가 계산 (현재가 기준 한 틱 아래)

    Args:
        current_price: 현재가
        buy_price: 매수 가격 (사용하지 않음, 하위 호환성 유지)
        profit_rate: 목표 수익률 (사용하지 않음, 하위 호환성 유지)

    Returns:
        매도 주문가 (현재가에서 한 틱 아래)
    """
    # 현재가 기준 틱 크기
    tick_size = get_tick_size(current_price)

    # 한 틱 아래 가격
    sell_price = current_price - tick_size

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
