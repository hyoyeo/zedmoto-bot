import json
import os
import time
import random
import logging
from datetime import datetime, time as dt_time

# Selenium 관련
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# Telegram 관련 (v21+ 호환)
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

# ---------------- 설정 ----------------
TELEGRAM_TOKEN = "7900531497:AAGHUYjnIAG7ib5cKgf0uKoCE10EFrwNVAI"
ALLOWED_CHAT_ID = 1715917739
DATA_FILE = "bikes.json"

# 한글 브랜드 → 영어 브랜드 매핑 (라이트바겐 사이트 기준)
BRAND_MAP = {
    "혼다": "Honda",
    "야마하": "Yamaha",
    "스즈키": "Suzuki",
    "가와사키": "Kawasaki",
    "가와사키": "Kawasaki",  # 오타 방지
    "bmw": "BMW",
    "BMW": "BMW",
    "두카티": "Ducati",
    "ducati": "Ducati",
    # 필요하면 더 추가하세요 (대소문자 구분 없이 동작)
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def load_bikes():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_bikes(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def scrape_bike_data(brand_kr, model, min_year, max_year):
    driver = get_driver()
    try:
        # 한글 브랜드 → 영어 변환
        brand = BRAND_MAP.get(brand_kr.lower(), brand_kr)  # 매핑 없으면 그대로 사용 (영어 입력 시)

        # 정확한 필터 URL (brands[0]=브랜드 & models[0]=모델)
        url = (
            f"https://www.reitwagen.co.kr/products/home/used?"
            f"brands%5B0%5D={brand}&"
            f"models%5B0%5D={model.replace(' ', '%20')}"
        )

        driver.get(url)
        time.sleep(random.uniform(5, 8))  # 페이지 로딩 + Cloudflare 대기

        # 가격 요소 찾기 - 사이트 구조에 따라 셀렉터 조정 필요
        # F12 눌러서 가격 부분 클래스 확인 후 수정하세요 (현재 임시)
        price_elements = driver.find_elements(
            By.CSS_SELECTOR,
            'div[class*="price"], span[class*="price"], strong.price, .price-amount, [class*="won"], .cost, .amount'
        )

        prices = []
        for elem in price_elements:
            text = elem.text.strip()
            if any(keyword in text for keyword in ['만원', '₩', '원']):
                cleaned = text.replace('만원', '').replace(',', '').replace(' ', '').replace('~', '').replace('₩', '').replace('원', '')
                try:
                    p = int(cleaned)
                    if 100 <= p <= 10000:  # 현실적 범위
                        prices.append(p)
                except ValueError:
                    pass

        count = len(prices)
        if count == 0:
            logging.info(f"{brand} {model} 매물 0개")
            return None, None, None, 0

        avg = round(sum(prices) / count)
        min_p = min(prices)
        max_p = max(prices)
        return avg, min_p, max_p, count

    except Exception as e:
        logging.error(f"스크래핑 실패 ({brand_kr} {model}): {e}")
        return None, None, None, 0
    finally:
        driver.quit()

async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    bikes = load_bikes()
    if not bikes:
        return

    message = f"[{datetime.now().strftime('%Y-%m-%d %H:%M KST')}] 오늘 라이트바겐 중고 바이크 시세\n\n"

    for key, info in bikes.items():
        brand_kr = info.get('brand_kr', info.get('brand', ''))  # 한글 브랜드 저장해두기
        model = info.get('model', '')
        years = info.get('years', [])
        if not years:
            continue
        min_y, max_y = min(years), max(years)

        avg, min_p, max_p, count = scrape_bike_data(brand_kr, model, min_y, max_y)

        if avg is None:
            message += f"{brand_kr} {model} ({min_y}~{max_y}): 매물 없거나 오류 발생\n\n"
        else:
            message += f"📌 {brand_kr} {model} ({min_y}~{max_y})\n"
            message += f"   • 평균: {avg:,}만원\n"
            message += f"   • 최저: {min_p:,}만원\n"
            message += f"   • 최고: {max_p:,}만원\n"
            message += f"   • 매물: {count}대\n\n"

    if len(message) > 100:
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=message)

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("사용법: /add 브랜드 모델 년식시작-년식끝\n예: /add 야마하 NMAX125 2018-2025")
        return
    try:
        brand_kr = args[0]  # 한글 브랜드 그대로 저장 (표시용)
        model = args[1]
        year_str = args[2]
        miny, maxy = map(int, year_str.split('-'))
        if miny > maxy:
            await update.message.reply_text("년식 범위 오류 (시작 > 끝)")
            return
        years = list(range(miny, maxy + 1))

        bikes = load_bikes()
        # 키는 영어 브랜드로 생성 (중복 방지 + 크롤링용)
        brand_eng = BRAND_MAP.get(brand_kr.lower(), brand_kr)
        key = f"{brand_eng.lower()}_{model.lower().replace(' ', '_')}"
        bikes[key] = {
            "brand_kr": brand_kr,  # 표시용 한글 브랜드
            "brand": brand_eng,    # 크롤링용 영어 브랜드
            "model": model,
            "years": years
        }
        save_bikes(bikes)
        await update.message.reply_text(f"추가 완료: {brand_kr} {model} ({miny}-{maxy})")
    except Exception as e:
        await update.message.reply_text(f"형식 오류! 예시처럼 입력해주세요 ({str(e)})")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("/remove 키\n예: /remove yamaha_nmax125\n/list로 키 확인하세요")
        return
    key = context.args[0]
    bikes = load_bikes()
    if key in bikes:
        brand_kr = bikes[key].get('brand_kr', key)
        model = bikes[key].get('model', '')
        del bikes[key]
        save_bikes(bikes)
        await update.message.reply_text(f"{brand_kr} {model} 삭제 완료")
    else:
        await update.message.reply_text("그런 기종 없음. /list로 키 확인하세요")

async def list_bikes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    bikes = load_bikes()
    if not bikes:
        await update.message.reply_text("등록된 기종 없음")
        return
    msg = "현재 등록 목록:\n"
    for key, v in bikes.items():
        brand_kr = v.get('brand_kr', v.get('brand', ''))
        model = v.get('model', '')
        ys = v.get('years', [])
        msg += f"- {key}: {brand_kr} {model} ({min(ys)}~{max(ys)})\n"
    await update.message.reply_text(msg)

def main():
    print("봇 시작 중...")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("add", add))
    application.add_handler(CommandHandler("remove", remove))
    application.add_handler(CommandHandler("list", list_bikes))

    job_queue = application.job_queue
    job_queue.run_daily(
        send_daily_report,
        time=dt_time(0, 0),
        days=tuple(range(7))
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=0.5,
        timeout=10,
        bootstrap_retries=0
    )

if __name__ == '__main__':
    main()
