#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BOT TWEET OTOMATIS TANPA API (POST DARI HALAMAN HOME)
=====================================================
+ Tambahan fitur:
  - Menyimpan antrean tweet ke `queue.json` supaya bisa lanjut walau script mati.
  - Saat start, tanya mau reset progress atau tidak.
"""

import os
import re
import time
import json
import random
import hashlib
import logging
import pathlib
from typing import List, Dict, Tuple, Set

import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, InvalidSessionIdException, NoSuchElementException

#########################
# KONFIGURASI UTAMA
#########################
MAX_PAGES_PER_SITE = 10
DELAY_TWEET_RANGE = (10, 30)
TWEETS_BEFORE_HOME = 3

OUTPUT_DIR = pathlib.Path("./data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
POSTS_JSON = OUTPUT_DIR / "posts.json"
POSTED_TXT = OUTPUT_DIR / "posted.txt"
QUEUE_JSON = OUTPUT_DIR / "queue.json"
CHROME_PROFILE_DIR = pathlib.Path("./chrome_profile")
CHROME_PROFILE_DIR.mkdir(exist_ok=True)
HOME_TWEET_FILE = pathlib.Path("./home_tweet.txt")

CATEGORIES: List[str] = [
    "https://tatarapilaundry.com/category/blog/",
    "https://villapermatagroup.com/category/artikel",
    "https://batikputrabengawan.co.id/category/batik/",
    "https://www.altembaga.com/category/artikel/",
    "https://mganik-nutrition.com/category/artikel/",
    "https://juallonceng.com/category/artikel/",
    "https://tehnuri.com/category/artikel/",
    "https://mozapro.id/category/artikel/",
    "https://kopimaxpresso.com/category/artikel",
    "https://haidartours.com/category/artikel",
    "https://laroiba.com/category/artikel",
    "https://maklonbygujati.id/category/artikel",
    "https://houseofasiyah.com/category/artikel/",
    "https://gagahjayaabadi.com/blog/",
    "https://batikestujaya.com/category/artikel/",
    "https://khattabatik.com/category/artikel",
    "https://deonkraft.com/category/artikel/",
    "https://alphenwear.com/category/artikel/",
    "https://cvmac.id/category/blog/", # <-- DIKEMBALIKAN KE SINI
]

LOAD_MORE_DOMAINS: Set[str] = {
    "villapermatagroup.com",
    "houseofasiyah.com",
    "haidartours.com",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

#########################
# UTILITAS
#########################
def domain_of(url: str) -> str:
    try:
        return url.split("//", 1)[1].split("/", 1)[0].lower()
    except Exception:
        return url

def rand_delay(a: int, b: int):
    t = random.uniform(a, b)
    logging.info(f"Delay {t:.2f} detik...")
    time.sleep(t)

def load_posted() -> Set[str]:
    if POSTED_TXT.exists():
        with open(POSTED_TXT, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_posted(items: List[str]):
    with open(POSTED_TXT, "a", encoding="utf-8") as f:
        for it in items:
            f.write(it + "\n")

def text_hash(txt: str) -> str:
    return hashlib.sha1(txt.encode("utf-8")).hexdigest()

# Perbaikan pada fungsi safe_get
def safe_get(url: str, timeout: int = 20) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
        "Accept-Language": "id,en;q=0.9",
    }
    # Tambahkan verify=False untuk mengabaikan kesalahan SSL
    return requests.get(url, headers=headers, timeout=timeout)

#########################
# SCRAPER
#########################
WP_TITLE_SELECTORS = [
    ("h2.entry-title a", "text", "href"),
    ("h3.entry-title a", "text", "href"),
    ("article h2 a", "text", "href"),
    (".post-title a", "text", "href"),
    (".blog-post h2 a", "text", "href"),
    (".entry-title a", "text", "href"),
]

def parse_posts(html: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    seen = set()
    results: List[Tuple[str, str]] = []
    for sel, _, _ in WP_TITLE_SELECTORS:
        for a in soup.select(sel):
            title = a.get_text(strip=True)
            href = a.get("href")
            if not title or not href:
                continue
            key = (title, href)
            if key in seen:
                continue
            seen.add(key)
            results.append((title, href))
    if not results:
        for art in soup.find_all("article"):
            a = art.find("a", href=True)
            h = None
            for hx in ["h1", "h2", "h3"]:
                htag = art.find(hx)
                if htag and htag.get_text(strip=True):
                    h = htag.get_text(strip=True)
                    break
            if a and h:
                results.append((h, a["href"]))
    return results

def paginate_urls(base: str, max_pages: int) -> List[str]:
    urls = [base.rstrip("/") + "/"]
    for n in range(2, max_pages + 1):
        urls.append(base.rstrip("/") + f"/page/{n}/")
    for n in range(2, max_pages + 1):
        sep = "&" if "?" in base else "?"
        urls.append(base.rstrip("/") + f"{sep}paged={n}")
    return urls

def scrape_pagination(category_url: str, max_pages: int = MAX_PAGES_PER_SITE) -> List[Tuple[str, str]]:
    logging.info(f"Scrape PAGINATION: {category_url}")
    results: List[Tuple[str, str]] = []
    for url in paginate_urls(category_url, max_pages):
        try:
            resp = safe_get(url)
            if resp.status_code != 200:
                continue
            posts = parse_posts(resp.text)
            results.extend(posts)
        except Exception as e:
            logging.exception(f"Gagal akses {url}: {e}")
    return dedupe_posts(results)

def dedupe_posts(items: List[Tuple[str, str]]):
    seen = set()
    out = []
    for title, href in items:
        key = href.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append((title.strip(), href.strip()))
    return out

def scrape_pagination_with_selenium(driver: webdriver.Chrome, category_url: str, max_pages: int = MAX_PAGES_PER_SITE) -> List[Tuple[str, str]]:
    logging.info(f"Scrape PAGINATION with Selenium: {category_url}")
    results: List[Tuple[str, str]] = []
    
    urls_to_scrape = paginate_urls(category_url, max_pages)

    for url in urls_to_scrape:
        try:
            logging.info(f"Mengunjungi halaman: {url}")
            driver.get(url)
            rand_delay(1, 2)
            
            if "page not found" in driver.page_source.lower():
                logging.info(f"Halaman {url} tidak ditemukan, berhenti.")
                break
            
            html = driver.page_source
            posts = parse_posts(html)
            results.extend(posts)
        
        except Exception as e:
            logging.error(f"Gagal akses {url} dengan Selenium: {e}")
            continue

    return dedupe_posts(results)

#########################
# LOAD MORE SELENIUM
#########################
LOAD_MORE_BUTTON_SELECTORS = [
    (By.CSS_SELECTOR, "button.load-more"),
    (By.CSS_SELECTOR, "a.load-more"),
    (By.XPATH, "//button[contains(translate(., 'LOADMOR', 'loadmor'), 'load more') or contains(., 'Muat Lagi')]"),
    (By.XPATH, "//a[contains(translate(., 'LOADMOR', 'loadmor'), 'load more') or contains(., 'Muat Lagi')]"),
    (By.CSS_SELECTOR, "#load-more, .more-posts, .infinite-scroll .next")
]

def build_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR.resolve()}")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def scrape_load_more(driver: webdriver.Chrome, category_url: str, max_clicks: int = MAX_PAGES_PER_SITE) -> List[Tuple[str, str]]:
    logging.info(f"Scrape LOAD MORE: {category_url}")
    driver.get(category_url)
    all_posts: List[Tuple[str, str]] = []

    def grab() -> List[Tuple[str, str]]:
        html = driver.page_source
        return parse_posts(html)

    all_posts.extend(grab())

    for _ in range(max_clicks - 1):
        btn = None
        for by, sel in LOAD_MORE_BUTTON_SELECTORS:
            try:
                btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
                if btn:
                    break
            except Exception:
                continue
        if not btn:
            break
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        rand_delay(1, 2)
        try:
            btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", btn)
        rand_delay(1, 3)
        all_posts.extend(grab())

    return dedupe_posts(all_posts)

#########################
# TWEETER HOME
#########################
HOME_TEXTBOX_SELECTORS = [
    (By.CSS_SELECTOR, "div[role='textbox'][data-testid='tweetTextarea_0']"),
    (By.CSS_SELECTOR, "div[data-testid='tweetTextarea_0']"),
]

HOME_TWEET_BUTTON_SELECTORS = [
    (By.CSS_SELECTOR, "div[data-testid='tweetButtonInline']"),
    (By.CSS_SELECTOR, "button[data-testid='tweetButtonInline']"),
]

def wait_home_ready(driver: webdriver.Chrome, timeout: int = 60):
    logging.info("Menunggu halaman beranda X/Twitter dimuat...")
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='primaryColumn']"))
        )
    except TimeoutException:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(text(), 'Masuk ke X')]"))
            )
            logging.error("Terdeteksi di halaman login. Silakan login secara manual.")
            input("Setelah login, tekan ENTER di sini untuk melanjutkan...")
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='primaryColumn']"))
            )
        except TimeoutException:
            logging.error("Gagal memuat halaman beranda atau terdeteksi di halaman login. Mungkin struktur halaman X/Twitter sudah berubah.")
            raise

def find_home_textbox(driver: webdriver.Chrome):
    for by, sel in HOME_TEXTBOX_SELECTORS:
        try:
            box = WebDriverWait(driver, 5).until(EC.presence_of_element_located((by, sel)))

            # scroll ke tengah layar
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", box)
            time.sleep(0.5)

            # cek apakah terlihat di viewport
            is_visible = driver.execute_script("""
                const box = arguments[0];
                const rect = box.getBoundingClientRect();
                return (
                    rect.top >= 0 &&
                    rect.left >= 0 &&
                    rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                    rect.right <= (window.innerWidth || document.documentElement.clientWidth)
                );
            """, box)

            if not is_visible:
                driver.execute_script("window.scrollBy(0, -300);")
                time.sleep(0.5)

            try:
                box.click()
            except Exception:
                # fallback kalau ketutup
                driver.execute_script("arguments[0].focus(); arguments[0].click();", box)

            return box
        except Exception:
            continue

    # fallback terakhir cari textbox umum
    box = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", box)
    driver.execute_script("arguments[0].focus(); arguments[0].click();", box)
    return box


def find_home_tweet_button(driver: webdriver.Chrome):
    for by, sel in HOME_TWEET_BUTTON_SELECTORS:
        try:
            return WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
        except Exception:
            continue
    return WebDriverWait(driver, 5).until(
        EC.element_to_be_clickable((By.XPATH, "//div[@role='button' and contains(@data-testid,'tweetButton')]"))
    )

def send_tweet_on_home(driver: webdriver.Chrome, text: str) -> bool:
    try:
        box = find_home_textbox(driver)
        box.send_keys(Keys.CONTROL, 'a', Keys.DELETE)
        box.send_keys(text)
    except Exception as e:
        logging.error(f"Gagal ketik: {e}")
        return False
    try:
        btn = find_home_tweet_button(driver)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        btn.click()
    except Exception as e:
        logging.error(f"Gagal klik: {e}")
        return False
    time.sleep(random.uniform(1.0, 2.0))
    logging.info("Tweet dikirim dari Home.")
    return True

def scroll_natural(driver):
    """
    Scroll naik setelah tweet supaya textbox tetap kelihatan.
    """
    try:
        box = driver.find_element(By.CSS_SELECTOR, "div[role='textbox']")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", box)
        time.sleep(0.5)

        # kalau masih ketutupan, scroll naik lagi
        for _ in range(3):
            is_visible = driver.execute_script("""
                const box = arguments[0];
                const rect = box.getBoundingClientRect();
                return (
                    rect.top >= 0 &&
                    rect.bottom <= (window.innerHeight || document.documentElement.clientHeight)
                );
            """, box)
            if is_visible:
                break
            driver.execute_script("window.scrollBy(0, -200);")
            time.sleep(0.5)
    except Exception as e:
        logging.warning(f"Gagal scroll_natural: {e}")



#########################
# PROGRESS QUEUE
#########################
def prompt_reset():
    ans = input("Reset progress? (y/n): ").strip().lower()
    if ans == "y":
        if POSTED_TXT.exists(): POSTED_TXT.unlink()
        if QUEUE_JSON.exists(): QUEUE_JSON.unlink()
        print("Progress direset.\n")
    else:
        print("Lanjut dari progress sebelumnya.\n")

def prompt_scrape_again() -> bool:
    ans = input("Scrape ulang situs web? (y/n): ").strip().lower()
    return ans == "y"

def save_queue(queue: List[str]):
    with open(QUEUE_JSON, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

def load_queue() -> List[str]:
    if QUEUE_JSON.exists():
        with open(QUEUE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

#########################
# PIPELINE
#########################
def load_home_tweets() -> List[str]:
    if not HOME_TWEET_FILE.exists():
        return []
    with open(HOME_TWEET_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def build_queue(posts: List[Tuple[str, str]], home_tweets: List[str], already: Set[str]) -> List[str]:
    queue: List[str] = []
    seen_home_tweets: Set[str] = set()
    home_i = 0
    home_len = len(home_tweets)
    random.shuffle(posts)
    count_since_home = 0
    for title, url in posts:
        tweet_text = f"{title} {url}"
        th = text_hash(tweet_text)
        if url in already or th in already:
            continue
        queue.append(tweet_text)
        count_since_home += 1
        if home_len > 0 and count_since_home >= TWEETS_BEFORE_HOME:
            ht = home_tweets[home_i % home_len]
            hh = text_hash(ht)
            if ht not in already and hh not in already and ht not in seen_home_tweets:
                queue.append(ht)
                seen_home_tweets.add(ht)
            
            home_i += 1
            count_since_home = 0
    return queue

def gather_all_posts() -> List[Tuple[str, str]]:
    posts: List[Tuple[str, str]] = []
    
    non_selenium_cats = [c for c in CATEGORIES if domain_of(c) not in LOAD_MORE_DOMAINS]
    
    for cat in non_selenium_cats:
        posts.extend(scrape_pagination(cat, MAX_PAGES_PER_SITE))
        
    selenium_cats = [c for c in CATEGORIES if domain_of(c) in LOAD_MORE_DOMAINS]
    
    if selenium_cats:
        drv = build_driver()
        try:
            for cat in selenium_cats:
                dom = domain_of(cat)
                if dom in ["villapermatagroup.com", "houseofasiyah.com", "haidartours.com"]:
                    posts.extend(scrape_load_more(drv, cat, MAX_PAGES_PER_SITE))
                elif dom == "tatarapilaundry.com":
                    # KARENA SEKARANG DIANGGAP NON-SELENIUM, BLOK INI TIDAK AKAN PERNAH DIJALANKAN
                    posts.extend(scrape_pagination_with_selenium(drv, cat, MAX_PAGES_PER_SITE))
                else:
                    logging.warning(f"Domain {dom} tidak memiliki metode scraping Selenium yang spesifik. Melewati.")
        finally:
            drv.quit()
    
    return dedupe_posts(posts)

#########################
# MAIN
#########################
def main():
    prompt_reset()
    already = load_posted()
    queue = load_queue()

    if not queue:
        posts = []
        scrape_again = prompt_scrape_again()
        
        if scrape_again or not POSTS_JSON.exists():
            logging.info("Memulai proses scraping...")
            posts = gather_all_posts()
            with open(POSTS_JSON, "w", encoding="utf-8") as f:
                json.dump([{"title": t, "url": u} for t, u in posts], f, ensure_ascii=False, indent=2)
        else:
            logging.info("Memuat data postingan dari posts.json...")
            try:
                posts_data = json.load(open(POSTS_JSON, "r", encoding="utf-8"))
                posts = [(x["title"], x["url"]) for x in posts_data]
            except (IOError, json.JSONDecodeError):
                logging.warning("Gagal memuat posts.json, melakukan scraping sebagai cadangan.")
                posts = gather_all_posts()
                with open(POSTS_JSON, "w", encoding="utf-8") as f:
                    json.dump([{"title": t, "url": u} for t, u in posts], f, ensure_ascii=False, indent=2)

        home_tweets = load_home_tweets()
        queue = build_queue(posts, home_tweets, already)
        random.shuffle(queue)
        save_queue(queue)

    if not queue:
        logging.info("Tidak ada tweet baru.")
        return

    driver = build_driver()
    print("\n>>> Login X/Twitter jika belum login, lalu biarkan script jalan.\n")
    driver.get("https://x.com/home")
    wait_home_ready(driver, timeout=60)

    posted_now: List[str] = []
    total = len(queue)  # total tweet di antrean
    processed = 0       # counter tweet

    while queue:
        text = queue.pop(0)
        processed += 1
        logging.info(f"[{processed}/{total}] Posting: {text[:80]}...")

        if send_tweet_on_home(driver, text):
            # scroll natural biar page nggak kebablasan turun
            scroll_natural(driver)

            urls = re.findall(r'https?://[^\s]+', text)
            if urls:
                posted_now.extend(urls)
            posted_now.append(text_hash(text))

            save_posted(posted_now)
            posted_now.clear()
            save_queue(queue)
            rand_delay(*DELAY_TWEET_RANGE)

    driver.quit()
    logging.info("Tweet Selesai !!! 🎉")

if __name__ == "__main__":
    main()
