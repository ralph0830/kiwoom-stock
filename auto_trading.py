"""
오늘의단타 LIVE 실시간 자동매매 시스템

웹페이지에서 종목을 실시간 감시하고, 포착 즉시 키움 API로 자동 매수합니다.
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Page
import logging
from dotenv import load_dotenv
from kiwoom_order import KiwoomOrderAPI, parse_price_string, calculate_sell_price
from kiwoom_websocket import KiwoomWebSocket

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('auto_trading.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class AutoTradingSystem:
    """실시간 종목 모니터링 + 자동 매수 시스템"""

    def __init__(
        self,
        account_no: str,
        max_investment: int = 1000000,
        url: str = "https://live.today-stock.kr/"
    ):
        """
        Args:
            account_no: 키움증권 계좌번호 (예: "12345678-01")
            max_investment: 최대 투자금액 (기본: 100만원)
            url: 모니터링할 웹페이지 URL
        """
        self.account_no = account_no
        self.max_investment = max_investment
        self.url = url
        self.page: Page | None = None
        self.is_monitoring = False
        self.order_executed = False
        self.sell_executed = False  # 매도 실행 플래그 (중복 방지)
        self.sell_monitoring = False

        # 매수 정보 저장
        self.buy_info = {
            "stock_code": None,
            "stock_name": None,
            "buy_price": 0,
            "quantity": 0,
            "target_profit_rate": 0.02  # 2% 수익률
        }

        # 키움 API 초기화
        self.kiwoom_api = KiwoomOrderAPI()

        # WebSocket 초기화
        self.websocket: Optional[KiwoomWebSocket] = None
        self.ws_receive_task: Optional[asyncio.Task] = None

        # 결과 저장 디렉토리 생성
        self.result_dir = Path("./trading_results")
        self.result_dir.mkdir(exist_ok=True)

        # 하루 1회 매수 제한 파일
        self.trading_lock_file = Path("./daily_trading_lock.json")

    def check_today_trading_done(self) -> bool:
        """
        오늘 이미 매수했는지 확인

        Returns:
            True: 오늘 이미 매수함, False: 매수 안 함
        """
        if not self.trading_lock_file.exists():
            return False

        try:
            with open(self.trading_lock_file, 'r', encoding='utf-8') as f:
                lock_data = json.load(f)

            last_trading_date = lock_data.get("last_trading_date")
            today = datetime.now().strftime("%Y%m%d")

            if last_trading_date == today:
                logger.info(f"⏹️  오늘({today}) 이미 매수를 실행했습니다.")
                logger.info(f"📝 매수 정보: {lock_data.get('stock_name')} ({lock_data.get('stock_code')})")
                logger.info(f"⏰ 매수 시각: {lock_data.get('trading_time')}")
                return True

            return False

        except Exception as e:
            logger.error(f"매수 이력 확인 중 오류: {e}")
            return False

    def record_today_trading(self, stock_code: str, stock_name: str, buy_price: int, quantity: int):
        """
        오늘 매수 기록 저장

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            buy_price: 매수가
            quantity: 매수 수량
        """
        try:
            lock_data = {
                "last_trading_date": datetime.now().strftime("%Y%m%d"),
                "trading_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "stock_code": stock_code,
                "stock_name": stock_name,
                "buy_price": buy_price,
                "quantity": quantity
            }

            with open(self.trading_lock_file, 'w', encoding='utf-8') as f:
                json.dump(lock_data, f, ensure_ascii=False, indent=2)

            logger.info(f"✅ 오늘 매수 기록 저장 완료")

        except Exception as e:
            logger.error(f"매수 기록 저장 중 오류: {e}")

    def load_today_trading_info(self) -> dict | None:
        """오늘 매수 정보 로드"""
        if not self.trading_lock_file.exists():
            return None

        try:
            with open(self.trading_lock_file, 'r', encoding='utf-8') as f:
                lock_data = json.load(f)

            last_trading_date = lock_data.get("last_trading_date")
            today = datetime.now().strftime("%Y%m%d")

            if last_trading_date == today:
                return lock_data

            return None
        except Exception as e:
            logger.error(f"매수 정보 로드 중 오류: {e}")
            return None

    async def start_browser(self):
        """브라우저 시작 및 페이지 로드"""
        logger.info("🚀 자동매매 시스템 시작...")
        logger.info(f"계좌번호: {self.account_no}")
        logger.info(f"최대 투자금액: {self.max_investment:,}원")

        # 오늘 이미 매수했는지 확인
        if self.check_today_trading_done():
            logger.info("🚫 오늘 이미 매수를 실행했습니다.")
            logger.info("📊 브라우저 없이 WebSocket 매도 모니터링만 시작합니다.")
            self.order_executed = True  # 매수 플래그 설정하여 추가 매수 방지
            return

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.page = await self.browser.new_page()

        logger.info(f"페이지 로딩: {self.url}")
        await self.page.goto(self.url)
        await self.page.wait_for_load_state("networkidle")

        logger.info("✅ 페이지 로드 완료!")

    async def check_stock_data(self) -> dict | None:
        """현재 페이지에서 종목 데이터 확인"""
        if not self.page:
            return None

        # 페이지/브라우저가 닫혔는지 확인
        if self.page.is_closed():
            logger.warning("⚠️ 브라우저가 닫혔습니다. 모니터링을 중단합니다.")
            self.is_monitoring = False
            return None

        try:
            stock_data = await self.page.evaluate("""
                () => {
                    const h3Elements = Array.from(document.querySelectorAll('h3'));
                const stockNameH3 = h3Elements.find(h3 => h3.textContent.trim() === '종목이름');
                const stockName = stockNameH3?.nextElementSibling?.textContent?.trim() || '-';

                // 종목 데이터가 있으면 모든 데이터 수집
                if (stockName !== '-') {
                    const currentPriceH3 = h3Elements.find(h3 => h3.textContent.trim() === '현재가');
                    const currentPrice = currentPriceH3?.nextElementSibling?.textContent?.trim() || '-';

                    const changeRateH3 = h3Elements.find(h3 => h3.textContent.trim() === '등락률');
                    const changeRate = changeRateH3?.nextElementSibling?.textContent?.trim() || '-';

                    const entryPriceH3 = h3Elements.find(h3 => h3.textContent.trim() === '매수가');
                    const entryPrice = entryPriceH3?.nextElementSibling?.textContent?.trim() || '-';

                    const stopLossH3 = h3Elements.find(h3 => h3.textContent.trim() === '손절가');
                    const stopLoss = stopLossH3?.nextElementSibling?.textContent?.trim() || '-';

                    // 모든 div에서 레이블-값 쌍 찾기
                    const allDivs = Array.from(document.querySelectorAll('div'));

                    const codeLabel = allDivs.find(el => el.textContent?.trim() === '종목코드');
                    const stockCode = codeLabel?.nextElementSibling?.textContent?.trim() || '-';

                    const capLabel = allDivs.find(el => el.textContent?.trim() === '시가총액');
                    const marketCap = capLabel?.nextElementSibling?.textContent?.trim() || '-';

                    const volLabel = allDivs.find(el => el.textContent?.trim() === '거래량');
                    const volume = volLabel?.nextElementSibling?.textContent?.trim() || '-';

                    const progLabel = allDivs.find(el => el.textContent?.trim() === '프로그램');
                    const program = progLabel?.nextElementSibling?.textContent?.trim() || '-';

                    const viLabel = allDivs.find(el => el.textContent?.trim() === '정적 Vi (상승)');
                    const viPrice = viLabel?.nextElementSibling?.textContent?.trim() || '-';

                    const targetLabel = allDivs.find(el => el.textContent?.trim() === '목표가');
                    const targetPrice = targetLabel?.nextElementSibling?.textContent?.trim() || '-';

                    const high30Label = allDivs.find(el => el.textContent?.trim() === '거래 30일 고가');
                    const high30 = high30Label?.nextElementSibling?.textContent?.trim() || '-';

                    const high52Label = allDivs.find(el => el.textContent?.trim() === '52주 신고가');
                    const high52 = high52Label?.nextElementSibling?.textContent?.trim() || '-';

                    const inst7Label = allDivs.find(el => el.textContent?.trim() === '거래 7일 기관');
                    const inst7 = inst7Label?.nextElementSibling?.textContent?.trim() || '-';

                    const frgn7Label = allDivs.find(el => el.textContent?.trim() === '거래 7일 외국인');
                    const frgn7 = frgn7Label?.nextElementSibling?.textContent?.trim() || '-';

                    return {
                        timestamp: new Date().toISOString(),
                        종목명: stockName,
                        종목코드: stockCode,
                        현재가: currentPrice,
                        등락률: changeRate,
                        매수가: entryPrice,
                        목표가: targetPrice,
                        손절가: stopLoss,
                        시가총액: marketCap,
                        거래량: volume,
                        프로그램: program,
                        정적Vi상승: viPrice,
                        거래30일고가: high30,
                        주52신고가: high52,
                        거래7일기관: inst7,
                        거래7일외국인: frgn7,
                        hasData: true
                    };
                }

                return {
                    hasData: false,
                    isWaiting: true,
                    stockName: stockName
                };
                }
            """)
            return stock_data
        except Exception as e:
            # 브라우저가 닫혔거나 페이지 접근 불가 시
            logger.error(f"페이지 데이터 조회 실패: {e}")
            self.is_monitoring = False
            return None

    async def execute_auto_buy(self, stock_data: dict):
        """자동 매수 실행 (시장가 주문)"""
        stock_code = stock_data.get("종목코드", "")
        stock_name = stock_data.get("종목명", "")
        current_price_str = stock_data.get("현재가", "-")

        # 현재가 파싱 (시장가 주문이므로 현재가 기준으로 수량 계산)
        current_price = parse_price_string(current_price_str)

        if not stock_code or stock_code == "-":
            logger.error("❌ 종목코드를 찾을 수 없습니다.")
            return None

        if current_price <= 0:
            logger.error("❌ 유효한 현재가를 찾을 수 없습니다.")
            return None

        # 매수 수량 계산 (현재가 기준)
        quantity = self.kiwoom_api.calculate_order_quantity(current_price, self.max_investment)

        if quantity <= 0:
            logger.error("❌ 매수 가능 수량이 0입니다.")
            return None

        logger.info("=" * 60)
        logger.info(f"🎯 종목 포착! 시장가 즉시 매수를 시작합니다")
        logger.info(f"종목명: {stock_name}")
        logger.info(f"종목코드: {stock_code}")
        logger.info(f"현재가: {current_price:,}원")
        logger.info(f"매수 수량: {quantity}주")
        logger.info(f"예상 투자금액: {current_price * quantity:,}원 (시장가)")
        logger.info("=" * 60)

        # 키움 API로 시장가 매수 주문
        try:
            # Access Token 발급
            self.kiwoom_api.get_access_token()

            # 시장가 매수 주문 (즉시 체결)
            order_result = self.kiwoom_api.place_market_buy_order(
                stock_code=stock_code,
                quantity=quantity,
                account_no=self.account_no
            )

            # 결과 저장
            await self.save_trading_result(stock_data, order_result)

            return order_result

        except Exception as e:
            logger.error(f"❌ 매수 주문 실행 중 오류: {e}")
            return None

    async def start_websocket_monitoring(self):
        """WebSocket 실시간 시세 모니터링 시작"""
        try:
            # WebSocket 생성 및 연결
            self.websocket = KiwoomWebSocket(self.kiwoom_api)
            await self.websocket.connect()

            # 실시간 시세 등록 (콜백 함수 등록)
            await self.websocket.register_stock(
                self.buy_info["stock_code"],
                self.on_price_update
            )

            # 실시간 수신 태스크 시작
            self.ws_receive_task = asyncio.create_task(self.websocket.receive_loop())

            logger.info(f"✅ 실시간 시세 모니터링 시작: {self.buy_info['stock_name']} ({self.buy_info['stock_code']})")

        except Exception as e:
            logger.error(f"❌ WebSocket 모니터링 시작 실패: {e}")

    async def on_price_update(self, stock_code: str, current_price: int, data: dict):
        """
        실시간 시세 업데이트 콜백 함수

        Args:
            stock_code: 종목코드
            current_price: 현재가
            data: 전체 실시간 데이터
        """
        if current_price <= 0:
            return

        buy_price = self.buy_info["buy_price"]
        if buy_price <= 0:
            return

        # 현재 수익률 계산
        profit_rate = (current_price - buy_price) / buy_price

        # 로그 출력 (10초마다)
        if not hasattr(self, '_last_profit_log') or (datetime.now() - self._last_profit_log).seconds >= 10:
            logger.info(f"📊 [{stock_code}] 현재가: {current_price:,}원 | 수익률: {profit_rate*100:.2f}% (목표: 2.00%)")
            self._last_profit_log = datetime.now()

        # 목표 수익률(2%) 도달 확인
        if profit_rate >= self.buy_info["target_profit_rate"] and not self.sell_executed:
            await self.execute_auto_sell(current_price, profit_rate)

    async def execute_auto_sell(self, current_price: int, profit_rate: float):
        """자동 매도 실행"""
        # 중복 매도 방지 (재진입 방지)
        if self.sell_executed:
            logger.warning("⚠️ 이미 매도 주문을 실행했습니다. 중복 실행 방지")
            return

        self.sell_executed = True  # 즉시 플래그 설정 (중복 방지)

        logger.info("=" * 60)
        logger.info(f"🎯 목표 수익률 2% 도달! 자동 매도를 시작합니다")
        logger.info(f"매수가: {self.buy_info['buy_price']:,}원")
        logger.info(f"현재가: {current_price:,}원")
        logger.info(f"수익률: {profit_rate*100:.2f}%")
        logger.info("=" * 60)

        # 매도가 계산 (목표가에서 한 틱 아래)
        sell_price = calculate_sell_price(self.buy_info["buy_price"], self.buy_info["target_profit_rate"])

        logger.info(f"💰 매도 주문가: {sell_price:,}원 (목표가에서 한 틱 아래)")

        try:
            # 지정가 매도 주문
            sell_result = self.kiwoom_api.place_limit_sell_order(
                stock_code=self.buy_info["stock_code"],
                quantity=self.buy_info["quantity"],
                price=sell_price,
                account_no=self.account_no
            )

            if sell_result and sell_result.get("success"):
                logger.info("✅ 자동 매도 완료!")

                # WebSocket 모니터링 중지
                if self.websocket:
                    await self.websocket.unregister_stock(self.buy_info["stock_code"])
                    if self.ws_receive_task:
                        self.ws_receive_task.cancel()

                # 매도 결과 저장
                await self.save_sell_result_ws(current_price, sell_result, profit_rate)
            else:
                logger.error("❌ 자동 매도 실패")

        except Exception as e:
            logger.error(f"❌ 매도 주문 실행 중 오류: {e}")

    async def check_and_sell(self, stock_data: dict):
        """
        수익률 확인 및 자동 매도

        2% 수익률 도달 시 한 틱 아래 가격으로 지정가 매도
        """
        current_price_str = stock_data.get("현재가", "0")
        current_price = parse_price_string(current_price_str)

        if current_price <= 0:
            return

        buy_price = self.buy_info["buy_price"]
        if buy_price <= 0:
            return

        # 현재 수익률 계산
        profit_rate = (current_price - buy_price) / buy_price

        # 로그 출력 (10초마다)
        if not hasattr(self, '_last_profit_log') or (datetime.now() - self._last_profit_log).seconds >= 10:
            logger.info(f"📊 현재가: {current_price:,}원 | 수익률: {profit_rate*100:.2f}% (목표: 2.00%)")
            self._last_profit_log = datetime.now()

        # 목표 수익률(2%) 도달 확인
        if profit_rate >= self.buy_info["target_profit_rate"]:
            logger.info("=" * 60)
            logger.info(f"🎯 목표 수익률 2% 도달! 자동 매도를 시작합니다")
            logger.info(f"매수가: {buy_price:,}원")
            logger.info(f"현재가: {current_price:,}원")
            logger.info(f"수익률: {profit_rate*100:.2f}%")
            logger.info("=" * 60)

            # 매도가 계산 (목표가에서 한 틱 아래)
            sell_price = calculate_sell_price(buy_price, self.buy_info["target_profit_rate"])

            logger.info(f"💰 매도 주문가: {sell_price:,}원 (목표가에서 한 틱 아래)")

            try:
                # 지정가 매도 주문
                sell_result = self.kiwoom_api.place_limit_sell_order(
                    stock_code=self.buy_info["stock_code"],
                    quantity=self.buy_info["quantity"],
                    price=sell_price,
                    account_no=self.account_no
                )

                if sell_result and sell_result.get("success"):
                    logger.info("✅ 자동 매도 완료!")
                    self.sell_monitoring = False  # 매도 모니터링 중지

                    # 매도 결과 저장
                    await self.save_sell_result(stock_data, sell_result, profit_rate)
                else:
                    logger.error("❌ 자동 매도 실패")

            except Exception as e:
                logger.error(f"❌ 매도 주문 실행 중 오류: {e}")

    async def save_trading_result(self, stock_data: dict, order_result: dict):
        """매매 결과 저장 (매수)"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stock_name = stock_data.get("종목명", "unknown").replace("/", "_")

        result = {
            "timestamp": timestamp,
            "action": "BUY",
            "stock_info": stock_data,
            "order_result": order_result
        }

        filename = self.result_dir / f"{timestamp}_{stock_name}_매수결과.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 매수 결과 저장: {filename}")

    async def save_sell_result(self, stock_data: dict, order_result: dict, profit_rate: float):
        """매도 결과 저장 (웹페이지 기반)"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stock_name = stock_data.get("종목명", "unknown").replace("/", "_")

        result = {
            "timestamp": timestamp,
            "action": "SELL",
            "buy_info": self.buy_info,
            "current_price": parse_price_string(stock_data.get("현재가", "0")),
            "profit_rate": f"{profit_rate*100:.2f}%",
            "order_result": order_result
        }

        filename = self.result_dir / f"{timestamp}_{stock_name}_매도결과.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 매도 결과 저장: {filename}")

    async def save_sell_result_ws(self, current_price: int, order_result: dict, profit_rate: float):
        """매도 결과 저장 (WebSocket 기반)"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stock_name = self.buy_info["stock_name"].replace("/", "_")

        result = {
            "timestamp": timestamp,
            "action": "SELL",
            "buy_info": self.buy_info,
            "current_price": current_price,
            "profit_rate": f"{profit_rate*100:.2f}%",
            "order_result": order_result,
            "source": "WebSocket 실시간 시세"
        }

        filename = self.result_dir / f"{timestamp}_{stock_name}_매도결과.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 매도 결과 저장: {filename}")

    async def monitor_and_trade(self):
        """실시간 모니터링 및 자동 매매"""
        logger.info("🔍 종목 감시 시작...")
        logger.info("종목 포착 시간: 09:00 ~ 09:10")

        check_interval = 0.5  # 0.5초마다 체크 (빠른 감지)
        last_waiting_log_time = None  # 마지막 대기 로그 출력 시간

        while self.is_monitoring:
            try:
                stock_data = await self.check_stock_data()

                if stock_data and stock_data.get("hasData"):
                    if not self.order_executed:
                        logger.info(f"🎯 종목 포착! {stock_data.get('종목명')}")

                        # 자동 매수 실행
                        order_result = await self.execute_auto_buy(stock_data)

                        if order_result and order_result.get("success"):
                            logger.info("✅ 자동 매수 완료!")
                            self.order_executed = True

                            # 매수 정보 저장 (매도 모니터링용)
                            self.buy_info["stock_code"] = stock_data.get("종목코드")
                            self.buy_info["stock_name"] = stock_data.get("종목명")
                            self.buy_info["buy_price"] = parse_price_string(stock_data.get("현재가", "0"))
                            self.buy_info["quantity"] = order_result.get("quantity", 0)

                            # 오늘 매수 기록 저장 (하루 1회 제한)
                            self.record_today_trading(
                                stock_code=self.buy_info["stock_code"],
                                stock_name=self.buy_info["stock_name"],
                                buy_price=self.buy_info["buy_price"],
                                quantity=self.buy_info["quantity"]
                            )

                            # WebSocket 실시간 시세 모니터링 시작
                            logger.info("📈 WebSocket 실시간 시세 모니터링 시작 (목표: 2%)")
                            await self.start_websocket_monitoring()
                        else:
                            logger.error("❌ 자동 매수 실패")
                            # 실패해도 재시도하지 않음 (중복 주문 방지)
                            self.order_executed = True

                elif stock_data and stock_data.get("isWaiting"):
                    if not self.order_executed:
                        # 10초마다 한 번만 로그 출력 (로그 과다 방지)
                        now = datetime.now()
                        if last_waiting_log_time is None or (now - last_waiting_log_time).seconds >= 10:
                            logger.info("⏳ 종목 대기 중...")
                            last_waiting_log_time = now

                await asyncio.sleep(check_interval)

            except Exception as e:
                logger.error(f"모니터링 중 오류 발생: {e}")
                await asyncio.sleep(check_interval)

    async def start_auto_trading(self, duration: int = 600):
        """
        자동매매 시작

        Args:
            duration: 모니터링 지속 시간(초). 기본값 600초(10분)
        """
        try:
            await self.start_browser()

            # 오늘 이미 매수했는지 확인하고 매도 모니터링 시작
            trading_info = self.load_today_trading_info()
            if trading_info and self.order_executed:
                # 매수 정보 복원
                self.buy_info["stock_code"] = trading_info.get("stock_code")
                self.buy_info["stock_name"] = trading_info.get("stock_name")
                self.buy_info["buy_price"] = trading_info.get("buy_price", 0)
                self.buy_info["quantity"] = trading_info.get("quantity", 0)

                logger.info("=" * 60)
                logger.info(f"📥 매수 정보 복원 완료")
                logger.info(f"종목명: {self.buy_info['stock_name']}")
                logger.info(f"종목코드: {self.buy_info['stock_code']}")
                logger.info(f"매수가: {self.buy_info['buy_price']:,}원")
                logger.info(f"수량: {self.buy_info['quantity']}주")
                logger.info("=" * 60)

                # WebSocket 실시간 시세 모니터링 시작
                logger.info("📈 WebSocket 매도 모니터링 시작 (목표: 2%)")
                await self.start_websocket_monitoring()

                # WebSocket 모니터링이 계속 유지되도록 무한 대기
                logger.info("⏱️  2% 수익률 도달 또는 Ctrl+C로 종료할 때까지 매도 모니터링합니다...")
                logger.info("💡 매도 타이밍을 놓치지 않도록 계속 모니터링합니다.")

                # WebSocket receive_loop()가 계속 실행되므로 무한 대기
                # 매도 완료 시 ws_receive_task가 cancel되면서 종료됨
                if self.ws_receive_task:
                    try:
                        await self.ws_receive_task
                    except asyncio.CancelledError:
                        logger.info("✅ WebSocket 모니터링이 정상 종료되었습니다.")

            else:
                self.is_monitoring = True

                # 모니터링 태스크 시작
                monitor_task = asyncio.create_task(self.monitor_and_trade())

                # 지정된 시간 동안 대기
                logger.info(f"⏱️  {duration}초 동안 모니터링합니다...")
                await asyncio.sleep(duration)

                # 모니터링 중지
                self.is_monitoring = False
                await monitor_task

        except Exception as e:
            logger.error(f"오류 발생: {e}")
            raise

        finally:
            await self.cleanup()

    async def cleanup(self):
        """리소스 정리"""
        logger.info("리소스 정리 중...")

        # WebSocket 종료
        if self.ws_receive_task:
            self.ws_receive_task.cancel()
            try:
                await self.ws_receive_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            await self.websocket.close()

        # 브라우저 종료
        if self.page:
            await self.page.close()

        if hasattr(self, 'browser') and self.browser:
            await self.browser.close()

        if hasattr(self, 'playwright') and self.playwright:
            await self.playwright.stop()

        logger.info("✅ 자동매매 시스템 종료")


async def main():
    """메인 실행 함수"""
    # 환경변수에서 설정 읽기
    ACCOUNT_NO = os.getenv("ACCOUNT_NO", "12345678-01")
    MAX_INVESTMENT = int(os.getenv("MAX_INVESTMENT", "1000000"))

    # 자동매매 시스템 생성
    trading_system = AutoTradingSystem(
        account_no=ACCOUNT_NO,
        max_investment=MAX_INVESTMENT
    )

    # 10분(600초) 동안 모니터링 및 자동매매
    await trading_system.start_auto_trading(duration=600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n사용자에 의해 중단되었습니다.")
    except Exception as e:
        logger.error(f"프로그램 오류: {e}")
