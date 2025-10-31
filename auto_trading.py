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
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich import box

# 환경변수 로드
load_dotenv()

# 로깅 설정 (200MB 제한, 최대 3개 백업 파일)
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 로그 포맷 설정
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# 콘솔 핸들러 (항상 추가 - fallback 보장)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 파일 핸들러 (안전하게 추가 - 실패해도 프로그램 계속 실행)
try:
    # 로그 디렉토리 생성 (없으면 자동 생성)
    import os
    log_dir = os.path.dirname('auto_trading.log')
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 파일 핸들러 생성 (200MB 제한, 최대 3개 백업)
    file_handler = RotatingFileHandler(
        'auto_trading.log',
        maxBytes=200 * 1024 * 1024,  # 200MB
        backupCount=3,                # 최대 3개 백업 파일 유지
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

except Exception as e:
    # 로그 파일 생성 실패 시 경고만 출력하고 계속 진행 (콘솔 전용 모드)
    print(f"⚠️ 로그 파일 생성 실패: {e}")
    print(f"📝 콘솔 전용 모드로 실행됩니다. 로그는 파일에 저장되지 않습니다.")
    print(f"💡 해결 방법: 1) 디스크 용량 확인, 2) 파일 쓰기 권한 확인, 3) 다른 프로세스가 로그 파일을 사용 중인지 확인")


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
        self.sell_order_no = None  # 매도 주문번호 저장

        # 목표 수익률 환경변수에서 읽기 (기본값: 1.0%)
        target_profit_rate_percent = float(os.getenv("TARGET_PROFIT_RATE", "1.0"))
        target_profit_rate = target_profit_rate_percent / 100  # 퍼센트를 소수로 변환

        # 미체결 처리 설정 환경변수에서 읽기
        self.cancel_outstanding_on_failure = os.getenv("CANCEL_OUTSTANDING_ON_FAILURE", "true").lower() == "true"
        self.outstanding_check_timeout = int(os.getenv("OUTSTANDING_CHECK_TIMEOUT", "30"))  # 초
        self.outstanding_check_interval = int(os.getenv("OUTSTANDING_CHECK_INTERVAL", "5"))  # 초

        # 매수 정보 저장
        self.buy_info = {
            "stock_code": None,
            "stock_name": None,
            "buy_price": 0,
            "quantity": 0,
            "buy_time": None,  # 매수 시간 (손절 지연용)
            "target_profit_rate": target_profit_rate  # 환경변수에서 읽어온 목표 수익률
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

        # DEBUG 모드 설정 (환경변수에서 읽기)
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        if self.debug_mode:
            logger.info("🐛 DEBUG 모드 활성화: 실시간 시세를 계속 출력합니다")

        # 매수 가능 시간 설정 (환경변수에서 읽기)
        self.buy_start_time = os.getenv("BUY_START_TIME", "09:00")
        self.buy_end_time = os.getenv("BUY_END_TIME", "09:10")

        # 매도 모니터링 활성화 여부 (환경변수에서 읽기)
        self.enable_sell_monitoring = os.getenv("ENABLE_SELL_MONITORING", "true").lower() == "true"
        if not self.enable_sell_monitoring:
            logger.info("⏸️  매도 모니터링이 비활성화되었습니다 (ENABLE_SELL_MONITORING=false)")

        # 손절 모니터링 활성화 여부 (환경변수에서 읽기)
        self.enable_stop_loss = os.getenv("ENABLE_STOP_LOSS", "true").lower() == "true"

        # 손절 수익률 환경변수에서 읽기 (기본값: -2.5%)
        stop_loss_rate_percent = float(os.getenv("STOP_LOSS_RATE", "-2.5"))
        self.stop_loss_rate = stop_loss_rate_percent / 100  # 퍼센트를 소수로 변환

        # 손절 지연 시간 환경변수에서 읽기 (기본값: 1분)
        self.stop_loss_delay_minutes = int(os.getenv("STOP_LOSS_DELAY_MINUTES", "1"))

        if self.enable_stop_loss:
            logger.info(f"🛡️  손절 모니터링 활성화: {stop_loss_rate_percent}% 이하 시 시장가 매도")
            if self.stop_loss_delay_minutes > 0:
                logger.info(f"⏱️  손절 지연 설정: 매수 후 {self.stop_loss_delay_minutes}분 이후부터 손절 가능")
            else:
                logger.info("⏱️  손절 지연 없음: 즉시 손절 가능")
        else:
            logger.info("⏸️  손절 모니터링이 비활성화되었습니다 (ENABLE_STOP_LOSS=false)")

        # 일일 강제 청산 활성화 여부 (환경변수에서 읽기)
        self.enable_daily_force_sell = os.getenv("ENABLE_DAILY_FORCE_SELL", "true").lower() == "true"

        # 일일 강제 청산 시간 환경변수에서 읽기 (기본값: 15:19)
        self.daily_force_sell_time = os.getenv("DAILY_FORCE_SELL_TIME", "15:19")

        # 실시간 체결 정보 검증 활성화 여부 (환경변수에서 읽기, 기본값: false)
        self.enable_lazy_verification = os.getenv("ENABLE_LAZY_VERIFICATION", "false").lower() == "true"

        if self.enable_lazy_verification:
            logger.info("⚙️ 실시간 체결 정보 검증: 활성화 (개선 모드 - 즉시 모니터링 + 자동 업데이트)")
        else:
            logger.info("⚙️ 실시간 체결 정보 검증: 비활성화 (기존 모드 - 추정값만 사용)")

        if self.enable_daily_force_sell:
            logger.info(f"⏰ 일일 강제 청산 활성화: {self.daily_force_sell_time}에 100% 전량 시장가 매도")
        else:
            logger.info("⏸️  일일 강제 청산이 비활성화되었습니다 (ENABLE_DAILY_FORCE_SELL=false)")

        # Rich Console 초기화
        self.console = Console()
        self.live_display = None  # Live 디스플레이 객체

        # 주기적 계좌 조회 설정 (환경변수에서 읽기, 기본값: 30초)
        self.balance_check_interval = int(os.getenv("BALANCE_CHECK_INTERVAL", "30"))
        self._last_balance_check = None  # 마지막 계좌 조회 시간

        if self.balance_check_interval > 0:
            logger.info(f"🔄 주기적 평균단가 업데이트: {self.balance_check_interval}초마다 계좌 조회")
        else:
            logger.info("⏸️  주기적 평균단가 업데이트 비활성화 (BALANCE_CHECK_INTERVAL=0)")

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

    def record_today_trading(self, stock_code: str, stock_name: str, buy_price: int, quantity: int, buy_time: datetime = None):
        """
        오늘 매수 기록 저장

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            buy_price: 매수가
            quantity: 매수 수량
            buy_time: 매수 시간 (선택적, 자동 매수만 전달)
        """
        try:
            lock_data = {
                "last_trading_date": datetime.now().strftime("%Y%m%d"),
                "stock_code": stock_code,
                "stock_name": stock_name,
                "buy_price": buy_price,
                "quantity": quantity
            }

            # buy_time이 있을 때만 trading_time 필드 추가 (자동 매수만)
            if buy_time is not None:
                lock_data["trading_time"] = buy_time.strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"✅ 오늘 매수 기록 저장 완료 (매수 시간: {lock_data['trading_time']})")
            else:
                logger.info(f"✅ 오늘 매수 기록 저장 완료 (수동 매수 - 손절 지연 없음)")

            with open(self.trading_lock_file, 'w', encoding='utf-8') as f:
                json.dump(lock_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"매수 기록 저장 중 오류: {e}")

    def load_today_trading_info(self) -> dict | None:
        """
        오늘 매수 정보 로드 (실제 계좌 잔고 조회)

        Returns:
            실제 계좌의 보유 종목 정보 또는 None
        """
        try:
            # 실제 계좌 잔고 조회
            logger.info("📊 실제 계좌 잔고 조회 중...")
            balance_result = self.kiwoom_api.get_account_balance()

            if not balance_result.get("success"):
                logger.warning("⚠️ 계좌 잔고 조회 실패")
                return None

            holdings = balance_result.get("holdings", [])

            if not holdings:
                logger.info("ℹ️ 보유 종목이 없습니다.")
                return None

            # 첫 번째 보유 종목 반환 (자동매매 시스템은 1종목만 관리)
            first_holding = holdings[0]

            trading_info = {
                "stock_code": first_holding.get("stk_cd", ""),
                "stock_name": first_holding.get("stk_nm", ""),
                "buy_price": int(first_holding.get("buy_uv", 0)),
                "quantity": int(first_holding.get("rmnd_qty", 0)),  # 보유수량 (rmnd_qty)
                "current_price": int(first_holding.get("cur_prc", 0)),  # 현재가 (cur_prc)
                "buy_time": None  # 기본값
            }

            # daily_trading_lock.json에서 매수 시간 로드 시도
            if self.trading_lock_file.exists():
                try:
                    with open(self.trading_lock_file, 'r', encoding='utf-8') as f:
                        lock_data = json.load(f)

                    # trading_time을 datetime 객체로 변환
                    trading_time_str = lock_data.get("trading_time")
                    if trading_time_str:
                        trading_info["buy_time"] = datetime.strptime(trading_time_str, "%Y-%m-%d %H:%M:%S")
                        logger.info(f"📅 매수 시간: {trading_time_str}")

                except Exception as e:
                    logger.warning(f"⚠️ 매수 시간 로드 실패: {e}")

            logger.info("=" * 60)
            logger.info("📥 실제 계좌 보유 종목 확인")
            logger.info(f"종목명: {trading_info['stock_name']}")
            logger.info(f"종목코드: {trading_info['stock_code']}")
            logger.info(f"매입단가: {trading_info['buy_price']:,}원")
            logger.info(f"보유수량: {trading_info['quantity']}주")
            logger.info(f"현재가: {trading_info['current_price']:,}원")
            logger.info("=" * 60)

            return trading_info

        except Exception as e:
            logger.error(f"❌ 계좌 정보 조회 중 오류: {e}")
            return None

    async def start_browser(self):
        """브라우저 시작 및 페이지 로드"""
        logger.info("🚀 브라우저 시작...")
        logger.info(f"계좌번호: {self.account_no}")
        logger.info(f"최대 투자금액: {self.max_investment:,}원")
        logger.info(f"🎯 목표 수익률: {self.buy_info['target_profit_rate']*100:.2f}%")

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

            # 매수 정보 저장 (추정값 또는 개선 모드용 초기값)
            buy_time = datetime.now()  # 매수 시간 기록
            self.buy_info = {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "buy_price": current_price,  # 추정값 (시장가 주문 시점 현재가)
                "quantity": quantity,         # 추정값
                "buy_time": buy_time,         # 매수 시간
                "target_profit_rate": self.buy_info["target_profit_rate"],
                "is_verified": not self.enable_lazy_verification  # 개선 모드면 False (자동 검증 필요)
            }

            # 결과 저장
            await self.save_trading_result(stock_data, order_result)

            # 매수 완료 로그
            logger.info("✅ 자동 매수 완료!")

            if self.enable_lazy_verification:
                logger.info("⚡ 즉시 매도 모니터링을 시작합니다 (추정 매수가 기준)")
                logger.info("   첫 번째 실시간 시세 수신 시 실제 체결 정보를 자동으로 확인합니다")
            else:
                logger.info("📝 추정 매수가로 매도 모니터링을 시작합니다")

            return order_result

        except Exception as e:
            logger.error(f"❌ 매수 주문 실행 중 오류: {e}")
            return None

    async def start_websocket_monitoring(self):
        """WebSocket 실시간 시세 모니터링 시작"""
        try:
            # WebSocket 생성 및 연결 (debug_mode 전달)
            self.websocket = KiwoomWebSocket(self.kiwoom_api, debug_mode=self.debug_mode)
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

    def create_price_table(self, current_price: int, buy_price: int, profit_rate: float, source: str = "REST API") -> Table:
        """실시간 시세 정보 테이블 생성"""
        table = Table(title=f"📊 실시간 시세 정보 ({source})", box=box.ROUNDED, show_header=False)
        table.add_column("항목", style="cyan", width=15)
        table.add_column("값", style="white")

        # 수익률에 따른 색상 결정 (한국 주식시장 관례: 수익=빨강, 손실=파랑)
        profit_color = "red" if profit_rate >= 0 else "blue"
        profit_sign = "+" if profit_rate >= 0 else ""

        table.add_row("종목명", self.buy_info['stock_name'])
        table.add_row("종목코드", self.buy_info['stock_code'])
        table.add_row("평균 매수가", f"{buy_price:,}원")
        table.add_row("현재가", f"{current_price:,}원")
        table.add_row("수익률", f"[{profit_color}]{profit_sign}{profit_rate*100:.2f}%[/{profit_color}] (목표: +{self.buy_info['target_profit_rate']*100:.2f}%)")
        table.add_row("수익금", f"[{profit_color}]{profit_sign}{(current_price - buy_price) * self.buy_info['quantity']:,}원[/{profit_color}]")
        table.add_row("보유수량", f"{self.buy_info['quantity']:,}주")
        table.add_row("업데이트", datetime.now().strftime("%H:%M:%S"))

        return table

    async def price_polling_loop(self):
        """REST API로 10초마다 현재가 조회 (WebSocket 백업)"""
        logger.info("🔄 REST API 백업 폴링 시작 (10초 간격)")
        await asyncio.sleep(10)  # 첫 10초 대기

        # 콘솔 클리어 (Rich 테이블 시작 전)
        self.console.clear()

        # 초기 테이블 생성
        initial_table = self.create_price_table(0, self.buy_info["buy_price"], 0.0, "대기 중")

        # Rich Live 디스플레이 시작 (screen=True로 전체 화면 제어)
        with Live(
            initial_table,
            console=self.console,
            refresh_per_second=4,
            screen=True
        ) as live:
            self.live_display = live

            while not self.sell_executed:
                try:
                    # REST API로 현재가 조회
                    result = self.kiwoom_api.get_current_price(self.buy_info["stock_code"])

                    if result.get("success"):
                        current_price = result.get("current_price", 0)

                        if current_price > 0:
                            buy_price = self.buy_info["buy_price"]
                            profit_rate = (current_price - buy_price) / buy_price

                            # Rich 테이블로 화면 갱신
                            table = self.create_price_table(current_price, buy_price, profit_rate, "REST API")
                            live.update(table)

                            # 목표 수익률 도달 확인
                            if profit_rate >= self.buy_info["target_profit_rate"]:
                                logger.info("🎯 REST API로 목표 수익률 도달 확인!")
                                await self.execute_auto_sell(current_price, profit_rate)
                                break
                        else:
                            logger.warning(f"⚠️ REST API 현재가가 0입니다: {result}")
                    else:
                        logger.error(f"❌ REST API 현재가 조회 실패: {result}")

                except Exception as e:
                    logger.error(f"❌ 현재가 조회 중 오류: {e}")

                # 10초 대기
                await asyncio.sleep(10)

            self.live_display = None

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

        # ⭐ Lazy Verification: 첫 시세 수신 시 실제 체결 정보 확인
        if self.enable_lazy_verification and not self.buy_info.get("is_verified", False):
            logger.info("🔄 실제 체결 정보를 확인합니다...")

            try:
                balance_result = self.kiwoom_api.get_account_balance()

                if balance_result.get("success"):
                    holdings = balance_result.get("holdings", [])

                    # 해당 종목 찾기
                    for holding in holdings:
                        if holding.get("stk_cd") == stock_code:
                            actual_price = int(holding.get("buy_uv") or 0)  # 평균 매입단가
                            actual_quantity = int(holding.get("rmnd_qty") or 0)  # 보유 수량

                            if actual_price > 0 and actual_quantity > 0:
                                # 추정값과 비교
                                price_diff = actual_price - self.buy_info["buy_price"]
                                quantity_diff = actual_quantity - self.buy_info["quantity"]

                                # 실제 체결 정보로 업데이트
                                self.buy_info["buy_price"] = actual_price
                                self.buy_info["quantity"] = actual_quantity
                                self.buy_info["is_verified"] = True

                                # 파일에도 실제값 저장
                                self.record_today_trading(
                                    stock_code=stock_code,
                                    stock_name=self.buy_info["stock_name"],
                                    buy_price=actual_price,
                                    quantity=actual_quantity,
                                    buy_time=self.buy_info.get("buy_time")
                                )

                                logger.info("✅ 실제 체결 정보 확인 완료!")
                                logger.info(f"   실제 평균 매입단가: {actual_price:,}원 (예상 대비 {price_diff:+,}원)")
                                logger.info(f"   실제 체결 수량: {actual_quantity:,}주 (예상 대비 {quantity_diff:+,}주)")
                                logger.info(f"   실제 투자금액: {actual_price * actual_quantity:,}원")
                            break
                    else:
                        logger.warning("⚠️ 계좌에서 해당 종목을 찾을 수 없습니다. 추정값으로 계속 진행합니다.")
                        self.buy_info["is_verified"] = True  # 재시도 방지
                else:
                    logger.warning("⚠️ 계좌 조회 실패! 추정값으로 계속 진행합니다.")
                    self.buy_info["is_verified"] = True  # 재시도 방지

            except Exception as e:
                logger.error(f"❌ 체결 정보 확인 중 오류: {e}")
                self.buy_info["is_verified"] = True  # 실패 시에도 플래그 설정 (무한 재시도 방지)

        buy_price = self.buy_info["buy_price"]
        if buy_price <= 0:
            return

        # ⭐ 주기적 계좌 조회 (수동 매수 대응)
        if self.balance_check_interval > 0:
            now = datetime.now()
            should_check_balance = (
                self._last_balance_check is None or
                (now - self._last_balance_check).total_seconds() >= self.balance_check_interval
            )

            if should_check_balance:
                try:
                    balance_result = self.kiwoom_api.get_account_balance()

                    if balance_result.get("success"):
                        holdings = balance_result.get("holdings", [])

                        for holding in holdings:
                            if holding.get("stk_cd") == stock_code:
                                actual_buy_price = int(holding.get("buy_uv") or 0)
                                actual_quantity = int(holding.get("rmnd_qty") or 0)

                                # 평균 매입단가 또는 수량 변경 감지
                                if actual_buy_price > 0 and (
                                    actual_buy_price != self.buy_info["buy_price"] or
                                    actual_quantity != self.buy_info["quantity"]
                                ):
                                    old_price = self.buy_info["buy_price"]
                                    old_quantity = self.buy_info["quantity"]

                                    # 업데이트
                                    self.buy_info["buy_price"] = actual_buy_price
                                    self.buy_info["quantity"] = actual_quantity

                                    # 파일에도 저장
                                    self.record_today_trading(
                                        stock_code=stock_code,
                                        stock_name=self.buy_info["stock_name"],
                                        buy_price=actual_buy_price,
                                        quantity=actual_quantity,
                                        buy_time=self.buy_info.get("buy_time")
                                    )

                                    logger.warning("=" * 80)
                                    logger.warning("🔄 수동 매수 감지! 평균 매입단가 업데이트")
                                    logger.warning(f"   평균 매입단가: {old_price:,}원 → {actual_buy_price:,}원 ({actual_buy_price - old_price:+,}원)")
                                    logger.warning(f"   보유 수량: {old_quantity:,}주 → {actual_quantity:,}주 ({actual_quantity - old_quantity:+,}주)")
                                    logger.warning(f"   투자금액: {old_price * old_quantity:,}원 → {actual_buy_price * actual_quantity:,}원")
                                    logger.warning("=" * 80)

                                    # buy_price 재설정 (수익률 계산용)
                                    buy_price = actual_buy_price
                                break

                    self._last_balance_check = now

                except Exception as e:
                    logger.error(f"❌ 주기적 계좌 조회 중 오류: {e}")
                    self._last_balance_check = now  # 오류 시에도 타이머 리셋

        # 현재 수익률 계산
        profit_rate = (current_price - buy_price) / buy_price

        # DEBUG 모드일 때만 실시간 시세 출력
        if self.debug_mode:
            # Rich 테이블로 화면 갱신 (1초마다)
            if not hasattr(self, '_last_profit_log') or (datetime.now() - self._last_profit_log).total_seconds() >= 1:
                # Live 디스플레이가 활성화되어 있으면 테이블 갱신
                if self.live_display:
                    table = self.create_price_table(current_price, buy_price, profit_rate, "WebSocket")
                    self.live_display.update(table)

                self._last_profit_log = datetime.now()

        # 강제 청산 시간 체크 (최우선 - 손절/익절보다 우선)
        if self.enable_daily_force_sell and self.is_force_sell_time() and not self.sell_executed:
            await self.execute_daily_force_sell()
            return

        # 손절 조건 체크 (손절이 목표 수익률보다 우선)
        if self.enable_stop_loss and profit_rate <= self.stop_loss_rate and not self.sell_executed:
            # 매수 후 경과 시간 체크 (손절 지연 설정)
            buy_time = self.buy_info.get("buy_time")
            if buy_time and self.stop_loss_delay_minutes > 0:
                elapsed_minutes = (datetime.now() - buy_time).total_seconds() / 60
                if elapsed_minutes < self.stop_loss_delay_minutes:
                    # 손절 지연 시간 이내면 손절하지 않음
                    if self.debug_mode:
                        logger.debug(f"⏱️  손절 지연: 매수 후 {elapsed_minutes:.1f}분 경과 (설정: {self.stop_loss_delay_minutes}분 이후부터 손절)")
                    return

            # 캐시된 평균단가로 즉시 손절 실행 (180ms 절약)
            await self.execute_stop_loss(current_price, profit_rate)
            return

        # 목표 수익률 도달 확인
        if profit_rate >= self.buy_info["target_profit_rate"] and not self.sell_executed:
            # 캐시된 평균단가로 즉시 익절 실행 (180ms 절약)
            await self.execute_auto_sell(current_price, profit_rate)

    async def execute_auto_sell(self, current_price: int, profit_rate: float):
        """자동 매도 실행 (100% 전량 매도)"""
        # 중복 매도 방지 (재진입 방지)
        if self.sell_executed:
            logger.warning("⚠️ 이미 매도 주문을 실행했습니다. 중복 실행 방지")
            return

        self.sell_executed = True  # 즉시 플래그 설정 (중복 방지)

        logger.info("=" * 60)
        logger.info(f"🎯 목표 수익률 {self.buy_info['target_profit_rate']*100:.2f}% 도달! 자동 매도를 시작합니다")
        logger.info(f"매수가: {self.buy_info['buy_price']:,}원")
        logger.info(f"현재가: {current_price:,}원")
        logger.info(f"수익률: {profit_rate*100:.2f}%")
        logger.info("=" * 60)

        # 캐시된 보유 정보 사용 (180ms 절약, 수동 매수 시 재시작 필요)
        actual_quantity = self.buy_info["quantity"]
        actual_buy_price = self.buy_info["buy_price"]

        logger.info(f"💰 매도 수량: {actual_quantity}주 (캐시 기반 100% 전량)")
        logger.info(f"💰 평균 매입단가: {actual_buy_price:,}원 (캐시 기반)")

        # 매도 수량이 0이면 중단
        if actual_quantity <= 0:
            logger.error("❌ 매도할 수량이 0입니다. 매도를 중단합니다.")
            return

        # 매도가 계산 (현재가에서 한 틱 아래)
        sell_price = calculate_sell_price(current_price)

        logger.info(f"💰 매도 주문가: {sell_price:,}원 (현재가에서 한 틱 아래)")

        try:
            # 지정가 매도 주문 (실제 보유 수량으로)
            sell_result = self.kiwoom_api.place_limit_sell_order(
                stock_code=self.buy_info["stock_code"],
                quantity=actual_quantity,  # 실제 보유 수량
                price=sell_price,
                account_no=self.account_no
            )

            if sell_result and sell_result.get("success"):
                # 주문번호 저장
                self.sell_order_no = sell_result.get("order_no")
                logger.info(f"✅ 지정가 매도 주문 접수! 주문번호: {self.sell_order_no}")
                logger.info(f"⏳ 체결 확인 중... (최대 {self.outstanding_check_timeout}초 대기)")

                # 체결 확인 대기
                is_executed = await self.wait_for_sell_execution(
                    order_no=self.sell_order_no,
                    current_price=current_price,
                    profit_rate=profit_rate,
                    actual_quantity=actual_quantity,
                    actual_buy_price=actual_buy_price
                )

                if is_executed:
                    logger.info("✅ 자동 매도 완료!")

                    # WebSocket 모니터링 중지
                    if self.websocket:
                        await self.websocket.unregister_stock(self.buy_info["stock_code"])
                        if self.ws_receive_task:
                            self.ws_receive_task.cancel()

                    # 매도 결과 저장
                    await self.save_sell_result_ws(current_price, sell_result, profit_rate, actual_quantity, actual_buy_price)
                else:
                    # 미체결 시 처리
                    await self.handle_outstanding_order(
                        order_no=self.sell_order_no,
                        stock_code=self.buy_info["stock_code"],
                        quantity=actual_quantity
                    )
            else:
                logger.error("❌ 자동 매도 실패")
                self.sell_executed = False  # 주문 실패 시 플래그 해제 (재시도 가능)

        except Exception as e:
            logger.error(f"❌ 매도 주문 실행 중 오류: {e}")
            self.sell_executed = False  # 오류 시 플래그 해제

    async def wait_for_sell_execution(
        self,
        order_no: str,
        current_price: int,
        profit_rate: float,
        actual_quantity: int,
        actual_buy_price: int
    ) -> bool:
        """
        매도 주문 체결 대기 및 확인

        Args:
            order_no: 주문번호
            current_price: 현재가
            profit_rate: 수익률
            actual_quantity: 실제 매도 수량
            actual_buy_price: 실제 평균 매입단가

        Returns:
            체결 완료 여부 (True: 체결 완료, False: 미체결)
        """
        elapsed_time = 0
        check_count = 0

        while elapsed_time < self.outstanding_check_timeout:
            await asyncio.sleep(self.outstanding_check_interval)
            elapsed_time += self.outstanding_check_interval
            check_count += 1

            logger.info(f"🔍 체결 확인 {check_count}회차 (경과: {elapsed_time}초/{self.outstanding_check_timeout}초)")

            # 체결 여부 확인
            execution_result = self.kiwoom_api.check_order_execution(order_no)

            if not execution_result.get("success"):
                logger.warning(f"⚠️ 체결 확인 실패: {execution_result.get('message', '알 수 없는 오류')}")
                continue

            if execution_result.get("is_executed"):
                logger.info(f"✅ 매도 주문 체결 완료! (소요 시간: {elapsed_time}초)")
                return True
            else:
                remaining_qty = execution_result.get("remaining_qty", 0)
                logger.info(f"⏳ 아직 미체결 상태입니다 (미체결 수량: {remaining_qty}주)")

        # 타임아웃
        logger.warning(f"⚠️ 체결 확인 타임아웃 ({self.outstanding_check_timeout}초 경과)")
        return False

    async def handle_outstanding_order(
        self,
        order_no: str,
        stock_code: str,
        quantity: int
    ):
        """
        미체결 주문 처리 (취소 또는 유지)

        Args:
            order_no: 주문번호
            stock_code: 종목코드
            quantity: 주문 수량
        """
        logger.info("=" * 80)
        logger.info("⚠️ 매도 주문이 체결되지 않았습니다!")
        logger.info(f"주문번호: {order_no}")
        logger.info(f"종목코드: {stock_code}")
        logger.info(f"주문수량: {quantity}주")

        if self.cancel_outstanding_on_failure:
            logger.info("🔄 미체결 주문 취소 후 재모니터링을 시작합니다...")

            # 주문 취소
            cancel_result = self.kiwoom_api.cancel_order(
                order_no=order_no,
                stock_code=stock_code,
                quantity=quantity
            )

            if cancel_result.get("success"):
                logger.info("✅ 미체결 주문 취소 완료!")
                logger.info("📈 실시간 시세 모니터링을 계속합니다...")

                # 플래그 해제하여 재매도 가능하게
                self.sell_executed = False
                self.sell_order_no = None
            else:
                logger.error(f"❌ 주문 취소 실패: {cancel_result.get('message', '알 수 없는 오류')}")
                logger.info("📈 주문은 유지되며, 실시간 시세 모니터링을 계속합니다...")
                # 플래그는 유지 (중복 주문 방지)
        else:
            logger.info("📌 미체결 주문을 유지하고 실시간 시세 모니터링을 계속합니다...")
            logger.info("💡 .env의 CANCEL_OUTSTANDING_ON_FAILURE=true로 설정하면 자동 취소됩니다")
            # 플래그는 유지 (중복 주문 방지)

        logger.info("=" * 80)

    async def execute_stop_loss(self, current_price: int, profit_rate: float):
        """손절 실행 (시장가 즉시 매도)"""
        # 중복 매도 방지 (재진입 방지)
        if self.sell_executed:
            logger.warning("⚠️ 이미 매도 주문을 실행했습니다. 중복 실행 방지")
            return

        self.sell_executed = True  # 즉시 플래그 설정 (중복 방지)

        logger.info("=" * 60)
        logger.info(f"🚨 손절 조건 도달! ({self.stop_loss_rate*100:.2f}% 이하)")
        logger.info(f"매수가: {self.buy_info['buy_price']:,}원")
        logger.info(f"현재가: {current_price:,}원")
        logger.info(f"손실률: {profit_rate*100:.2f}%")
        logger.info("=" * 60)

        # 캐시된 보유 정보 사용 (180ms 절약, 수동 매수 시 재시작 필요)
        actual_quantity = self.buy_info["quantity"]
        actual_buy_price = self.buy_info["buy_price"]

        logger.info(f"💰 손절 수량: {actual_quantity}주 (캐시 기반 100% 전량)")
        logger.info(f"💰 평균 매입단가: {actual_buy_price:,}원 (캐시 기반)")

        # 매도 수량이 0이면 중단
        if actual_quantity <= 0:
            logger.error("❌ 매도할 수량이 0입니다. 손절을 중단합니다.")
            return

        try:
            # 시장가 매도 주문 (즉시 체결)
            sell_result = self.kiwoom_api.place_market_sell_order(
                stock_code=self.buy_info["stock_code"],
                quantity=actual_quantity,
                account_no=self.account_no
            )

            if sell_result and sell_result.get("success"):
                logger.info("✅ 손절 매도 완료!")

                # WebSocket 모니터링 중지
                if self.websocket:
                    await self.websocket.unregister_stock(self.buy_info["stock_code"])
                    if self.ws_receive_task:
                        self.ws_receive_task.cancel()

                # 손절 결과 저장
                await self.save_stop_loss_result(current_price, sell_result, profit_rate, actual_quantity, actual_buy_price)
            else:
                logger.error("❌ 손절 매도 실패")

        except Exception as e:
            logger.error(f"❌ 손절 주문 실행 중 오류: {e}")

    async def execute_daily_force_sell(self):
        """일일 강제 청산 실행 (100% 전량 시장가 매도)"""
        # 중복 매도 방지
        if self.sell_executed:
            logger.warning("⚠️ 이미 매도 주문을 실행했습니다. 중복 실행 방지")
            return

        self.sell_executed = True  # 즉시 플래그 설정 (중복 방지)

        logger.info("=" * 80)
        logger.info(f"⏰ 강제 청산 시간 도달! ({self.daily_force_sell_time})")
        logger.info(f"💰 보유 종목을 100% 전량 시장가 매도합니다")
        logger.info("=" * 80)

        # 1단계: 미체결 주문 확인 및 취소
        logger.info("🔍 강제 청산 전 미체결 주문 확인 중...")
        outstanding_result = self.kiwoom_api.get_outstanding_orders()

        if outstanding_result.get("success"):
            outstanding_orders = outstanding_result.get("outstanding_orders", [])

            if outstanding_orders:
                logger.warning(f"⚠️ 미체결 주문 {len(outstanding_orders)}건 발견!")
                logger.info("🔄 강제 청산을 위해 모든 미체결 주문을 취소합니다...")

                # 모든 미체결 주문 취소
                for order in outstanding_orders:
                    order_no = order.get("ord_no", "")
                    stock_code = order.get("stk_cd", "")
                    remaining_qty = int(order.get("rmnd_qty", order.get("ord_qty", "0")))

                    logger.info(f"  ❌ 미체결 주문 취소 중: 주문번호={order_no}, 종목={stock_code}, 수량={remaining_qty}주")

                    cancel_result = self.kiwoom_api.cancel_order(
                        order_no=order_no,
                        stock_code=stock_code,
                        quantity=remaining_qty
                    )

                    if cancel_result.get("success"):
                        logger.info(f"  ✅ 주문 취소 완료: {order_no}")
                    else:
                        logger.error(f"  ❌ 주문 취소 실패: {order_no} - {cancel_result.get('message', '알 수 없는 오류')}")

                logger.info("✅ 미체결 주문 취소 처리 완료")
            else:
                logger.info("✅ 미체결 주문이 없습니다")
        else:
            logger.warning("⚠️ 미체결 주문 확인 실패. 강제 청산을 계속 진행합니다.")

        logger.info("=" * 80)

        # 캐시된 보유 정보 사용 (180ms 절약, 수동 매수 시 재시작 필요)
        actual_quantity = self.buy_info["quantity"]
        actual_buy_price = self.buy_info["buy_price"]

        logger.info(f"💰 강제 청산 수량: {actual_quantity}주 (캐시 기반 100% 전량)")
        logger.info(f"💰 평균 매입단가: {actual_buy_price:,}원 (캐시 기반)")

        try:
            # 시장가 매도 주문
            sell_result = self.kiwoom_api.place_market_sell_order(
                stock_code=self.buy_info["stock_code"],
                quantity=actual_quantity,
                account_no=self.account_no
            )

            if sell_result and sell_result.get("success"):
                logger.info("✅ 강제 청산 완료!")

                # WebSocket 모니터링 중지
                if self.websocket:
                    await self.websocket.unregister_stock(self.buy_info["stock_code"])
                    if self.ws_receive_task:
                        self.ws_receive_task.cancel()

                # 현재가 조회 (수익률 계산용)
                current_price = 0
                price_result = self.kiwoom_api.get_current_price(self.buy_info["stock_code"])
                if price_result.get("success"):
                    current_price = price_result.get("current_price", 0)

                profit_rate = 0
                if actual_buy_price > 0 and current_price > 0:
                    profit_rate = (current_price - actual_buy_price) / actual_buy_price

                # 강제 청산 결과 저장
                await self.save_force_sell_result(current_price, sell_result, profit_rate, actual_quantity, actual_buy_price)
            else:
                logger.error("❌ 강제 청산 실패")

        except Exception as e:
            logger.error(f"❌ 강제 청산 주문 실행 중 오류: {e}")

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
            logger.info(f"📊 현재가: {current_price:,}원 | 수익률: {profit_rate*100:.2f}% (목표: {self.buy_info['target_profit_rate']*100:.2f}%)")
            self._last_profit_log = datetime.now()

        # 목표 수익률 도달 확인
        if profit_rate >= self.buy_info["target_profit_rate"]:
            logger.info("=" * 60)
            logger.info(f"🎯 목표 수익률 {self.buy_info['target_profit_rate']*100:.2f}% 도달! 자동 매도를 시작합니다")
            logger.info(f"매수가: {buy_price:,}원")
            logger.info(f"현재가: {current_price:,}원")
            logger.info(f"수익률: {profit_rate*100:.2f}%")
            logger.info("=" * 60)

            # 매도가 계산 (현재가에서 한 틱 아래)
            sell_price = calculate_sell_price(current_price)

            logger.info(f"💰 매도 주문가: {sell_price:,}원 (현재가에서 한 틱 아래)")

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

    async def save_sell_result_ws(self, current_price: int, order_result: dict, profit_rate: float, actual_quantity: int = None, actual_buy_price: int = None):
        """매도 결과 저장 (WebSocket 기반)"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stock_name = self.buy_info["stock_name"].replace("/", "_")

        # 실제 매도 수량 (파라미터로 받거나 buy_info에서 가져옴)
        sell_quantity = actual_quantity if actual_quantity is not None else self.buy_info["quantity"]

        # 실제 평균 매입단가 (파라미터로 받거나 buy_info에서 가져옴)
        avg_buy_price = actual_buy_price if actual_buy_price is not None else self.buy_info["buy_price"]

        result = {
            "timestamp": timestamp,
            "action": "SELL",
            "buy_info": self.buy_info,
            "actual_avg_buy_price": avg_buy_price,  # 실제 평균 매입단가
            "sell_quantity": sell_quantity,  # 실제 매도 수량
            "current_price": current_price,
            "profit_rate": f"{profit_rate*100:.2f}%",
            "order_result": order_result,
            "source": "WebSocket 실시간 시세"
        }

        filename = self.result_dir / f"{timestamp}_{stock_name}_매도결과.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 매도 결과 저장: {filename}")

    async def save_stop_loss_result(self, current_price: int, order_result: dict, profit_rate: float, actual_quantity: int = None, actual_buy_price: int = None):
        """손절 결과 저장"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stock_name = self.buy_info["stock_name"].replace("/", "_")

        # 실제 매도 수량
        sell_quantity = actual_quantity if actual_quantity is not None else self.buy_info["quantity"]

        # 실제 평균 매입단가
        avg_buy_price = actual_buy_price if actual_buy_price is not None else self.buy_info["buy_price"]

        result = {
            "timestamp": timestamp,
            "action": "STOP_LOSS",
            "buy_info": self.buy_info,
            "actual_avg_buy_price": avg_buy_price,
            "sell_quantity": sell_quantity,
            "current_price": current_price,
            "profit_rate": f"{profit_rate*100:.2f}%",
            "stop_loss_rate": f"{self.stop_loss_rate*100:.2f}%",
            "order_result": order_result,
            "source": "WebSocket 실시간 시세 (손절)"
        }

        filename = self.result_dir / f"{timestamp}_{stock_name}_손절결과.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 손절 결과 저장: {filename}")

    async def save_force_sell_result(self, current_price: int, order_result: dict, profit_rate: float, actual_quantity: int = None, actual_buy_price: int = None):
        """강제 청산 결과 저장"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stock_name = self.buy_info["stock_name"].replace("/", "_")

        # 실제 매도 수량
        sell_quantity = actual_quantity if actual_quantity is not None else self.buy_info["quantity"]

        # 실제 평균 매입단가
        avg_buy_price = actual_buy_price if actual_buy_price is not None else self.buy_info["buy_price"]

        result = {
            "timestamp": timestamp,
            "action": "DAILY_FORCE_SELL",
            "buy_info": self.buy_info,
            "actual_avg_buy_price": avg_buy_price,
            "sell_quantity": sell_quantity,
            "current_price": current_price,
            "profit_rate": f"{profit_rate*100:.2f}%",
            "force_sell_time": self.daily_force_sell_time,
            "order_result": order_result,
            "source": "일일 강제 청산"
        }

        filename = self.result_dir / f"{timestamp}_{stock_name}_강제청산결과.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 강제 청산 결과 저장: {filename}")

    def is_buy_time_allowed(self) -> bool:
        """
        매수 가능 시간인지 확인 (환경변수 기준)

        Returns:
            True: 매수 가능 시간, False: 매수 불가 시간
        """
        from datetime import datetime as dt

        now = datetime.now()
        current_time_str = now.strftime("%H:%M")

        # 시간 문자열을 datetime 객체로 변환하여 정확한 비교
        try:
            current_time = dt.strptime(current_time_str, "%H:%M").time()
            start_time = dt.strptime(self.buy_start_time, "%H:%M").time()
            end_time = dt.strptime(self.buy_end_time, "%H:%M").time()

            # 시간 범위 확인
            if start_time <= current_time < end_time:
                return True
            return False
        except ValueError as e:
            logger.error(f"❌ 시간 형식 오류: {e}")
            return False

    def is_force_sell_time(self) -> bool:
        """
        강제 청산 시간인지 확인

        Returns:
            True: 강제 청산 시간 도달, False: 아직 도달 안함
        """
        from datetime import datetime as dt

        now = datetime.now()
        current_time_str = now.strftime("%H:%M")

        try:
            current_time = dt.strptime(current_time_str, "%H:%M").time()
            force_sell_time = dt.strptime(self.daily_force_sell_time, "%H:%M").time()

            # 강제 청산 시간 도달 확인 (이상)
            return current_time >= force_sell_time
        except ValueError as e:
            logger.error(f"❌ 강제 청산 시간 형식 오류: {e}")
            return False

    async def monitor_and_trade(self):
        """실시간 모니터링 및 자동 매매"""
        logger.info("🔍 종목 감시 시작...")
        logger.info(f"⏰ 매수 가능 시간: {self.buy_start_time} ~ {self.buy_end_time}")

        check_interval = 0.5  # 0.5초마다 체크 (빠른 감지)
        last_waiting_log_time = None  # 마지막 대기 로그 출력 시간
        last_time_check_log = None  # 마지막 시간 체크 로그 출력 시간

        while self.is_monitoring:
            try:
                # 매수 가능 시간 체크
                if not self.is_buy_time_allowed():
                    # 10초마다 한 번만 로그 출력
                    now = datetime.now()
                    if last_time_check_log is None or (now - last_time_check_log).seconds >= 10:
                        current_time = now.strftime("%H:%M:%S")
                        logger.info(f"⏸️  매수 가능 시간이 아닙니다. 현재 시각: {current_time} (매수 시간: {self.buy_start_time}~{self.buy_end_time})")
                        last_time_check_log = now

                    await asyncio.sleep(check_interval)
                    continue

                stock_data = await self.check_stock_data()

                if stock_data and stock_data.get("hasData"):
                    if not self.order_executed:
                        logger.info(f"🎯 종목 포착! {stock_data.get('종목명')}")

                        # 자동 매수 실행
                        order_result = await self.execute_auto_buy(stock_data)

                        if order_result and order_result.get("success"):
                            logger.info("✅ 자동 매수 완료!")
                            self.order_executed = True

                            # 매수 정보는 execute_auto_buy()에서 이미 설정됨
                            # buy_time도 이미 기록되어 있음

                            # 오늘 매수 기록 저장 (하루 1회 제한)
                            self.record_today_trading(
                                stock_code=self.buy_info["stock_code"],
                                stock_name=self.buy_info["stock_name"],
                                buy_price=self.buy_info["buy_price"],
                                quantity=self.buy_info["quantity"],
                                buy_time=self.buy_info.get("buy_time")  # 매수 시간 전달
                            )

                            # WebSocket 실시간 시세 모니터링 시작 (환경변수 확인)
                            if self.enable_sell_monitoring:
                                logger.info(f"📈 WebSocket 실시간 시세 모니터링 시작 (목표: {self.buy_info['target_profit_rate']*100:.2f}%)")
                                await self.start_websocket_monitoring()
                            else:
                                logger.info("⏸️  매도 모니터링이 비활성화되어 있습니다. 수동으로 매도해야 합니다.")
                                self.is_monitoring = False  # 모니터링 중지
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
            # 먼저 계좌 잔고 조회 (브라우저 시작 전)
            trading_info = self.load_today_trading_info()

            # 보유 종목이 있으면 매도 모니터링만 진행 (브라우저 없이)
            if trading_info:
                logger.info("✅ 보유 종목이 있습니다. 매도 모니터링만 시작합니다.")
                logger.info("📊 브라우저 없이 WebSocket 매도 모니터링을 진행합니다.")
                self.order_executed = True  # 매수 플래그 설정하여 추가 매수 방지

                # 매수 정보 복원
                self.buy_info["stock_code"] = trading_info.get("stock_code")
                self.buy_info["stock_name"] = trading_info.get("stock_name")
                self.buy_info["buy_price"] = trading_info.get("buy_price", 0)
                self.buy_info["quantity"] = trading_info.get("quantity", 0)
                self.buy_info["buy_time"] = trading_info.get("buy_time")  # 매수 시간 복원

                logger.info("=" * 60)
                logger.info(f"📥 매수 정보 복원 완료")
                logger.info(f"종목명: {self.buy_info['stock_name']}")
                logger.info(f"종목코드: {self.buy_info['stock_code']}")
                logger.info(f"매수가: {self.buy_info['buy_price']:,}원")
                logger.info(f"수량: {self.buy_info['quantity']}주")
                logger.info("=" * 60)

                # WebSocket 실시간 시세 모니터링 시작 (환경변수 확인)
                if self.enable_sell_monitoring:
                    logger.info(f"📈 WebSocket 매도 모니터링 시작 (목표: {self.buy_info['target_profit_rate']*100:.2f}%)")
                    await self.start_websocket_monitoring()

                    # WebSocket 모니터링이 계속 유지되도록 무한 대기
                    logger.info(f"⏱️  {self.buy_info['target_profit_rate']*100:.2f}% 수익률 도달 또는 Ctrl+C로 종료할 때까지 매도 모니터링합니다...")
                    logger.info("💡 매도 타이밍을 놓치지 않도록 계속 모니터링합니다.")
                    logger.info("📡 WebSocket 실시간 시세 수신 중 (DEBUG 모드에서 1초마다 출력)")
                    logger.info("⏰ 장 마감 시간 외에는 REST API로 1분마다 현재가를 조회합니다.")

                    # REST API 폴링 태스크 추가 (백업 - WebSocket 데이터가 없을 때)
                    polling_task = asyncio.create_task(self.price_polling_loop())

                    # WebSocket receive_loop()가 계속 실행되므로 무한 대기
                    # 매도 완료 시 ws_receive_task가 cancel되면서 종료됨
                    if self.ws_receive_task:
                        try:
                            await self.ws_receive_task
                        except asyncio.CancelledError:
                            logger.info("✅ WebSocket 모니터링이 정상 종료되었습니다.")
                            polling_task.cancel()
                else:
                    logger.info("⏸️  매도 모니터링이 비활성화되어 있습니다.")
                    logger.info("💡 수동으로 매도를 진행해야 합니다.")
                    logger.info(f"📊 보유 종목: {self.buy_info['stock_name']} ({self.buy_info['stock_code']})")
                    logger.info(f"📊 매수가: {self.buy_info['buy_price']:,}원 | 수량: {self.buy_info['quantity']}주")
                    return

            # 보유 종목이 없으면 매수 가능 시간 확인
            else:
                # 매수 가능 시간이 아니면 브라우저 시작하지 않고 로그만 출력
                if not self.is_buy_time_allowed():
                    now = datetime.now()
                    current_time = now.strftime("%H:%M:%S")
                    logger.info("=" * 60)
                    logger.info("⏸️  매수 가능 시간이 아닙니다.")
                    logger.info(f"현재 시각: {current_time}")
                    logger.info(f"매수 가능 시간: {self.buy_start_time} ~ {self.buy_end_time}")
                    logger.info("브라우저를 시작하지 않습니다.")
                    logger.info("=" * 60)
                    return

                # 매수 가능 시간이면 브라우저 시작
                await self.start_browser()
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
        """리소스 정리 (종료 전 미체결 확인)"""
        logger.info("=" * 80)
        logger.info("🔍 종료 전 미체결 주문 확인 중...")

        # 미체결 주문 확인
        outstanding_result = self.kiwoom_api.get_outstanding_orders()

        if outstanding_result.get("success"):
            outstanding_orders = outstanding_result.get("outstanding_orders", [])

            if outstanding_orders:
                logger.warning(f"⚠️ 미체결 주문이 {len(outstanding_orders)}건 존재합니다!")
                logger.warning("⚠️ 시스템을 종료하지 않고 계속 모니터링합니다.")
                logger.warning("💡 미체결 주문이 모두 체결되면 자동으로 종료됩니다.")
                logger.info("=" * 80)

                # 미체결이 있으면 종료하지 않고 대기
                # (WebSocket 모니터링은 계속 유지)
                return
            else:
                logger.info("✅ 미체결 주문이 없습니다. 안전하게 종료합니다.")
        else:
            logger.warning("⚠️ 미체결 주문 확인 실패. 강제 종료합니다.")

        logger.info("=" * 80)
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
