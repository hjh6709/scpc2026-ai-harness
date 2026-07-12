# SCPC 2026 AI Agent Harness — quick reference

`harness.py`는 700개 screening 태스크(+120개 dev)에 대해 결정론적 규칙만으로
`focal_id/target/control/content_scope/policy/plan_events`를 만들어 `submission.csv`로
제출하는 **stdlib-only Python 규칙 엔진**이다. LLM 호출 없음 — `FixedSLMClient`도 순수 함수.
전체 판단 히스토리와 각 라운드의 근거는 [docs/decisions-and-plan.md](docs/decisions-and-plan.md)에
연대기 순으로 있다 — 새 세션은 이 파일부터 읽고, 자세한 사정은 그쪽에서 검색.

## 채점 구조 (baseline notebook과 line-by-line 동일 확인됨)

`overall = focal*0.18 + target*0.12 + control*0.18 + content_scope*0.17 + policy*0.13 + plan*0.18 + semantic_response*0.04 + counterfactual*0.0`

- `target`/`control` = `focal_match * exact_match`; `content_scope`/`policy`/`plan` = `(target*control) * sub_score`
  → **focal이 틀리면 나머지 축이 전부 연쇄로 0점.** focal이 최우선 지렛대.
- `evaluate_dev.py`는 주최측 baseline의 `score_dev_submission()`을 그대로 옮긴 것 — 우리가 만든 임의 채점 기준이 아니다. 이 스크립트를 "더 세게" 바꿔도 실제 제출 점수엔 영향 없음(측정 도구를 바꾸는 것뿐).
- 로컬 dev 점수의 수학적 상한은 **0.96**(`semantic_response` 4%가 로컬에서 항상 0). baseline 자체 주석: 서버는 control 부분점수/필드명 정규화까지 있어 로컬보다 **관대**하게 나올 수 있음 — "hidden이 더 엄격할 것"이라는 가정은 근거와 반대.

## 절대 원칙 (반복해서 검증된 것들)

1. **추측 금지, 항상 dev_answers.json 또는 screening 재발생 횟수로 실증.** "그럴듯하다"는 근거가 아니다.
2. **트리거 문구를 추가하기 전에 screening 재발생 0건인지 반드시 확인.** dev에만 있고 screening 0건인 문구는 과거 실제로 dev 0.94→leaderboard 0.39 붕괴를 일으킨 패턴과 동일 시그니처.
3. **자유 어휘(target 이름 등) 하드코딩 금지.** `resolved_target`/`target_changed_after_turn` record나 object attrs에서 구조적으로만 도출.
4. **세션 종속 predicate는 반드시 `run_harness`와 동일하게 session_id/turn_index 순서로 실제 스레딩해서 검증.** `decide_control(view, focal, {}, {})`처럼 세션을 매번 빈 값으로 넘기면 전체 감사가 왜곡된다(700개 중 699개가 멀티턴).
5. **제안을 채택/기각하기 전에 (a) dev 전수 대조로 새 mismatch 0건 확인, (b) screening에서 새로 열리는 케이스를 최소 1~2건 직접 열어 답이 말이 되는지 확인.** 둘 중 하나라도 불가능하면 손대지 않는다.
6. **대회 규정상 금지:** task_id/session_id별 정답 매핑, public screening 전용 lookup table, 리더보드 피드백으로 answer 역산, public 태스크에만 맞춘 과도한 예외, dev_answers.json 값 암기 후 재적용, 외부 API/모델/토크나이저, numpy/pandas 등 미보장 외부 패키지.

## 알려진, 더 이상 재조사할 필요 없는 것들

- **target 미스매치 3건**(`6903fe98eb6a`/`511b1dc0b84d`/`a7f2a443f654`): 참조하는 cross-session memory write가 dev+screening 어디에도 없음 — 데이터 부재로 구조적으로 해결 불가능. 세 번 독립적으로 재확인됨.
- **`0937ccedef94` control 미스매치**: "우선한다"/"보류 후보로 남겼고"/`_unaddressed_prior_failure_recall` 세 가지 독립 시도 전부 반증됨(각각 dev 36/120 붕괴, 방향 반대 상관관계, 등). 안전하게 고칠 방법 없음.
- **`build_content_scope`의 "표면 문장만"(~line 1022)**: dev 근거 1건뿐이라 대안 자체를 검증할 수 없는 영역. 제거해도 screening 9건은 이미 fallback이라 무변화 — 순손실이라 그대로 둠.
- **`user_response`/`counterfactual`**: `dev_answers.json`에 이 필드 자체가 없어 로컬 검증 불가능. baseline의 해당 코드는 "일부러 약하게 만든 starter"라고 명시돼 있어 정답 레퍼런스로 못 씀 — 판단 근거가 원천적으로 없는 영역.

## 상시 검증 루틴 (수정 후 항상 실행)

```bash
python3 -m unittest discover -s tests -q
python3 evaluate_dev.py --tasks "<data>/dev_tasks.jsonl" --answers "<data>/dev_answers.json"
python3 audit_screening.py --tasks "<data>/screening_tasks.jsonl" --dev-tasks "<data>/dev_tasks.jsonl"
python3 generate_submission.py --tasks "<data>/screening_tasks.jsonl" --output submission.csv
```

`audit_screening.py`는 라벨 없이도 버그를 잡는 감사 도구다: exception sweep, answer shape invariants,
status_only 관례 체크, 미인식 object 필드, rule coverage, correction-clause 일관성, dev 대비 novel
record value, **order invariance**(objects/records 셔플해도 focal/target/control 불변해야 함 — 정답
라벨 없이 순서 의존 버그를 잡는 mutation test).

새 트리거 후보를 검증할 땐 매번 새 스크립트를 짜지 말고 `diagnostics/label_function_audit.py`의
`audit_label_function(predicate, field, implied_value, dev_tasks, dev_answers, screening_tasks)`를
써라 — dev coverage/경험적 정확도/conflicts/screening coverage를 한 번에 계산해준다. 단, 이건 원시
predicate의 1차 필터일 뿐 실제 코드의 특정 게이트에 물렸을 때 캐스케이드 효과까지는 못 본다 — 통과해도
채택 전엔 반드시 원칙 4(세션 스레딩 before/after 비교)를 따로 수행할 것.

## 커밋 컨벤션

- Co-Authored-By: Claude 트레일러 넣지 않음(사용자 지시).
- 사용자가 명시적으로 요청할 때만 커밋.
- 매 라운드 `docs/decisions-and-plan.md`에 근거/수치와 함께 기록(채택이든 기각이든).
