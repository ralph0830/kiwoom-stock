"""
키움증권 WebSocket API를 이용한 실시간 시세 수신

WebSocket을 통해 실시간 주식 시세를 받습니다.
"""

import asyncio
import json
import logging
from typing import Optional, Callable
import websockets
from kiwoom_order import KiwoomOrderAPI

logger = logging.getLogger(__name__)


class KiwoomWebSocket:
    """키움증권 WebSocket 실시간 시세 클래스"""

    def __init__(self, kiwoom_api: KiwoomOrderAPI, debug_mode: bool = False):
        """
        Args:
            kiwoom_api: 인증된 KiwoomOrderAPI 인스턴스
            debug_mode: 디버그 모드 (상세 로그 출력)
        """
        self.kiwoom_api = kiwoom_api
        self.ws_url = f"{kiwoom_api.base_url.replace('https', 'wss')}:10000/api/dostk/websocket"
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.callbacks = {}  # 종목코드별 콜백 함수
        self.current_prices = {}  # 종목코드별 현재가 캐시
        self.debug_mode = debug_mode  # 디버그 모드

    async def connect(self):
        """WebSocket 연결"""
        try:
            logger.info(f"📡 WebSocket 연결 시도: {self.ws_url}")

            # Access Token 발급 (없으면)
            if not self.kiwoom_api.access_token:
                self.kiwoom_api.get_access_token()

            # WebSocket 연결 (인증 헤더 포함)
            # ping_interval=None으로 설정하여 클라이언트 측 자동 ping 비활성화
            # (키움 서버가 ping/pong을 지원하지 않아 40초에 타임아웃 발생 방지)
            self.websocket = await websockets.connect(
                self.ws_url,
                additional_headers={
                    "authorization": f"Bearer {self.kiwoom_api.access_token}"
                },
                ping_interval=None,
                ping_timeout=None
            )

            logger.info("✅ WebSocket 연결 성공!")

            # 로그인 전문 전송 (token 필드로)
            login_message = {
                "trnm": "LOGIN",
                "token": self.kiwoom_api.access_token
            }

            await self.websocket.send(json.dumps(login_message))
            logger.info(f"🔑 WebSocket 로그인 전문 전송")

            # 로그인 응답 대기
            login_response = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
            login_data = json.loads(login_response)
            logger.info(f"📨 로그인 응답: {login_data}")

            if login_data.get("return_code") == 0 or login_data.get("trnm") == "LOGIN":
                logger.info("✅ WebSocket 로그인 성공!")
                self.is_connected = True
            else:
                logger.error(f"❌ WebSocket 로그인 실패: {login_data}")
                raise Exception(f"WebSocket 로그인 실패: {login_data}")

        except Exception as e:
            logger.error(f"❌ WebSocket 연결 실패: {e}")
            raise

    async def register_stock(self, stock_code: str, callback: Optional[Callable] = None):
        """
        실시간 시세 등록 (0A: 주식기세, 0B: 주식체결)

        Args:
            stock_code: 종목코드 (6자리)
            callback: 시세 수신 시 호출할 콜백 함수
        """
        if not self.is_connected:
            await self.connect()

        # 콜백 함수 등록
        if callback:
            self.callbacks[stock_code] = callback

        # 실시간 시세 등록 요청 (0A: 주식기세, 0B: 주식체결 모두 등록)
        register_request = {
            "trnm": "REG",  # 등록
            "grp_no": "1",  # 그룹번호
            "refresh": "1",  # 기존 유지
            "data": [
                {
                    "item": [stock_code],  # 종목코드
                    "type": ["0A", "0B"]  # 0A: 주식기세 (체결없이 가격변경), 0B: 주식체결 (실제 체결)
                }
            ]
        }

        try:
            await self.websocket.send(json.dumps(register_request))
            logger.info(f"📊 실시간 시세 등록: {stock_code}")

            # 등록 응답 대기
            response = await self.websocket.recv()
            response_data = json.loads(response)

            if response_data.get("return_code") == 0:
                logger.info(f"✅ 실시간 시세 등록 완료: {stock_code}")
            else:
                logger.error(f"❌ 실시간 시세 등록 실패: {response_data.get('return_msg')}")

        except Exception as e:
            logger.error(f"❌ 실시간 시세 등록 중 오류: {e}")
            raise

    async def unregister_stock(self, stock_code: str):
        """실시간 시세 해지"""
        if not self.is_connected:
            return

        unregister_request = {
            "trnm": "REMOVE",  # 해지
            "grp_no": "1",
            "data": [
                {
                    "item": [stock_code],
                    "type": ["0A", "0B"]  # 등록한 모든 타입 해지
                }
            ]
        }

        try:
            await self.websocket.send(json.dumps(unregister_request))
            logger.info(f"📊 실시간 시세 해지: {stock_code}")

            # 콜백 제거
            if stock_code in self.callbacks:
                del self.callbacks[stock_code]

        except Exception as e:
            logger.error(f"❌ 실시간 시세 해지 중 오류: {e}")

    async def receive_loop(self, auto_reconnect: bool = True):
        """
        실시간 데이터 수신 루프

        Args:
            auto_reconnect: 연결 끊김 시 자동 재연결 여부 (기본값: True)
        """
        reconnect_delay = 2  # 재연결 대기 시간 (초)

        while True:  # 자동 재연결을 위한 외부 루프
            try:
                # 타임아웃을 피하기 위해 무한 루프로 변경
                while self.is_connected:
                    try:
                        # 타임아웃 60초로 설정하여 메시지 대기
                        message = await asyncio.wait_for(self.websocket.recv(), timeout=60.0)

                        data = json.loads(message)

                        # PING 메시지 처리 (서버 heartbeat)
                        if data.get("trnm") == "PING":
                            # PING 메시지를 그대로 돌려보내서 연결 유지
                            await self.websocket.send(message)
                            if self.debug_mode:
                                logger.info("💓 PING 응답 전송 (연결 유지)")
                            continue

                        # 실시간 데이터 수신 (trnm이 "REAL"인 경우)
                        if data.get("trnm") == "REAL":
                            if self.debug_mode:
                                logger.info(f"📡 REAL 메시지 수신: {json.dumps(data, ensure_ascii=False)[:200]}")
                            await self._handle_realtime_data(data)
                        # SYSTEM 메시지 처리 (연결 종료 등)
                        elif data.get("trnm") == "SYSTEM":
                            code = data.get("code")
                            message = data.get("message", "")
                            logger.warning(f"⚠️ SYSTEM 메시지: [{code}] {message}")

                            # R10001: 동일한 App key로 중복 접속 - 연결 종료
                            if code == "R10001":
                                logger.warning("⚠️ 중복 접속으로 인한 연결 종료 - 재연결 대기")
                                self.is_connected = False
                                break
                        else:
                            # 기타 메시지 로깅 (디버깅용)
                            if self.debug_mode:
                                logger.info(f"📬 기타 WebSocket 메시지: {json.dumps(data, ensure_ascii=False)[:200]}")

                    except asyncio.TimeoutError:
                        # 60초 동안 메시지가 없으면 연결 상태 확인
                        logger.debug("WebSocket 메시지 대기 중... (연결 유지)")
                        continue
                    except json.JSONDecodeError:
                        logger.error(f"JSON 파싱 실패: {message}")
                        continue
                    except websockets.exceptions.ConnectionClosed as e:
                        # WebSocket 연결 종료 처리
                        if e.code == 1000:
                            logger.info("✅ WebSocket이 정상 종료되었습니다")
                        else:
                            logger.warning(f"⚠️ WebSocket 연결이 종료되었습니다 (code: {e.code})")
                        self.is_connected = False
                        break
                    except Exception as e:
                        logger.error(f"데이터 처리 중 오류: {e}")
                        # ConnectionClosed가 아닌 다른 오류는 계속 진행
                        continue

            except websockets.exceptions.ConnectionClosed as e:
                if e.code == 1000:
                    logger.info("✅ WebSocket이 정상 종료되었습니다")
                else:
                    logger.warning(f"⚠️ WebSocket 연결이 종료되었습니다 (code: {e.code})")
                self.is_connected = False
            except Exception as e:
                logger.error(f"❌ 수신 루프 오류: {e}")
                self.is_connected = False

            # 자동 재연결 로직
            if not auto_reconnect:
                logger.info("자동 재연결이 비활성화되어 있습니다. 종료합니다.")
                break

            # 연결이 끊어진 경우 재연결 시도
            if not self.is_connected:
                logger.info(f"🔄 {reconnect_delay}초 후 WebSocket 재연결을 시도합니다...")
                await asyncio.sleep(reconnect_delay)

                try:
                    # 기존 콜백 정보 백업
                    saved_callbacks = self.callbacks.copy()

                    # 재연결
                    await self.connect()

                    # 모든 종목 재등록
                    for stock_code, callback in saved_callbacks.items():
                        logger.info(f"📊 종목 재등록: {stock_code}")
                        await self.register_stock(stock_code, callback)

                    logger.info("✅ WebSocket 재연결 및 종목 재등록 완료")

                except Exception as e:
                    logger.error(f"❌ WebSocket 재연결 실패: {e}")
                    logger.info(f"🔄 {reconnect_delay * 2}초 후 다시 시도합니다...")
                    await asyncio.sleep(reconnect_delay * 2)
                    continue

    async def _handle_realtime_data(self, data: dict):
        """실시간 데이터 처리"""
        try:
            data_list = data.get("data", [])

            for item in data_list:
                type_code = item.get("type")  # 0A (주식기세) 또는 0B (주식체결)
                stock_code = item.get("item")  # 종목코드
                values = item.get("values", {})  # 실시간 데이터 값

                # 0A (주식기세) 또는 0B (주식체결) 모두 처리
                if type_code in ["0A", "0B"] and values:
                    # 실시간 데이터 파싱
                    realtime_data = values

                    # 현재가 (10: 현재가)
                    # +/- 기호 제거 후 파싱
                    current_price_str = realtime_data.get("10", "0")
                    current_price_str = current_price_str.replace("+", "").replace("-", "").replace(" ", "")
                    current_price = int(current_price_str) if current_price_str.replace(".", "").isdigit() else 0

                    # 현재가 캐시 업데이트
                    if current_price > 0:
                        self.current_prices[stock_code] = current_price

                    # 콜백 함수 호출
                    if stock_code in self.callbacks:
                        callback = self.callbacks[stock_code]
                        await callback(stock_code, current_price, realtime_data)

        except Exception as e:
            logger.error(f"실시간 데이터 처리 오류: {e}")

    def get_current_price(self, stock_code: str) -> int:
        """
        캐시된 현재가 가져오기

        Args:
            stock_code: 종목코드

        Returns:
            현재가 (없으면 0)
        """
        return self.current_prices.get(stock_code, 0)

    async def close(self):
        """WebSocket 연결 종료"""
        if self.websocket:
            await self.websocket.close()
            self.is_connected = False
            logger.info("📡 WebSocket 연결 종료")


if __name__ == "__main__":
    # 테스트 코드
    import os
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    async def test_price_callback(stock_code: str, price: int, data: dict):
        """테스트용 콜백 함수"""
        print(f"📊 [{stock_code}] 현재가: {price:,}원")

    async def main():
        # API 인스턴스 생성
        api = KiwoomOrderAPI()

        # WebSocket 생성
        ws = KiwoomWebSocket(api)

        # 연결
        await ws.connect()

        # 종목 등록 (삼성전자)
        await ws.register_stock("005930", test_price_callback)

        # 10초 동안 수신
        receive_task = asyncio.create_task(ws.receive_loop())
        await asyncio.sleep(10)

        # 종료
        receive_task.cancel()
        await ws.close()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n테스트 종료")
