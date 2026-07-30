# tools

## manifest-workflow.yml

`.github/workflows/manifest.yml` 로 옮기면 동작하는 워크플로. 멤버가 `data/members/` 에
JSON 을 올리면 `data/manifest.json` 을 자동 재생성하고, weight 합·날짜 일치를 검증한다.

옮기는 방법 — 저장소 소유자가 GitHub 웹에서 `Add file → Create new file` 로
경로에 `.github/workflows/manifest.yml` 을 입력하고 이 파일 내용을 붙여넣으면 된다.
CLI 로 올리려면 `gh auth refresh -s workflow` 로 스코프를 먼저 받아야 한다.
