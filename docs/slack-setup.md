# Slack 연결 (주간 약관 확인 알림·승인)

운영자가 한 번만 한다. 토큰은 비밀번호와 같으니 채팅·git에 올리지 말고 서버 `.env`에만 넣는다.

1. https://api.slack.com/apps → **Create New App → From scratch** → 이름(예: 보험메이트 알림), 개인 워크스페이스 선택
2. **OAuth & Permissions → Bot Token Scopes**에 추가
   - `chat:write` (메시지 보내기)
   - `channels:history` (공개 채널의 "승인" 답글 읽기). 비공개 채널이면 `groups:history`
3. **Install to Workspace** → 허용 → **Bot User OAuth Token**(`xoxb-…`) 복사
4. 알림 채널(예: `#약관알림`)을 만들고 채널에서 `/invite @보험메이트 알림`
5. 채널 이름 클릭 → 맨 아래 **채널 ID**(`C…`) 복사
6. 내 프로필 → **⋮ → 멤버 ID 복사**(`U…`). 이 사람이 단 "승인"만 인정한다
7. 서버에서 `.env`에 추가(집 네트워크에서 접속):

```bash
ssh -i ~/.ssh/boheom-mate.pem ubuntu@3.36.241.30
cd ~/boheom-mate && nano .env
# 아래 세 줄 추가 후 저장
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
SLACK_OPERATOR_ID=U...
```

## 매주 하는 일

- 월요일 9시쯤 `[주간 약관 확인]` 요약과 `[새 약관]` 메시지가 온다.
- 약관 메시지를 읽고 반영하려면 그 메시지에 **"승인"**(딱 두 글자) 답글을 단다. 5분 안에 "반영 완료"가 달린다.
- "확인 필요"는 조항 나누기가 이상해 보인다는 뜻이다. 그래도 넣고 싶으면 승인할 수 있다.
- 반영하지 않을 약관은 답글을 달지 않으면 된다. 다음 주 요약에 "승인 대기"로 계속 보인다.
- "반영 실패"가 달린 약관은 자동으로 다시 시도하지 않는다. 원인을 고친 뒤 서버 `state/sync.json`에서 그 항목의 `"status"`를 `"pending"`으로 바꾸면 이미 단 "승인"으로 5분 안에 다시 시도한다.
- "자동 확인 불가" 보험사는 공시실에서 PDF를 직접 받아 서버 `data/inbox/`에 `보험사__상품 이름__판.pdf`(예: `삼성화재__무배당 실손__26.10.pdf`)로 넣는다. 다음 월요일에 같이 처리된다.
