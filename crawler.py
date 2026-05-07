import json
import os
import requests


class BoardCrawler:
    def __init__(self):
        self.lounge_id = "sena_rebirth"
        self.api_url = f"https://comm-api.game.naver.com/nng_main/v1/community/lounge/{self.lounge_id}/feed"
        self.detail_url = f"https://game.naver.com/lounge/{self.lounge_id}/board/detail/"

        self.headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json, text/plain, */*',
            'Origin': 'https://game.naver.com',
            'Referer': f'https://game.naver.com/lounge/{self.lounge_id}/board/1'
        }

        self.monitored_boards = {}

        self.save_file = "board_cache.json"
        self.saved_ids = self._load_cache()

    # ------------------------
    # 캐시 저장/로드
    # ------------------------

    def _load_cache(self):
        if not os.path.exists(self.save_file):
            return {}

        try:
            with open(self.save_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"캐시 로드 실패: {e}")
            return {}

    def _save_cache(self):
        try:
            with open(self.save_file, "w", encoding="utf-8") as f:
                json.dump(self.saved_ids, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"캐시 저장 실패: {e}")

    # ------------------------
    # 게시판 등록
    # ------------------------

    def register(self, board_id, board_name, channel_id):
        if board_id in self.monitored_boards:
            return False

        last_ids = self.saved_ids.get(str(board_id), [])

        self.monitored_boards[board_id] = {
            "board_name": board_name,
            "last_ids": last_ids,
            "channel_id": channel_id
        }

        return True

    def unregister(self, board_id):
        if board_id in self.monitored_boards:
            del self.monitored_boards[board_id]
            return True
        return False

    def get_board_list(self):
        return list(self.monitored_boards.keys())

    # ------------------------
    # 게시글 조회
    # ------------------------

    def _fetch_posts(self, board_id):
        posts = []

        try:
            params = {
                'boardId': board_id,
                'buffFilteringYN': 'N',
                'limit': 5,
                'offset': 0,
                'order': 'NEW'
            }

            headers = self.headers.copy()
            headers['Referer'] = f'https://game.naver.com/lounge/{self.lounge_id}/board/{board_id}'

            response = requests.get(
                self.api_url,
                headers=headers,
                params=params,
                timeout=5
            )

            if response.status_code != 200:
                return []

            json_data = response.json()

            feeds = json_data.get('content', {}).get('feeds', [])

            for item in feeds:
                feed = item.get('feed', {})

                post_id = str(feed.get('feedId', '')).strip()
                title = str(feed.get('title', '')).strip()

                if post_id:
                    posts.append({
                        "id": post_id,
                        "title": title
                    })

        except Exception as e:
            print(e)

        return posts

    # ------------------------
    # 새 글 체크
    # ------------------------

    def check_new_posts(self):
        updates = []
        changed = False

        for board_id, data in self.monitored_boards.items():
            current_posts = self._fetch_posts(board_id)

            if not current_posts:
                continue

            current_ids = [post["id"] for post in current_posts]

            new_posts = [
                post for post in current_posts
                if post["id"] not in data["last_ids"]
            ]

            if new_posts:
                updates.append({
                    "channel_id": data["channel_id"],
                    "board_id": board_id,
                    "board_name": data["board_name"],
                    "posts": list(reversed(new_posts))
                })

                latest_ids = current_ids[:5]

                self.monitored_boards[board_id]["last_ids"] = latest_ids
                self.saved_ids[str(board_id)] = latest_ids

                changed = True

        if changed:
            self._save_cache()

        return updates