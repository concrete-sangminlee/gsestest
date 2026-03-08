#!/usr/bin/env python3
"""
GSES 공지사항을 가져와서 Slack으로 전송하는 스크립트
"""

import os
import sys
import argparse
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import json
import re

# .env 파일 지원
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv가 없어도 환경변수로 동작 가능


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
        
        # bbsidx가 포함된 링크를 찾기
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
                
                # bbsidx를 추출하여 중복 체크에 사용
                bbsidx_match = re.search(r'bbsidx=(\d+)', href)
                bbsidx = bbsidx_match.group(1) if bbsidx_match else None
                
                announcements.append({
                    'title': title,
                    'url': href,
                    'bbsidx': bbsidx
                })
        
        # bbsidx 기준으로 정렬 (최신순, 숫자가 큰 것이 최신)
        if announcements:
            announcements.sort(key=lambda x: int(x['bbsidx']) if x['bbsidx'] else 0, reverse=True)
            # 최신 20개만 반환 (너무 많으면 중복 체크 파일이 커질 수 있음)
            announcements = announcements[:20]
        
        return announcements
        
    except requests.RequestException as e:
        print(f"❌ 공지사항을 가져오는 중 오류 발생: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"❌ 공지사항 파싱 중 오류 발생: {e}", file=sys.stderr)
        return []


def load_state():
    """state.json에서 이미 처리한 공지사항의 bbsidx 목록을 로드합니다."""
    file_path = 'state.json'
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # bbsidx를 문자열로 통일하여 비교 문제 방지
                processed_list = data.get('processed_bbsidx', [])
                processed_set = set(str(bbsidx) for bbsidx in processed_list)
                return {
                    'processed_bbsidx': processed_set,
                    'last_updated': data.get('last_updated'),
                    'initialized': data.get('initialized', False)
                }
        except Exception as e:
            print(f"⚠️  state.json 로드 중 오류: {e}", file=sys.stderr)
            return {'processed_bbsidx': set(), 'last_updated': None, 'initialized': False}
    return {'processed_bbsidx': set(), 'last_updated': None, 'initialized': False}


def save_state(processed_bbsidx, initialized=True):
    """state.json에 처리한 공지사항의 bbsidx 목록을 저장합니다."""
    file_path = 'state.json'
    # bbsidx를 문자열 리스트로 저장 (정렬하여 일관성 유지)
    processed_list = sorted([str(bbsidx) for bbsidx in processed_bbsidx], reverse=True)
    data = {
        'processed_bbsidx': processed_list,
        'last_updated': datetime.now().isoformat(),
        'initialized': initialized
    }
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ state.json 저장 중 오류: {e}", file=sys.stderr)


def send_to_slack(announcements, webhook_url):
    """
    Slack 웹훅을 통해 여러 공지사항을 한 번에 전송합니다.
    """
    if not announcements:
        return False

    count = len(announcements)
    now_kst = datetime.now().strftime("%Y. %m. %d  %H:%M")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📢  GSES 새 공지사항",
                "emoji": True
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"서울대학교 공학전문대학원  ｜  🔔 *{count}건*의 새로운 공지사항"
                }
            ]
        },
        {"type": "divider"}
    ]

    for i, ann in enumerate(announcements):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"> *{ann['title']}*"
            },
            "accessory": {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "확인하기",
                    "emoji": True
                },
                "url": ann['url'],
                "style": "primary"
            }
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📋  전체 공지사항 보기",
                    "emoji": True
                },
                "url": "https://gses.snu.ac.kr/news/notice/notice"
            }
        ]
    })
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"🏫 GSES Notice Bot  ｜  {now_kst} KST"
            }
        ]
    })

    message = {
        "text": f"새로운 GSES 공지사항 {count}건",
        "attachments": [
            {
                "color": "#003876",
                "blocks": blocks
            }
        ]
    }

    try:
        response = requests.post(webhook_url, json=message, timeout=10)
        response.raise_for_status()
        print(f"✅ Slack 메시지 전송 성공: {count}개의 공지사항")
        return True
    except requests.RequestException as e:
        print(f"❌ Slack 메시지 전송 실패: {e}", file=sys.stderr)
        return False


def send_ping_test(webhook_url):
    """Slack 연결 테스트용 ping 메시지 전송"""
    now_kst = datetime.now().strftime("%Y. %m. %d  %H:%M")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🧪  연결 테스트",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "GSES Slack Bot이 정상적으로 연결되어 있습니다.\n새로운 공지사항이 등록되면 이 채널로 알림을 보내드립니다."
            }
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"✅ *Status: Connected*  ｜  🏫 GSES Notice Bot  ｜  {now_kst} KST"
                }
            ]
        }
    ]

    message = {
        "text": "GSES Slack Bot 연결 테스트",
        "attachments": [
            {
                "color": "#2eb67d",
                "blocks": blocks
            }
        ]
    }

    try:
        response = requests.post(webhook_url, json=message, timeout=10)
        response.raise_for_status()
        print("✅ Slack ping 테스트 성공")
        return True
    except requests.RequestException as e:
        print(f"❌ Slack ping 테스트 실패: {e}", file=sys.stderr)
        return False


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description='GSES 공지사항을 확인하고 Slack으로 알림을 보냅니다.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 최초 실행: 기준점만 저장 (스팸 방지)
  python fetch_announcements.py --init
  
  # Dry-run: 전송 없이 확인만
  python fetch_announcements.py --dry-run
  
  # 정상 실행: 새 공지사항 있으면 Slack 전송
  python fetch_announcements.py
  
  # 테스트: 최신 1개만 전송
  TEST_MODE=true python fetch_announcements.py
  
  # Slack 연결 테스트
  python fetch_announcements.py --ping
        """
    )
    
    parser.add_argument('--init', action='store_true',
                       help='최초 실행 시 기준점만 저장하고 알림은 보내지 않음 (스팸 방지)')
    parser.add_argument('--dry-run', action='store_true',
                       help='전송 없이 새 공지사항만 확인')
    parser.add_argument('--ping', action='store_true',
                       help='Slack 연결 테스트 메시지 전송')
    parser.add_argument('--send-on-first-run', action='store_true',
                       help='최초 실행 시에도 최신 공지사항 전송 (기본값: False, 스팸 방지)')
    
    args = parser.parse_args()
    
    # 환경 변수에서 설정 읽기
    webhook_url = os.getenv('SLACK_WEBHOOK_URL')
    test_mode = os.getenv('TEST_MODE', 'false').lower() == 'true'
    send_on_first_run = args.send_on_first_run or os.getenv('SEND_ON_FIRST_RUN', 'false').lower() == 'true'
    
    # Slack 웹훅 URL 확인
    if not webhook_url:
        print("❌ 오류: SLACK_WEBHOOK_URL 환경 변수가 설정되지 않았습니다.", file=sys.stderr)
        print("   .env 파일을 만들거나 환경 변수를 설정해주세요.", file=sys.stderr)
        sys.exit(1)
    
    # Ping 테스트
    if args.ping:
        send_ping_test(webhook_url)
        return
    
    # 공지사항 가져오기
    print("📡 공지사항을 가져오는 중...")
    announcements = fetch_announcements()
    
    if not announcements:
        print("⚠️  공지사항을 가져올 수 없습니다.")
        sys.exit(1)
    
    print(f"📋 총 {len(announcements)}개의 공지사항을 찾았습니다.")
    
    # state 로드
    state = load_state()
    processed_bbsidx = state['processed_bbsidx']
    is_initialized = state['initialized']
    
    print(f"📊 State 상태:")
    print(f"   - 초기화 여부: {is_initialized}")
    print(f"   - 처리한 공지사항 수: {len(processed_bbsidx)}")
    if state['last_updated']:
        print(f"   - 마지막 업데이트: {state['last_updated']}")
    
    # --init 옵션: 기준점만 저장
    if args.init:
        if announcements:
            latest_bbsidx = announcements[0]['bbsidx']
            if latest_bbsidx:
                processed_bbsidx.add(latest_bbsidx)
                save_state(processed_bbsidx, initialized=True)
                print(f"✅ 기준점 저장 완료: bbsidx={latest_bbsidx}")
                print(f"   다음 실행부터 이 공지사항 이후의 새 공지사항만 알림을 보냅니다.")
            else:
                print("⚠️  bbsidx를 찾을 수 없어 기준점을 저장할 수 없습니다.")
        else:
            print("⚠️  공지사항을 찾을 수 없어 기준점을 저장할 수 없습니다.")
        return
    
    # 테스트 모드: 최신 1개를 강제로 전송 (state 무시)
    if test_mode:
        if announcements:
            test_announcement = announcements[0]
            print(f"🧪 테스트 모드: 최신 공지사항 1개를 강제로 전송합니다.")
            print(f"   - {test_announcement['title']}")
            
            # Dry-run 모드가 아니면 전송
            if not args.dry_run:
                if send_to_slack([test_announcement], webhook_url):
                    print("✅ 테스트 메시지 전송 완료 (state는 업데이트하지 않음)")
                else:
                    print("❌ 테스트 메시지 전송 실패")
                    sys.exit(1)
            else:
                print("\n🔍 Dry-run 모드: 실제로 전송하지 않습니다.")
        else:
            print("⚠️  테스트할 공지사항이 없습니다.")
        return
    
    # 최초 실행이고 send_on_first_run이 False면 스팸 방지
    if not is_initialized and not send_on_first_run:
        if announcements:
            latest_bbsidx = announcements[0]['bbsidx']
            if latest_bbsidx:
                processed_bbsidx.add(str(latest_bbsidx))
                save_state(processed_bbsidx, initialized=True)
                print("ℹ️  최초 실행: 기준점만 저장하고 알림은 보내지 않습니다 (스팸 방지)")
                print(f"   기준점: bbsidx={latest_bbsidx} ({announcements[0]['title'][:50]}...)")
                print("   다음 실행부터 새 공지사항이 있으면 알림을 보냅니다.")
                print("   최초 실행에서도 알림을 받으려면 --send-on-first-run 옵션을 사용하세요.")
                return
    
    # send_on_first_run 모드: 최초 실행이어도 최신 1개만 전송
    if not is_initialized and send_on_first_run:
        if announcements:
            # 최신 1개만 선택
            first_run_announcement = announcements[0]
            print(f"🆕 최초 실행 모드: 최신 공지사항 1개를 전송합니다.")
            print(f"   - {first_run_announcement['title']}")
            
            # Dry-run 모드가 아니면 전송
            if not args.dry_run:
                if send_to_slack([first_run_announcement], webhook_url):
                    # 전송 성공 시 state 업데이트
                    if first_run_announcement['bbsidx']:
                        processed_bbsidx.add(str(first_run_announcement['bbsidx']))
                    save_state(processed_bbsidx, initialized=True)
                    print("✅ 최초 실행 메시지 전송 완료")
                else:
                    print("❌ 최초 실행 메시지 전송 실패")
                    sys.exit(1)
            else:
                print("\n🔍 Dry-run 모드: 실제로 전송하지 않습니다.")
        else:
            print("⚠️  전송할 공지사항이 없습니다.")
        return
    
    # 새로운 공지사항만 필터링 (bbsidx 기준)
    # bbsidx를 문자열로 통일하여 비교
    new_announcements = []
    for ann in announcements:
        if ann['bbsidx']:
            bbsidx_str = str(ann['bbsidx'])
            if bbsidx_str not in processed_bbsidx:
                new_announcements.append(ann)
    
    # 디버깅: 처리된 bbsidx와 현재 공지사항의 bbsidx 비교
    current_bbsidx_list = [str(ann['bbsidx']) for ann in announcements if ann['bbsidx']]
    processed_bbsidx_list = sorted(list(processed_bbsidx), reverse=True)
    print(f"🔍 디버깅 정보:")
    print(f"   - 현재 공지사항 bbsidx: {current_bbsidx_list[:5]}... (총 {len(current_bbsidx_list)}개)")
    print(f"   - 처리된 bbsidx: {processed_bbsidx_list[:5]}... (총 {len(processed_bbsidx_list)}개)")
    print(f"   - 새로 발견된 bbsidx: {[str(ann['bbsidx']) for ann in new_announcements[:5]]}... (총 {len(new_announcements)}개)")
    
    if not new_announcements:
        print("✅ 새로운 공지사항이 없습니다.")
        return
    
    print(f"🆕 새로운 공지사항 {len(new_announcements)}개를 발견했습니다:")
    for ann in new_announcements[:5]:  # 처음 5개만 출력
        print(f"   - {ann['title']}")
    if len(new_announcements) > 5:
        print(f"   ... 외 {len(new_announcements) - 5}개")
    
    # Dry-run 모드
    if args.dry_run:
        print("\n🔍 Dry-run 모드: 실제로 전송하지 않습니다.")
        return
    
    # 새로운 공지사항들을 한 번에 Slack으로 전송
    if send_to_slack(new_announcements, webhook_url):
        # 전송 성공 시 처리한 bbsidx를 state에 추가 (문자열로 통일)
        for ann in new_announcements:
            if ann['bbsidx']:
                processed_bbsidx.add(str(ann['bbsidx']))
        
        # state 저장
        save_state(processed_bbsidx, initialized=True)
        print(f"✅ 처리 완료: {len(new_announcements)}개의 새로운 공지사항을 전송했습니다.")
        print(f"📊 저장된 처리된 공지사항 수: {len(processed_bbsidx)}")
    else:
        print("❌ Slack 전송 실패로 인해 state를 업데이트하지 않았습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()
