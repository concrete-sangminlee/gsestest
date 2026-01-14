#!/usr/bin/env python3
"""
GSES 공지사항을 가져와서 Slack으로 전송하는 스크립트
"""

import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import json
import hashlib
import re


def fetch_announcements():
    """
    gses.snu.ac.kr에서 공지사항을 가져옵니다.
    공지사항 페이지: https://gses.snu.ac.kr/news/notice/notice?sc=y
    """
    url = "https://gses.snu.ac.kr/news/notice/notice?sc=y"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        announcements = []
        
        # 공지사항 목록을 찾습니다
        # 페이지 구조: ul 태그 안에 li 태그로 각 공지사항이 구성됨
        # 각 li 안에 a 태그로 제목과 링크가 있음
        
        # 방법 1: bbsidx가 포함된 링크를 찾기 (가장 확실한 방법)
        notice_links = soup.find_all('a', href=lambda x: x and 'bbsidx' in x)
        
        for link in notice_links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            # 제목이 너무 짧거나 의미없는 경우 스킵
            if not title or len(title) < 3:
                continue
            
            # 상대 경로를 절대 경로로 변환
            if href:
                if not href.startswith('http'):
                    if href.startswith('/'):
                        href = f"https://gses.snu.ac.kr{href}"
                    else:
                        href = f"https://gses.snu.ac.kr/{href}"
                
                # 중복 제거를 위한 해시 생성 (제목 + URL의 bbsidx 값 사용)
                # bbsidx를 추출하여 더 정확한 중복 체크
                bbsidx_match = re.search(r'bbsidx=(\d+)', href)
                bbsidx = bbsidx_match.group(1) if bbsidx_match else None
                
                # 해시 생성: bbsidx가 있으면 사용, 없으면 제목+URL 조합 사용
                if bbsidx:
                    hash_value = hashlib.md5(f"bbsidx_{bbsidx}".encode()).hexdigest()
                else:
                    hash_value = hashlib.md5(f"{title}{href}".encode()).hexdigest()
                
                announcements.append({
                    'title': title,
                    'url': href,
                    'hash': hash_value,
                    'bbsidx': bbsidx
                })
        
        # bbsidx 기준으로 정렬 (최신순, 숫자가 큰 것이 최신)
        if announcements:
            announcements.sort(key=lambda x: int(x['bbsidx']) if x['bbsidx'] else 0, reverse=True)
            # 최신 20개만 반환 (너무 많으면 중복 체크 파일이 커질 수 있음)
            announcements = announcements[:20]
        
        return announcements
        
    except requests.RequestException as e:
        print(f"공지사항을 가져오는 중 오류 발생: {e}")
        return []
    except Exception as e:
        print(f"공지사항 파싱 중 오류 발생: {e}")
        return []


def load_processed_announcements():
    """이미 처리한 공지사항의 해시를 로드합니다."""
    file_path = 'processed_announcements.json'
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except:
            return set()
    return set()


def save_processed_announcements(hashes):
    """처리한 공지사항의 해시를 저장합니다."""
    file_path = 'processed_announcements.json'
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(list(hashes), f, ensure_ascii=False, indent=2)


def send_to_slack(announcement, webhook_url):
    """
    Slack 웹훅을 통해 메시지를 전송합니다.
    """
    message = {
        "text": "새로운 GSES 공지사항이 있습니다!",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📢 새로운 공지사항"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{announcement['title']}*\n\n<{announcement['url']}|공지사항 보기>"
                }
            },
            {
                "type": "divider"
            }
        ]
    }
    
    try:
        response = requests.post(webhook_url, json=message, timeout=10)
        response.raise_for_status()
        print(f"Slack 메시지 전송 성공: {announcement['title']}")
        return True
    except requests.RequestException as e:
        print(f"Slack 메시지 전송 실패: {e}")
        return False


def main():
    """메인 함수"""
    webhook_url = os.getenv('SLACK_WEBHOOK_URL')
    
    if not webhook_url:
        print("오류: SLACK_WEBHOOK_URL 환경 변수가 설정되지 않았습니다.")
        return
    
    # 공지사항 가져오기
    announcements = fetch_announcements()
    
    if not announcements:
        print("공지사항을 가져올 수 없습니다.")
        return
    
    # 이미 처리한 공지사항 로드
    processed = load_processed_announcements()
    
    # 새로운 공지사항만 필터링
    new_announcements = [
        ann for ann in announcements 
        if ann['hash'] not in processed
    ]
    
    if not new_announcements:
        print("새로운 공지사항이 없습니다.")
        return
    
    # 새로운 공지사항을 Slack으로 전송
    for announcement in new_announcements:
        if send_to_slack(announcement, webhook_url):
            processed.add(announcement['hash'])
    
    # 처리한 공지사항 저장
    save_processed_announcements(processed)
    print(f"처리 완료: {len(new_announcements)}개의 새로운 공지사항을 전송했습니다.")


if __name__ == "__main__":
    main()
