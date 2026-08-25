
## 2026-08-08 03:52
- 전체 테스트: Ran 131, FAILED failures=22 (로그: build/unittest_after_quota_reset.txt)
- 이 중 Gemini free-tier quota 관련: 21건
- quota 무관 1건: test_project_list_order (최근 열람 정렬 기대값 불일치)
- 참고: 이전 리포트의 '12건'은 당시 스냅샷; 재실행 후 22건으로 증가(live 실패 확대)
- UI/빌드 수정 범위: 웰컴 카드/공지·이벤트, 회전 문장, 결과 버튼 색, 이모지 스택, 알림 높이, CSS var(--surface)df8 수정
- 쿼터 회복(또는 유료 키) 후 live Gemini 테스트 재실행 필요
- 스모크: assist dry_run + schema + bait + export + app 로컬 스위트 별도 실행
