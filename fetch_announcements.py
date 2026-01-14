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


def fetch_announcements():
    """
    gses.snu.ac.kr에서 공지사항을 가져옵니다.
    실제 페이지 구조에 맞게 수정이 필요할 수 있습니다.
    """
    url = "https://gses.snu.ac.kr/board/notice"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 공지사항 목록을 찾습니다 (실제 페이지 구조에 맞게 수정 필요)
        # 일반적으로 table, ul, div 등의 구조를 가집니다
        announcements = []
        
        # 예시: table 구조인 경우
        # table = soup.find('table', class_='board-list')  # 실제 클래스명으로 변경
        # rows = table.find_all('tr')[1:]  # 헤더 제외
        
        # 예시: div 구조인 경우
        # items = soup.find_all('div', class_='notice-item')  # 실제 클래스명으로 변경
        
        # 임시로 모든 링크가 있는 항목을 찾는 예시 코드
        # 실제 페이지 구조를 확인 후 수정이 필요합니다
        notice_links = soup.find_all('a', href=True)
        
        for link in notice_links[:10]:  # 최신 10개만 가져오기
            title = link.get_text(strip=True)
            href = link.get('href')
            
            # 상대 경로를 절대 경로로 변환
            if href and not href.startswith('http'):
                if href.startswith('/'):
                    href = f"https://gses.snu.ac.kr{href}"
                else:
                    href = f"https://gses.snu.ac.kr/{href}"
            
            if title and len(title) > 5:  # 의미있는 제목만 필터링
                announcements.append({
                    'title': title,
                    'url': href,
                    'hash': hashlib.md5(f"{title}{href}".encode()).hexdigest()
                })
        
        return announcements
        
    except requests.RequestException as e:
        print(f"공지사항을 가져오는 중 오류 발생: {e}")
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
