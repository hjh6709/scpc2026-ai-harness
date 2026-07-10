# SCPC 2026 하니스 — 판단 기준과 계획

이 문서는 2026-07-09 세션에서 harness.py를 뜯어고치며 확립한 판단 원칙과, 앞으로의 작업 방향을 정리한 기록입니다. 새 세션에서 이어서 작업할 때 여기서부터 시작하면 됩니다.

## 문제의 본질

`FinalHarness.answer_task(task, session)`이 700개 screening 태스크 각각에 대해 `focal_id / target / control / content_scope / policy / plan_events`를 만들면, 서버가 비공개 정답과 비교해 채점합니다. 처음 상태는:

- 로컬 dev 점수(120개, 정답 공개): **0.9407**
- 실제 leaderboard(700개, 정답 비공개): **0.3928**

이 거대한 격차 자체가 신호였습니다 — dev의 120개 예제를 사실상 암기한 하니스였고, 대회 규정이 명시적으로 금지하는 방식("공개 dev 예시의 특정 문장을 그대로 외워 적용하는 방식")이었습니다.

**목표는 0.94다. dev 점수가 아니라 실제 leaderboard(제출) 점수 기준.** dev 점수는 참고용 보조 지표일 뿐이고, 언제든 실제 제출 점수와 괴리될 수 있다는 걸 이 세션 내내 확인해왔다. 다음 세션에서 이어받을 때도 이 목표 기준을 절대 dev 점수로 착각하지 말 것.

## 확립한 판단 원칙

1. **로컬 dev 점수를 신뢰하지 않는다.** dev에 없는 표현/패턴은 dev 점수에 전혀 반영되지 않으므로, 개선했는지 확인하려면 `screening_tasks.jsonl`의 실제 텍스트/구조 분포를 직접 대조해야 한다.
2. **자유 어휘(target 이름 등)는 절대 하드코딩하지 않는다.** `resolved_target` record, object의 `recipient/channel/attendee` 같은 attrs에서 구조적으로 도출하고, 없으면 generic fallback(`user`)으로 떨어진다. dev 답안 vocabulary를 역산해서 끼워 넣는 방식은 재현성 검증에서 적발될 위험이 크고 애초에 screening에 안 맞는다.
3. **텍스트 패턴을 코드에 추가하기 전에 반드시 screening에서 몇 번 나오는지 세어본다.** 등장 횟수, 그 문구가 다른 의미로도 쓰이는지(오탐 위험)를 확인한 뒤에만 추가한다.
4. **가설은 실제 코드 경로 추적 또는 dev ground truth 대조로 검증한 뒤에만 반영한다.** "그럴듯해 보인다"만으로 고치면 오히려 회귀를 만든다 (`security_alert`보다 로컬 업데이트 지시가 우선해야 한다는 걸 몰라서 순서를 잘못 바꿨다가 기존 테스트로 걸러진 사례가 실제로 있었음).
5. **control 축이 최우선이다.** 서버 채점식이 `target`과 `control`을 곱해서 `content_scope/policy/plan` 점수를 게이팅하기 때문에, control이 틀리면 나머지 4개 축이 통째로 0점이 된다. 그래서 target/scope 세부 튜닝보다 control 오분류를 잡는 게 leverage가 훨씬 크다.
6. **같은 의미의 correction 문구는 거의 같은 control로 수렴해야 한다.** 700개를 `단, ...` 같은 후행 절 기준으로 클러스터링해서, 한 클러스터 안에서 control이 여러 갈래로 흩어져 있으면(무작위 수준으로) 놓친 패턴이라는 강한 신호다. 반대로 이미 hold/ask 등 다른 정당한 신호(ambiguous_target, security_alert 등)가 섞여 있어서 자연스럽게 갈리는 경우도 있으니, 갈라진 원인을 반드시 추적해서 구분한다.
7. **통계만 보고 "괜찮다"고 넘기지 않는다.** ask control의 scope mode 분포가 dev 대비 치우쳐 있었을 때 처음엔 "새로 고친 ask 케이스가 원래 그런 경향일 것"이라고 넘겼는데, 실제로 특정 fallback 분기가 콘텐츠 신호를 아예 무시하는 버그였다. 이후로는 표면적 분포 차이도 실제 코드 경로까지 추적해서 원인을 확인한다.
8. **텍스트 문구뿐 아니라 구조적 record 값 자체도 dev와 대조한다.** `route_candidate_snapshot`/`dispatch_authority_check` 같은 핵심 필드가 screening에만 있는 새로운 값(예: `local_authority_confirmed`)을 쓰는 경우, exact-match 비교 로직이 전부 놓친다. 문구 감사만으로는 못 잡는 유형의 버그라 별도로 체크해야 한다.
9. **의미 있어 보이는 가설도 dev 정답으로 먼저 검증한다.** `user_binding_pending`이 "ask"를 의미할 거라 추측했는데, dev 정답을 대조해보니 실제로는 proceed/amend/hold에 고르게 분포하고 ask는 0건이었다 — 이름만 보고 판단하면 틀릴 수 있다.
10. **채점식에서 다른 축을 게이팅하는 축을 최우선으로 계측한다.** `focal_id`가 틀리면 `target`/`control`까지 0점 처리되고, 그게 다시 `content_scope`/`policy`/`plan`을 곱셈으로 0으로 만든다 — 사실상 전체 점수의 96%가 focal 하나에 걸려있다. choose_focal의 각 해석 경로(marker_trace/direct_id/regex/ordinal/window_scoring/fallback)가 700개 중 몇 개씩 쓰이는지 계측해서, 가장 약한 경로(window_scoring, 20%)를 표본 조사했더니 실제로 33% 규모 버그(서수 인식 실패)가 나왔다. 통계/클러스터링이 안 통하는 축이라도, "이 축이 전체 점수에 얼마나 큰 지렛대를 가지는가"부터 따져서 우선순위를 정해야 한다.
11. **세션 종속 로직을 검증할 때는 반드시 `run_harness`와 동일하게 session_id/turn_index 순서로 세션을 실제로 이어가며 계측한다.** `decide_control(view, focal, {}, {})`처럼 세션을 매번 빈 값으로 넘기면 700개 중 699개가 멀티턴 세션인 이 데이터셋에서는 전체 감사가 왜곡된다 — 세션 종속 predicate(`_prior_hold_followup` 등)가 "0건, dead branch"로 잘못 보이고, `infer_target`의 실제 분기 분포도 달라진다. 감사 스크립트 자체가 검증 대상 파이프라인과 다른 실행 모델을 쓰고 있지 않은지 항상 먼저 확인한다.

## 지금까지 한 일 (커밋 순서)

| 커밋 | 내용 |
|---|---|
| `6af99a1` | 하드코딩된 target vocabulary(clinic_portal, caregiver 등) 전부 제거, 구조 기반으로 재작성. choose_focal의 서수 판별 로직 일반화 |
| `6f5e4b0` | scope/policy/plan의 구조적 일관성 버그 5건 수정 (excluded_fields 기본값, external_share 플래그, redact.remove, requires_confirmation, precondition_changed/invalidated 공존) |
| `7f381b5` | 로컬 업데이트("공유 말고 내부 상태만") 텍스트 패턴을 dev 전용 표현에서 실제 screening 표현으로 재보정 — 131개 태스크 |
| `c506e94` | hold/ask correction 절 8개 클러스터의 screening 전용 문구 추가 — 대략 150개 태스크 |
| `6d7b2ec` | "공유 작업이 아니라" 로컬 업데이트 패턴 추가 — 22개 (처음엔 "다른 신호가 정당하게 우선한다"고 오판했던 걸 재검증 후 정정) |
| `ffd1d18` | "확정되지" 신호를 `_condition_uncertain`에 연결해 `decide_control`이 실제로 참조하도록 배선 — 44개 |
| `799c158` | ask control의 content_scope mode 최종 fallback이 콘텐츠 민감도 신호를 무시하던 버그 수정 — dev 검증 패턴과 정확히 일치하는 10개 확인 |
| `e10e4db` | choose_focal의 "통과/배제" 후보 선별 문장에서 마침표가 없어 세 후보가 하나의 윈도우로 묶이던 버그 수정 — focal_id는 다른 모든 필드를 게이팅하므로 임팩트 큼, 95개 |
| `d8a7b50` | `local_authority_confirmed`/`local_candidate_only` (구조적 record 값, screening에만 존재)를 "route 확정됨" 신호로 인식하도록 `ROUTE_CONFIRMED_VALUES`로 통합 — 65개 중 proceed 17→29 |
| `46057a9` | `audit_screening.py` 추가 — rule coverage / clause consistency / novel record value 세 가지 리포트를 재사용 가능한 도구로 정리 (아래 "상시 점검 루틴" 참고) |
| `b556258` | 문서 정리 — 판단 원칙 8, 9번 추가, 상시 점검 루틴 절 추가 |
| `c816840` | `user_response`/`audit_tags`/`counterfactual`를 제출에서 복원 — `semantic_response`(4%) 축을 로컬 채점기가 항상 0으로 고정해서 보여서 이 손실 자체를 감사로 못 잡고 있었음 |
| `38b31b5` | 견고성 테스트 20개(`test_robustness.py`) + `audit_screening.py`에 exception sweep/shape invariant 전수 검사 추가 |
| `7d7b936` | `excluded_fields` self-consistency 체크 시도 → dev 대조로 전제가 틀렸음을 확인하고 정정 (status_only의 dev 검증된 2가지 고정값만 체크로 축소) |
| `2a1dc37` | **focal 해석 경로를 700개 전체에 계측**해서 가장 약한 fallback(window_scoring, 142개=20%)을 표본 조사 → "유효한"이 긍정 신호 목록에 없어서/"둘째·셋째" 어간이 "두째·세째"로 잘못 매핑돼서 서수 인식 자체가 실패하던 버그 2건 발견·수정. 232개(33%) 영향, window_scoring 의존 142→57로 감소, 남은 57개는 전수 검증으로 전부 정답 확인 |
| `e91e4bd` | 남은 window_scoring 57개("참조는 X이다"/"binding은 X을...지정한다")를 명시적 regex 추출로 전환 → window_scoring 의존 0%로 제거 |
| (미커밋 감사) | "window_scoring만 줄이는 로직은 안 된다"는 지적에 따라 **marker_trace(463개, 66%)와 ordinal_scoring(85개, 12%)을 전수 재검증** — harness 코드와 무관하게 visible_history 텍스트에서 정답을 독립적으로 파싱하는 regex를 별도로 작성해(marker_trace는 6개 서로 다른 문장 패턴: 직접고정/서수나열/서수순서/통과제외/승인유지/binding지정) choose_focal의 실제 출력과 대조. 결과: marker_trace 463/463(100%) 일치, ordinal_scoring 85/85(100%) 일치, 불일치 0건. 두 경로 모두 코드 수정 불필요 — 이미 정답이었음을 검증으로 확인 |
| (미커밋 감사) | `decide_control`을 같은 방식으로 감사: 700개 전체에 대해 35개 분기 계측(dead branch 11개 확인 — dev 전용, screening엔 없음), correction-clause 클러스터 중 consistency<85%인 6개(72개 태스크)를 record 신호 조합 기준으로 재확인 → **동일 신호 조합에서 다른 control이 나온 사례 0건** (표면상 같은 "단, ..." 문구라도 실제로는 dispatch_authority_check/route_binding_order/external_share_policy 등이 달라서 갈린 것, 버그 아님). 결제 태스크 2개(4798d3b98f1a, 5c14a93d9604)에서 "금액 변경 여부도 봐줘"라는 프롬프트에도 proceed로 떨어지는 걸 처음엔 애매하다고 판단했지만, `amount_changed` record type이 dev+screening 820개 태스크 전체에서 단 한 번도 실제로 쓰이지 않는다는 걸 확인 — "조건부로 체크하라"는 프롬프트 문구는 해당 record가 실제로 있을 때만 개입하라는 뜻이고 없으면 proceed가 맞다는, 이미 검증된 L35(안전/범위 신호 없으면 proceed) 패턴과 동일 구조. 애매했던 걸 검증 가능하게 만들어 확인 완료 — 버그 아님 |
| (미커밋 감사) | `build_content_scope`/`build_policy`를 (control, 관련 record 신호, correction-clause, focal.type, sensitive contains) 조합으로 시그니처화해 700개 전체에서 동일-시그니처-다른-출력 케이스 탐색 → mode 5쌍/policy 5쌍 후보 발견. 각 쌍을 harness의 모든 내부 predicate(`_confirmation_precondition`/`_condition_uncertain`/`_plain_composite_plan`/`_mixed_local_external_confirmation` 등)로 재비교 → **5쌍 전부 실제로 다른 predicate 값을 가짐이 확인됨 (설명 안 되는 충돌 0건)**. 표면 구조 시그니처가 놓친 텍스트 차이가 실제 원인이었음을 규명 — 애매했던 "같은데 왜 다르지"를 predicate 단위까지 파내려가 검증 가능하게 만든 것. `build_plan_events`는 verb 시퀀스 분포(read+clarify 224/read+verify+update 179/read+guard 152/read+redact+dispatch 127/read+summarize+dispatch 11/read+dispatch 7, 합 700)가 이미 검증된 control/scope 분포와 정확히 1:1 대응함을 확인 — 추가 버그 없음. focal→control→scope→policy→plan 전 파이프라인 700개 전수 감사 완료 |
| `23dcd5f` | **감사 방법론 자체의 결함 발견**: 오늘 짠 모든 검증 스크립트가 `decide_control(view, focal, {}, {})`처럼 세션을 매번 빈 `{}`로 넘겼는데, 실제 `run_harness`는 같은 session_id 내에서 turn 순서대로 세션을 이어간다 — screening 700개 중 699개가 멀티턴 세션(단일 태스크 세션은 1개뿐)이라 이 차이가 전체 감사 결과를 왜곡할 수 있는 문제였다. `run_harness`와 동일하게 세션을 스레딩해서 재검증한 결과 두 가지 실제 버그를 발견: (1) `infer_target`의 T04 분기(`ask`+`ambiguous_focal`+`resolved_target` 존재 시 무조건 그 값 사용)가 "사용자에게 먼저 확인"류의 명시적 확인 요청 문구를 무시하던 버그 — dev 7건 전수 대조로 `_explicit_user_confirmation_requested` 가드 추가가 7/7 정확히 맞아떨어짐을 확인 후 수정 (screening 2건 영향). (2) `infer_target`이 세션 간 공유 메모리(`self.user_memory`, harness 인스턴스 전체에서 공유되고 session_id와 무관)를 전혀 참조하지 않아서, 다른 세션에서 쓴 `persistent_memory_write`를 나중 세션이 `persistent_memory_recall`로 다시 참조하는 경우 target을 못 찾던 구조적 공백 — dev 전체(9건, write가 실제로 존재하는 사례만)를 대조해 필드 선택 규칙을 도출(`memory_class=="standing_constraint"`→`approval_channel`, `"prior_result"`→`last_success_target`, "조명" 문구→`dusk_room`, "검진/점검" 문구→`health_channel`, 그 외→`preferred_channel`)하고 `infer_target`에 `user_memory` 파라미터 추가. dev target 불일치 13→3(남은 3건은 dev에도 대응하는 write가 아예 없어 구조적으로 복구 불가능 — 하드코딩 없이는 못 푸는 케이스라 그대로 둠), dev control 불일치는 1건 남음(`0937ccedef94`, last_failure_reason 기반 hold 판단 — screening 영향 2건뿐이라 n=1 표본으로는 규칙화하지 않고 기록만 남김). screening 14개 태스크(2%) target 변경, 전부 개선 방향(예: 가맹점명 "GalaxyStore"가 target으로 잘못 들어가던 게 정상화). 부수적으로 `audit_screening.py`의 `rule_coverage()`도 같은 세션-격리 버그가 있어서 `_prior_hold_followup`/`_prior_local_only_external_followup`가 "0건, 조사 필요"로 잘못 나오고 있었음 — 같은 방식으로 세션 스레딩하도록 수정, 수정 후 1건/60건으로 정상 계측 |
| (커밋 예정) | **외부 코드 리뷰(18개 항목)를 하나씩 검증**해 `receiving-code-review` 스킬 방식으로 처리: dev+screening 820개 전체에서 "raw" 부분 문자열이 오탐된 사례 0건(모두 `raw_quote`류 의도된 식별자) 확인 후 관련 지적 기각, 스레드 락/로깅 프레임워크/매직스트링 enum화는 이 코드베이스에 동시성 진입점이 없어 YAGNI로 기각. 그 과정에서 `policy.risk_flags`를 dev와 **정확히(set 단위)** 대조한 적이 이번 세션에 없었단 걸 발견 — 대조해보니 47/120(39%) 불일치. `requires_confirmation`(불리언)은 46/47 정확했지만 개별 태그가 자주 어긋남. 5개 버그를 dev 전수 대조로 하나씩 검증 후 수정: (1) `precondition_changed`는 `route_binding_order` 존재 + `dispatch_authority_check=="internal_binding_confirmed"`일 때 항상 붙어야 함(dev 24/24 vs 18/18 완벽 분리) — hold일 때 이 경로로 붙은 플래그는 `precondition_invalidated`로 덮여도 discard하지 않도록 수정(`violations: precondition_changed_ignored`가 있다는 건 애초에 감지된 플래그가 남아있어야 의미가 있음). (2) `control=="amend"`는 dev 28/28(100%) 항상 `external_share`를 가짐 — local_status 여부와 무관. (3) `control=="ask"`+`mode=="redacted"`는 dev 9/9 `minimal_disclosure`를 절대 안 가짐(아직 disclose가 일어난 게 아니라 확인 대기 상태라서) — 별도로 텍스트 기반 minimal_disclosure 트리거도 `not local_status`로 게이팅(부분 문자열 "summary_only"가 `persistent_memory_write`에 저장 중인 남의 enterprise_rule 값이나 `external_share_policy=="summary_only_allowed"`(허용 신호, 제한 신호 아님) 안에서 오탐되던 걸 발견). (4) `_target_ambiguity_signal`을 `ambiguous_target 존재 OR dispatch_authority_check=="user_binding_pending"`으로 재정의 — 기존엔 "authority_incomplete"/"dispatch_blocked_until_binding" 텍스트를 넓게 매칭해 dev 0/120 완벽 일치를 달성. `policy.risk_flags` 정확 일치 불일치 47→21(55% 개선), screening 176개(25%) 태스크 영향. `external_share`가 hold 케이스에서 여전히 13건 애매하게 남음 — route_binding_order/security_alert/consent 어느 단일 신호로도 안 갈리고 템플릿별로 다른 것으로 보여, 표본(13건)으로 더 파면 과적합 위험이 크다고 판단해 중단, 기록만 남김 |
| (커밋 예정) | **감사 스크립트 자체가 또 한 번 부정확했던 걸 발견**: `diagnostics/report.py`/`audit_screening.py`의 `rule_coverage()`/`tests/test_diagnostics_drift.py`가 전부 `build_content_scope`/`build_policy`를 `evidence={}`로 호출하고 있었는데, 실제 `FinalHarness.answer_task`는 `FixedSLMClient.summarize_task()`로 계산한 진짜 evidence를 넘긴다 — evidence의 `view.all_text` 기반(레코드뿐 아니라 object attrs까지 포함) 스캔이 `build_policy`의 좁은 레코드 전용 체크가 놓치는 `external_share` 일부 케이스를 채워주고 있었다. 실제 evidence로 재측정하니 위 policy.risk_flags 불일치가 21→16/120으로 더 좋아짐(코드 버그는 아니었음 — `submission.csv`는 애초부터 실제 evidence를 썼으므로 21이라는 숫자 자체가 감사 스크립트의 측정 오류였다). 세 파일 모두 real evidence를 쓰도록 수정. 외부 리뷰 5개 항목 추가 검증: `user_memory` 병합을 얕은 병합→깊은 병합으로 강화(dev+screening 실제 데이터는 전부 flat dict라 지금은 무영향이지만 공짜로 더 안전함, 적용). 나머지 4개(민감 필드 fallback 확장/ask에서 resolved_target 유지 확장/plan_events verb 분기/띄어쓰기 정규화/WM- 포맷 일반화)는 전부 dev 또는 820개 전체 데이터로 반증: fallback 확장은 dev 35/35→0/35로 역행하는 회귀, resolved_target 유지는 우려한 13개 dev 케이스가 이미 전부 정답, verb 분기는 dev status_only 39/39가 전부 "update"만 씀, 띄어쓰기/ID 포맷 변형은 820개 태스크 40만 자 전체에서 단 1건도 없어(완전 템플릿 생성 데이터) 일반화가 오히려 이미 검증된 정밀도를 낮출 위험만 있음 — 전부 기각 |
| (커밋 예정) | **"점수 올릴 부분 고안" 요청으로 dev 점수 axis별 재분해** — `evaluate_dev.py`의 `weak_record_buckets`/`worst_rows`를 훑다가 새로운 클러스터 발견: `status_only` 모드의 `excluded_fields`가 지금까지 항상 고정 3필드(`raw_quote`/`location`/`numeric_value`)였는데, dev 40개 중 8개는 빈 배열을 기대함. `session_share_policy`만으로는 안 갈렸지만(strict 29/29 full, normal은 8 empty+3 full로 혼재), **`session_share_policy=="strict"` OR (`"normal"`이면서 focal이 그 3필드 중 하나라도 실제로 포함) → 3필드, 그 외 → 빈 배열**로 정의하니 dev 40/40 완벽 일치. `content_scope` 축 0.9446→0.9612, `excluded_fields_f1` 0.931→0.997, 전체 dev 0.934→0.9368. screening 179개 status_only 중 13개 영향. 남은 알려진 갭은 hold의 `external_share`(13건, 여전히 단일 신호로 안 갈림)와 cross-session-memory write가 아예 없는 target 3건(구조적으로 복구 불가) 뿐 — 둘 다 표본이 작아 과적합 위험이 커서 보류 유지 |

**실제 leaderboard 결과 추이**:

| 시점 | 반영된 커밋 범위 | 로컬 dev 점수 | 실제 leaderboard |
|---|---|---|---|
| 최초 제출 | (과적합 버전) | 0.9407 | 0.3928 |
| 2차 제출 | `6af99a1`~`c506e94` | 0.8866 | 0.56 |
| 3차 제출 | 2차 + `6d7b2ec`~`daf3ac5`(CRLF 수정 포함) | 0.8866 | 0.5967 |
| 4차 제출 | 3차 + `e10e4db`~`38b31b5`(focal 버그, route 확정, user_response 복원, 견고성 등) | 0.8866 | **0.6534** |

로컬 dev 점수는 이 모든 과정에서 거의 그대로(0.94 → 0.88 → 0.8866)였다 — 이건 버그가 아니라, dev가 볼 수 없는 개선을 하고 있다는 증거로 해석해야 한다. **목표는 0.94 (실제 제출 기준, dev 아님).** 4차 제출 기준 남은 격차: 0.94 - 0.6534 = 0.2866.

## 상시 점검 루틴 (새 기준)

AI observability/eval 관행(질문·근거·응답을 로그로 남기고 자동 평가·실패 케이스를 축적해 반복 개선하는 것)을 이 프로젝트에 맞게 적용한 것. 코드를 고칠 때마다 다음을 실행:

```bash
python3 -m unittest discover tests
python3 evaluate_dev.py --tasks ".../dev_tasks.jsonl" --answers ".../dev_answers.json" --show 0
python3 audit_screening.py --tasks ".../screening_tasks.jsonl" --dev-tasks ".../dev_tasks.jsonl"
```

`audit_screening.py`가 새로 찾아주는 것:
- **rule coverage 0건인 함수**: dev에서 검증했지만 screening에서 한 번도 안 걸리는 규칙. 버그일 수도, screening에 그 시나리오가 그냥 없는 것일 수도 있다 — dev 정답으로 그 record 조합/값의 control 분포를 대조해서 판단한다(원칙 9).
- **correction 절 클러스터 중 consistency < 85%인 것**: 놓친 phrasing 후보. 단, ambiguous_target/focal 같은 다른 정당한 신호가 섞여서 자연스럽게 갈리는 경우도 있으니 개별 태스크를 추적해서 구분한다(원칙 6, 7).
- **dev에 없는 새 record 값**: 텍스트 감사로는 못 잡는 유형의 버그(원칙 8). 발견 즉시 exact-match 비교 로직 전체를 grep해서 어디서 놓치는지 확인.

## 남은 계획

1. **오늘(2026-07-09) 제출 횟수 소진.** 하루 3회 제한을 4차 제출로 다 썼을 가능성이 높음 — 사용자 확인 필요. 남은 시간은 검증/수정에 쓰고, 다음 제출 가능 시점에 배치로 반영한다.
2. **`mixed_local_external_candidates`/`redacted_after_selection_boundary`의 세부 조합별 처리.** 개별 태스크 트레이스로 여러 건 확인했고 전부 이미 검증된 correction 절로 정당하게 설명됨 — 추가 조사는 리스크 대비 수익이 낮다고 판단, 보류.
3. **KEY_VALUE_RECORD_TYPES 16개로 제한했던 novel-value 검사를 record type 전체로 확장.** 지금까지 큰 발견(local_authority_confirmed 등)이 이 검사에서 나왔는데, 확인 대상을 임의로 16개로 좁혀놨었다 — 전체로 넓혀서 놓친 게 더 있는지 확인 필요.
3. **policy.risk_flags/plan_events.args는 이미 감사 완료** — dev와 비율이 거의 일치하고, plan_events.args는 매핑 안 되는 값이 0건임을 확인함(더 팔 곳 없음).
4. **상위권 검증 대비 상태 점검.** `harness.py`는 이미 표준 라이브러리만 쓰고, task/session id 하드코딩 없음(`test_harness_source_does_not_hardcode_task_or_session_ids`로 강제), 결정론적(seed=42, temperature=0.0, 재실행 시 바이트 단위로 동일한 출력 확인됨). 이 부분은 이미 규정을 충족한 상태로 보이나, 상위권 진출 시 재확인 필요.
5. **본선 대비 PPT 준비는 아직 시작 전.** 발표자료에 넣을 좋은 스토리 후보: dev/leaderboard 격차 진단 과정 자체(overfitting 탐지 → 구조적 신호 기반 재설계 → screening 분포 직접 대조 검증 → 재사용 가능한 audit 도구화), 실제 leaderboard 피드백으로 검증한 개선 흐름.

## 제약과 리스크

## 추가 policy.risk_flags 정밀화 (같은 "점수 올릴 부분 고안" 라운드 계속)

`status_only` excluded_fields 수정 이후 `evaluate_dev.py`로 남은 불일치를 다시 훑어서 3건 더 찾아 고침 (전부 dev 전수 대조, 표본 크기와 mismatch=0 명시):

1. **`allowed_fields` 마지막 fallback**: `["summary", "status"]`였는데 dev 2/2(해당 fallback에 도달하는 순수 케이스 전부)가 `["summary"]`만 원함 — `"status"` 제거.
2. **`hold`의 `external_share`/`local_only`**: `_precondition_invalidated(view)`가 True면(사용자 자신의 요청 전제가 취소/무효화된 경우, 애초에 나갈 데가 없었음) `local_only`만, False면(security_alert/consent 철회/route 모호성 등으로 막힌 경우, 원래 외부로 나갈 뻔한 걸 막은 것) `external_share`를 붙임 — dev 18/18(hold 전체) 완벽 일치. `route_candidate_snapshot="external_candidates_present"`가 "external"을 부분 문자열로 포함해 기존 텍스트 매칭 규칙이 먼저 잘못 추가해버리는 걸 override 형태로 정리.
3. **`ask`+`ambiguous_focal`의 `external_share`/`local_only`**: 같은 `_explicit_user_confirmation_requested` predicate(오늘 세션 초반 target 필드에서 검증했던 것과 동일)로 분기 — 명시적 "사용자 확인" 문구가 있으면 `local_only`, 없으면 기본이 `external_share` — dev 7/7(ask+ambiguous_focal 전체) 완벽 일치.

dev 전체: 0.934(status_only 수정 전) → 0.9368 → 0.9384. policy 축 0.9567→0.9632, risk_flags_f1 0.975→(개선). 남은 dev 불일치 12건 — 대부분 n=1~2 표본(개별 case별로 서로 다른 예외 신호가 필요해 보이고, 일반화 가능한 패턴을 못 찾음)이라 과적합 위험 대비 추가 조사 중단. screening 109개(15.6%) 태스크 영향(이번 라운드 전체), `submission.csv` 갱신 완료.

## "일반화 가능한 패턴을 못 찾았으면 이유를 찾으라"는 지적 이후 재조사

이전 라운드에서 12개 dev 불일치를 "표본이 작아 과적합 위험" 이유로 보류했는데, 사용자가 "포기 말고 이유를 찾으라"고 지적 — 각 케이스를 근접한 dev 예시와 필드 단위로 전수 비교해서 다시 파봄. 이번에도 전부 최종 규칙은 해당 전체 모집단(n=6~40) 대조 0 mismatch 확인 후 적용:

1. **`b0696e0a0b55`/`d2a3fd50f334`** (missing external_share): `local_status`가 `share_boundary_update=="local_update_boundary"`라는 구조적 레코드만으로 True가 되고 있었는데, 이 두 태스크는 그걸 뒷받침하는 텍스트("보내지 말고"류)가 아예 없었음 — 반면 이미 맞던 케이스들은 전부 그 텍스트가 있었음. `control=="proceed" and local_status and not _is_local_update(view)` → external_share 추가. **dev 40/40**(proceed+local_status 전체).
2. **`1ada8b6f857e`/`b350a6b5a5ff`** (extra precondition_changed): `_external_binding_blocked`가 정상적으론 hold를 유발하는 신호인데, `_is_local_update`가 먼저 control을 proceed로 확정시켜버려 무의미해진 뒤에도 `precondition_changed` 계산엔 여전히 기여하고 있었음. hold는 구조상 `_is_local_update`가 항상 False일 때만 나오므로, `_external_binding_blocked(view) and not _is_local_update(view)`로 게이팅해도 hold 케이스엔 영향 없음 — **dev 7/7**(해당 신호가 True인 전체) mismatch 0.
3. **`5181075801a4`** (excluded_fields): doctor_note 분기가 이미 두 문구를 인식하는 `_doctor_note_external_scope_uncertain`을 쓰면서, 그 위에 불필요하게 좁은 문구("새 전제가 확정되지") 하나만 추가로 요구하던 중복 조건 제거. excluded_fields는 문구와 무관하게 고정, mode만 문구별로 분기.
4. **`083ee82f08f6`** (external_share/local_only): `persistent_memory_recall`이 있는 ask 케이스는 `_condition_uncertain`과 무관하게 항상 external_share를 유지해야 함(cross-session 메모리를 참조하는 시점에 이미 외부 공유 쪽으로 가고 있었으므로) — 기존 discard 규칙의 예외 목록(`ambiguous_focal`/`target_changed_after_turn`)에 추가. **dev 4/4**(ask+persistent_memory_recall 전체) mismatch 0.
5. **`0937ccedef94`** (control: hold, n=1처럼 보였던 것): 현재 턴 텍스트엔 hold 근거가 전혀 없고 다른 세션에서 recall된 `last_failure_reason`(과거 실패 이력)과 "바로"(확인 생략 요청)의 조합으로만 설명됨. **screening에서 완전히 동일한 템플릿(`ec6febf11406`, "~에게 ~쿠폰을 바로 보내줘. 예전에 말한 취향이 있으면 반영해.")을 발견해 n=1이 아니라 템플릿 매치임을 확인**. 같은 조합을 가진 dev 나머지 9개(`persistent_memory_recall`+`last_failure_reason` 존재)는 전부 이미 다른 경로로 정답이었음을 먼저 확인해 광범위한 규칙이 아님을 검증한 뒤, "바로"가 있고 명시적 확인 요청 문구가 없을 때만 hold로 좁혀 적용. `decide_control`/`build_policy`에 `user_memory` 파라미터 추가.

**남은 4건**(31fb29f3b379 mode 노이즈, a7f2a443f654/6903fe98eb6a/511b1dc0b84d target)은 "왜 안 되는지"까지 재확인 완료:
- `31fb29f3b379`: "점검 내용" 트리거 단어 자체가 dev 전체(n=3)에서 2:1로 갈리고, `session_share_policy`로 넓혀 봐도(n=24) 11개 불일치 — 단일/이중 필드로 환원 안 되는 진짜 노이즈로 결론.
- target 3건: `personal_memory` 필드(전부 빈 배열), 같은 사람 이름의 다른 프로필까지 교차 대조했지만(jimin은 dev+screening에 다른 프로필 3개 있음) 값이 다 다름 — 유일하게 검증 가능한 dev 사례(`7efad6a5e982`)로 재확인한 결과 애초에 쓰던 필드(`health_channel`)가 맞고 대안(`checkup_place`)은 틀렸을 것임을 확인, 우연의 일치였을 뿐 실제 데이터 자체가 없음이 최종 결론.

dev 전체: 0.9389 → **0.9444** (control 축 100%, focal 100%, target 97.5%, content_scope 97.2%, policy 97.5%, plan 97.5%). screening 35개 태스크 추가 영향, `submission.csv` 갱신·커밋 완료.

## "다른 방법도 찾아보자" 라운드 — user_response/counterfactual 언어 버그

로컬 dev 지표(`allowed_fields_f1`/`control_exact`/`excluded_fields_f1`/`focal_exact`/`plan_verbs_f1`/`risk_flags_f1`/`violations_f1`)가 전부 1.000이 된 뒤, semantic_response(4% 가중치, 로컬에서 항상 0으로 측정 불가)를 다시 검토하다가 organizer가 제공한 `SCPC2026_Final_baseline.ipynb`에서 **공식 레퍼런스 `user_response` 구현이 한국어**라는 걸 발견 — 데이터셋 전체(prompt/history/TERMS_GUIDE)가 한국어인데 우리 `user_response`/`counterfactual`은 영어였다. baseline 문구 그대로 한국어로 교체(`user_response`: hold/ask/amend/proceed 4가지, `counterfactual`도 동일). ASCII 전용을 요구하던 기존 테스트(`test_final_harness_answer_shape`)는 근거 문서 어디에도 없는 임의 가정이었음을 확인(schema/TERMS_GUIDE 어디에도 ASCII 제약 없음, 제출 CSV 자체가 UTF-8) — 정정.

부수적으로 baseline 노트북에서 `score_dev_submission`의 완전한 구현을 찾아 우리 `evaluate_dev.py`와 라인 단위로 대조 — 완전히 동일함을 확인(안심). 다만 baseline 주석에 "서버 공식 채점은 control 부분점수, content_scope 필드명 정규화, semantic_response(0.04)를 로컬 근사치가 완전히 반영하지 못해 서버 점수가 로컬보다 다소 높게 나올 수 있다"고 명시돼 있음 — 지금까지 걱정했던 "dev가 real보다 부풀려짐" 방향과 반대로, 구조적 하드코딩을 이미 다 제거한 지금 시점에선 **실제 제출 점수가 dev 0.9444보다 오히려 높을 가능성**을 시사.

- **screening 정답 raw data가 없다.** 모든 검증은 간접적(구조 상관관계, dev와의 패턴 일치, 클러스터 내부 일관성)일 수밖에 없어서, 일부 수정(특히 `799c158`의 ask scope mode 판단)은 "다음 제출 전까지는 100% 확신 불가"인 상태로 남아있다.
- **제출 횟수 제한**으로 실험적 변경을 남발하면 안 되고, 확신도 높은 변경을 모아서 배치로 제출하는 편이 낫다.
- **대회 종료 시점**을 확인해서 남은 라운드 수를 가늠해야 한다 (현재 세션에서는 파악 못함).

## Public/Hidden 과적합 리스크 자체 감사 — `_unaddressed_prior_failure_recall` 롤백

사용자가 대회 공식 규정 전문(Public vs Hidden 점수 격차, "공개 screening Task에만 맞는 예외 규칙을 과도하게 추가"가 규정 위반 판단 사유가 될 수 있다는 경고)을 그대로 인용하며 최근 추가한 규칙들을 다시 감사하라고 요청. 직전 라운드에서 추가한 13개 dev-검증 규칙 전체를 모집단 크기·근거의 구조적 독립성 기준으로 재분류한 결과, 가장 위험한 건 `0937ccedef94`/`ec6febf11406` 케이스에서 도출한 `_unaddressed_prior_failure_recall`(persistent_memory_recall + last_failure_reason + "바로" + 확인 문구 부재 → hold)이었음:

- 공개 데이터 820개(dev+screening) 전체에서 **정확히 2건**에서만 발동.
- 그 2건은 이름/물건만 바뀐 **동일 서사 템플릿**(피민/케이크 ↔ 하나/견과류)으로, 독립적인 두 사례가 아니라 사실상 같은 사례 하나를 두 번 관찰한 것에 가까움.
- Hidden 데이터에 이 정확한 조합이 없으면 기대 이득은 0, 있는데 조금이라도 다르게 변형돼 있으면(예: "바로" 대신 다른 표현, 다른 memory_class) 오히려 오답을 유발할 위험이 있음.
- 기대 이득(전체 태스크의 약 0.24%) 대비 "공개 task 구조에만 맞춘 예외 규칙"으로 해석될 규정 위반 리스크가 불균형하게 크다고 판단해 롤백 결정.

**롤백 내용**: `harness.py`에서 `_unaddressed_prior_failure_recall` 함수 정의와 `decide_control`/`build_policy` 내부 호출부 제거, 두 함수의 `user_memory` 파라미터도 함께 제거(더 이상 쓰는 곳이 없으므로). `diagnostics/trace_control.py`(대응 브랜치 `L02b_unaddressed_prior_failure_recall` 제거), `diagnostics/report.py`, `audit_screening.py`, `tests/test_diagnostics_drift.py`의 호출부도 함께 갱신. `infer_target`의 `user_memory` 파라미터(cross-session memory 기반 target 필드 라우팅)는 이번 롤백 대상이 아님 — 별도로 리스크 분류됨(아래 참고).

**검증**: 74개 유닛 테스트 전체 통과, drift guard 테스트 통과, dev 점수 0.9444 → **0.9389**로 정확히 롤백 대상 태스크(`0937ccedef94`) 1건 분만큼만 하락(다른 회귀 없음 확인). `submission.csv` 재생성 후 `ec6febf11406`의 control이 `hold`→`ask`로 되돌아간 것 확인, UTF-8 BOM+CRLF 포맷 유지 확인.

**아직 사용자 결정 대기 중인 나머지 두 건** (같은 감사에서 MEDIUM-HIGH 리스크로 분류했으나 이번엔 롤백하지 않음):
1. **`doctor_note` mode 분기**(redacted vs summary) — dev 검증 표본 n=2뿐. 다만 두 문구가 이미 존재하던 `_doctor_note_external_scope_uncertain`이 인식하는 문구 자체에서 자연스럽게 갈리는 구조라, 순수 lookup보다는 방어 가능할 수 있음.
2. **cross-session memory 기반 target 필드 라우팅**(`memory_class`별 approval_channel/last_success_target/dusk_room/health_channel/preferred_channel) — 서브 분기별 검증 표본 n=1~4. `memory_class`/키워드 이름 자체가 의미적으로 필드를 가리키는 구조라 순수 표면 패턴 매칭은 아니라고 볼 여지가 있으나, 표본이 작다는 점은 동일한 우려.

두 건 모두 다음 라운드에서 사용자와 함께 유지/롤백 여부를 결정하기로 하고 보류.

## 보류했던 두 건 + 유사 항목 전수 재조사 — "규정 위반이나 hidden 점수를 위해 다시 살펴봐" 라운드

지난 롤백 이후 사용자가 다시 한번 규정 위반/hidden 점수 관점에서 전체를 재검토하라고 요청. `_unaddressed_prior_failure_recall`을 롤백한 기준(모집단 크기, 근거의 구조적 독립성)을 남은 모든 예외 규칙에 동일하게 적용해 재분류했다.

**분석 방법**: 모든 named predicate(28개)가 dev에서 최소 몇 번이나 발동하는지 전수 카운트(`0건`인 것 — 즉 dev로는 전혀 검증할 수 없고 screening 구조만 보고 만든 규칙이 있는지 — 를 최우선으로 찾음). 결과: **0건은 하나도 없음.** 가장 적게 발동하는 것도 dev 1건은 있음(`_prior_success_memory_reuse`, `_surface_resolved_channel_conflict`, `_direct_reuse_followup` 등). 즉 "screening 구조에만 맞춰 만들고 dev로는 검증조차 안 되는" 유형의 규칙은 이미 없다는 뜻.

**보류 중이던 두 건 + 유사 항목 하나를 직접 트레이스로 재검증**:

1. **cross-session memory 필드 라우팅의 `조명`/`검진,점검` 키워드 분기** — 실제로 이 분기까지 도달하는(참조하는 memory_key가 실제로 이 세션에서 먼저 기록된) dev 사례가 각각 정확히 1건씩(`cf4f02fecf71`→dusk_room="living_room", `7efad6a5e982`→health_channel="caregiver")이고, 둘 다 정확히 일치함을 직접 트레이스로 확인. 서로 다른 두 도메인(조명/건강)에서 독립적으로 검증된 것이지, `_unaddressed_prior_failure_recall`처럼 같은 서사를 두 번 관찰한 게 아님. 근거 스키마 자체도 확인: `persistent_memory_write`의 값 dict는 dev+screening 전체 9건 중 8건이 **정확히 동일한 23개 키**(`approval_channel`/`dusk_room`/`health_channel`/`preferred_channel` 등)를 갖는 고정 스키마 — 즉 이 필드명들은 태스크별로 임의로 지어낸 게 아니라 데이터셋 전역에서 고정된 프로필 스키마의 일부. "조명"/"검진"/"점검"도 각각 그 주제를 가리키는 일반 한국어 단어(캐릭터 이름이나 특정 서사 문구가 아님). 이 규칙이 hidden에서 안 맞아도 영향 범위는 target 필드 하나(같은 세션 내 정확히 이 조합이 재현될 때만) — control을 오염시켜 여러 필드를 한꺼번에 깎아먹는 구조가 아님. **결론: 유지.**
2. **`doctor_note` 분기의 redacted vs summary mode 분기**(`새 전제가 확정되지` 문구 여부) — 이미 존재하는 `_doctor_note_external_scope_uncertain`이 인식하는 두 문구 중 어느 쪽이냐로만 나뉘고, dev 2건이 각각 다른 문구로 다른 mode를 100% 정확히 재현. 영향 범위도 `content_scope.mode` 서브필드 하나뿐. **결론: 유지.**
3. **비교 검증용으로 추가 조사한 `ambiguous_focal`+`resolved_target=="privacy_review"/"audit_vendor"` scope 분기** — `audit_vendor` 쪽은 dev 3건(redacted 2건 + summary로 정확히 갈리는 1건)으로 실제로는 표본이 더 큼. `privacy_review`→`none` 쪽은 dev 1건뿐이지만 마찬가지로 `content_scope` 서브필드 하나에만 영향. **결론: 유지.**
4. **비교 기준점으로 `build_content_scope`의 redacted/summary 판별에 쓰이는 대규모 트리거 단어 리스트**(`민감`/`점검 내용`/`원본`/`사진` 등, line 854/857)를 dev 전체 `ask` 태스크(26건) 대상으로 재검증 — 25/26 정확(유일한 불일치는 기존에 이미 "노이즈"로 결론 낸 `31fb29f3b379`). 대규모 표본에서도 잘 작동함을 재확인, 별도 조치 불필요.

**결론**: `_unaddressed_prior_failure_recall`을 위험하게 만들었던 두 가지 특징 — (a) `control` 필드 자체를 바꿔서 그 태스크의 target/content_scope/policy/plan 점수까지 연쇄로 0에 가깝게 만드는 큰 blast radius, (b) "증거"로 삼은 두 사례가 사실 이름만 바뀐 동일 템플릿이라 독립적인 근거가 아니었던 점 — 이 두 가지를 남은 후보들엔 적용할 수 없었다. 남은 규칙들은 전부 (a) `content_scope.mode`나 `target` 같은 서브필드 하나에만 영향을 주고, (b) 검증 사례가 서로 다른 독립적 시나리오였다. 따라서 추가 롤백 없이 유지하기로 결정.

**부수적으로 발견한 이슈(규정과 무관, 순수 코드 정리)**: `_enterprise_policy_review`, `_same_context_followup` 두 함수가 정의만 되어 있고 어디서도 호출되지 않는 죽은 코드였음(아마 이전 리팩터링 과정에서 호출부가 다른 함수로 대체되며 남은 잔재로 추정). 동작에는 전혀 영향 없지만 정리 차원에서 제거, `audit_screening.py`의 predicate 목록에서도 대응 항목 제거. 74개 테스트 전체 통과, drift guard 통과, dev 0.9389 그대로 유지(동작 변화 없음이 예상대로 확인됨), `submission.csv` 재생성 완료.

## "말대로 적용할 수 있다면 적용해" — 트리거 단어를 벗겨내는 재검증, 실제로 실행

바로 앞에서 "유지"로 결론냈던 세 항목("조명"/"검진,점검" 키워드 라우팅, `doctor_note` mode 분기)에 대해, 사용자가 이전에 내가 제안했던 "한국어 트리거 단어를 걷어내고 record 타입/필드 의미만으로 말이 되는지" 테스트를 실제로 실행하라고 요청. 세 항목 전부 직접 트레이스로 재검증했고, 이번엔 **표면적 population 크기가 아니라 "screening에 이 정확한 문구/신호가 재현되는가"를 기준**으로 판단했다. 이 기준을 잡게 된 결정적 계기: `git log -S`로 과거 히스토리를 보다가 이 프로젝트가 이미 한 번 정확히 이 문제로 크게 데인 적이 있다는 걸 발견함 — 커밋 `6af99a1`("Remove dev-set overfitting from focal/target/control logic")의 메시지: "Local dev score (0.94) diverged sharply from the leaderboard score (0.39) because ... matched dozens of exact Korean phrases ... that only occur in the 120 public dev examples, none of which fire on the 700 screening tasks." 즉 "dev 1건짜리 정확 문구 매칭"은 추상적 우려가 아니라 이 프로젝트에서 실제로 0.94→0.39 붕괴를 일으킨 적이 있는 검증된 위험 패턴.

이 기준으로 셋을 다시 판정:

1. **`doctor_note` mode 분기(`새 전제가 확정되지` 문구)** — screening 700개 전체에서 이 정확한 문구가 **0회** 등장함을 직접 확인(`grep`으로 phrase가 어느 screening task에도 없음을 검증). dev 2건 각각 redacted/summary로 정확히 갈리긴 했지만, 두 dev 태스크의 record/object 구조를 전수 비교한 결과 텍스트 문구 외에는 어떤 구조적 차이도 없었음(같은 `external_share_policy`, 같은 `session_share_policy`, 같은 object 스키마) — 즉 이 분기는 순수하게 "이 정확한 한국어 문구가 이 dev 태스크에 있었다"는 사실에만 의존. 이는 `6af99a1`이 제거했던 패턴과 정확히 같은 프로파일. **롤백**: mode를 항상 `"summary"`(분기 도입 전 기본값과 동일 계열, 같은 함수 내 다른 fallback들도 쓰는 값)로 고정.
2. **"검진/점검" → `health_channel` 키워드 라우팅** — 대상 dev 태스크(`7efad6a5e982`)를 구조적으로 재조사한 결과, focal object가 `message` 타입에 자유 텍스트 `body` 필드만 가지고 있어 "조명" 케이스와 달리 domain을 나타내는 구조적 필드가 전혀 없음 — 순수 텍스트 키워드 매칭이었음. 게다가 실제로 해당 dev 태스크의 memory profile을 직접 열어보니 `health_channel`과 `preferred_channel` 값이 **우연히 똑같이 "caregiver"** — 즉 이 특수 분기를 완전히 제거해도 이미 있던 범용 fallback(`preferred_channel`)만으로 정확히 같은 결과가 나옴, dev 점수 손실 0. **제거**(위험 감소 + 점수 손실 없음, 순수 이득).
3. **"조명" → `dusk_room` 키워드 라우팅** — 대상 dev 태스크(`cf4f02fecf71`)를 구조적으로 재조사한 결과, `choose_focal`이 실제로 골라내는 focal object가 `{"type": "iot_routine", "attrs": {"actions": ["light"], ...}}` — object 자체가 이미 구조적으로 "조명 동작"임을 선언하고 있음. 프롬프트의 "조명"이라는 단어를 걷어내고 `focal.get("type")=="iot_routine" and "light" in focal.attrs.actions`로 바꿔도 정확히 같은 dev 결과가 나옴 — 순수 텍스트 매칭을 구조적(스키마 기반) 판별로 교체. "조명"이라는 단어 자체도 screening 7건에서 재현되어(text 재현성도 확인) 이중으로 방어됨. **유지하되 구조적으로 재작성**: 이제 프롬프트 문구가 아니라 focal object의 `type`/`actions` 필드를 직접 읽음 — hidden 태스크가 다른 표현("불을 켜")을 쓰더라도 object 스키마가 같다면 여전히 맞음.

`diagnostics/trace_target.py`도 동일하게 갱신(구식 `T04d_recall_health_channel` 브랜치 제거, `T04c_recall_dusk_room`을 구조적 판별로 교체) — drift guard 테스트로 실제 `infer_target`과 계속 일치함을 확인.

**검증**: 74개 테스트 전체 통과, drift guard 통과, dev 0.9389 → **0.9384**(doctor_note 롤백으로 인한 예상된 소폭 하락, 나머지 두 변경은 dev 점수에 영향 없음 — 예측과 정확히 일치). `submission.csv` 재생성, `audit_screening.py` 재실행으로 새로운 이상 없음 확인.

**교훈**: population 크기(n=1, n=2)만으로는 위험을 다 못 잡는다 — 진짜 판별 기준은 "이 신호가 dev 밖(screening)에서도 재현되는가"와 "구조적 필드에 근거하는가"이고, 이 프로젝트는 이미 한 번 그 교훈을 비싸게 치른 적이 있다(`6af99a1`). 앞으로 새 규칙을 추가할 때는 dev 검증뿐 아니라 **screening에서의 문구/신호 재현 여부를 항상 같이 확인**하는 것을 표준 절차로 삼는다.

## 목표 0.96 라운드 — local dev 점수의 수학적 상한, 그리고 screening-recurrence 감사로 찾은 진짜 버그 2건

사용자가 "추측하지 말고 정확하게 hidden에서도 쓸 수 있도록 근거를 내서" 0.96을 목표로 고도화를 요청. 먼저 확인한 것: 채점 가중치(`focal*0.18 + target*0.12 + control*0.18 + content_scope*0.17 + policy*0.13 + plan*0.18 + semantic_response*0.04 + counterfactual*0.0`)에서 `semantic_response`(가중치 0.04)는 로컬에서 의미 기반 모델 없이는 항상 0으로만 측정됨(baseline 노트북에도 명시된 로컬 채점의 한계) — 즉 **local dev 점수의 수학적 상한은 정확히 0.96**(다른 모든 축이 완벽한 1.0일 때). 사용자가 요청한 목표점수 0.96은 사실상 "로컬에서 측정 가능한 모든 축을 완벽하게 만들어라"와 동일한 요구.

현재 dev 0.9384에서 남은 손실을 전수 분해한 결과, 이미 다 파악된 6개 태스크로 완전히 설명됨(다른 미지의 mismatch 없음을 `focal/target/control 모두 일치하는데 scope/policy만 다른 경우`까지 별도로 재확인):
- `0937ccedef94`(control): 이전 라운드에서 규정 위반 리스크로 의도적으로 되돌린 것.
- `6903fe98eb6a`/`511b1dc0b84d`/`a7f2a443f654`(target): 참조하는 cross-session memory write가 dev+screening 전체 어디에도 실제로 존재하지 않음 — 데이터 자체가 없어 코드로 해결 불가능.
- `31fb29f3b379`(content_scope.mode): 동일 트리거 단어가 더 넓은 population(n=24)에서 정확히 2:1로 갈리는 순수 노이즈로 이미 결론.
- `891dd2e62a0a`(content_scope.mode): 이전 라운드에서 screening 재현율 0으로 확인되어 의도적으로 되돌린 것.

즉 "쉽게 더 딸 수 있는 dev 점수"는 이미 없다 — 남은 격차를 메우려면 방금 되돌린 것과 같은 종류의 위험한 규칙을 다시 추가해야 한다. 그래서 방향을 바꿔 **dev 점수에는 안 잡히지만 hidden 일반화에는 영향을 주는 구조적 결함**을 찾기로 함 — `audit_screening.py`의 "Rule coverage" 섹션(각 predicate가 screening 700개 중 몇 번이나 실제로 발동하는지)을 전수 재검사.

**핵심 방법론 교정**: `rule_coverage()`와 내 첫 조사 스크립트 둘 다 predicate 함수를 단독 호출해서 세는 방식이었는데, 이건 `decide_control`의 if-chain에서 그 앞의 조건이 먼저 걸려서 실제로는 도달하지 않는 경우까지 "발동"으로 잘못 셀 수 있다(이 프로젝트가 이미 한 번 겪은 "session-isolation 버그로 0건처럼 보임" 문제의 사촌격 - 이번엔 반대로 과대 카운트 방향). `decide_control_traced`로 실제 도달 브랜치까지 확인해야 진짜 population을 알 수 있음 — 새로 표준 절차로 채택.

**Rule coverage 0건인 predicate 4개를 전수 재조사**:

1. **`_guardrail_verified_external_route`, `_surface_resolved_channel_conflict`** — 둘 다 순수 구조적(record type/enum 값만 사용, 한국어 문구 매칭 없음). 이들이 참조하는 개별 필드값(`route_candidate_snapshot=="single_internal_candidate"` 등)은 screening에서 각각 57~103회씩 재현됨 — 이 predicate들의 screening 0건은 "5개 조건이 동시에 다 맞아떨어지는 조합"이 우연히 screening에 없었을 뿐인 조합적 희소성이지, 존재하지 않는 어휘에 의존한 게 아님. **조치 없음, 유지.**
2. **`_same_place_check_summary`** — `decide_control`에서 **`control` 필드를 직접 결정**(blast radius가 지난번 되돌린 규칙과 동급). 원래 조건 4개 중 3개("같은 곳"/"점검 내용" 등)는 screening에 18회나 재현되는데, 나머지 하나("최근 동의"/"최신 consent")만 dev 50건에 있고 screening 0건 — 이 한 조건이 사실상 이 규칙 전체를 screening/hidden에서 죽은 코드로 만들고 있었음. 이 문구가 나오는 dev 태스크 4개를 직접 열어보니 전부 `session_share_policy=="strict"`였고, 유일하게 이 시나리오인데 "proceed"를 원하는 dev 태스크(`aca57c383d4c`)는 `session_share_policy=="normal"` — **구조적 필드로 완전히 치환 가능**함을 발견. `session_share_policy=="strict"`로 교체 후 dev 전체 재검증: 기존 4건 + 새로 포함된 1건(`aca57c383d4c`) 전부 정확, 새 mismatch 0. screening에서도 실제로 이 필드가 정확히 갈리는 것 확인(normal 15건, strict 3건) → **구조적으로 재작성, screening 재현 0건→3건.**
3. **`_summary_only_composite_plan`** — 같은 "최근 동의"/"최신 consent" 문구를 5개 조건 중 하나로 요구하고 있었고, 나머지 4개 조건(요약본만/임시 알림/masked_ref 등)은 screening에서 각각 25~100회씩 재현됨. dev 2건 모두 이 문구가 있든 없든 결과가 똑같아(제거해도 dev 영향 0) 안전하게 제거 가능함을 먼저 확인. 제거 후 screening에서 새로 3건이 이 규칙에 걸렸는데, 그중 실제로 답이 바뀌는 1건(`final_screening_ef91f9b790d0`)을 직접 열어보니 — object의 `contains`가 `["summary", "raw_quote", "name", "location"]`이고 요청 문구가 명시적으로 "요약본만 보낸 뒤"(summary만 보내라)인데, 기존 코드는 `proceed`+`mode="raw"`로 답해서 raw_quote/name/location까지 그대로 새 나가는 답을 내고 있었음 — **명백한 버그**(사용자 요청과 정면으로 모순). 제거 후 `amend`+`mode="redacted"`로 정정됨. **제거, screening 재현 0건→3건, 그중 1건은 실질 오답을 수정.**

**검증**: 74개 테스트 전체 통과, drift guard 통과, dev 0.9384 → **0.9384**(예측대로 dev 영향 0 — 두 변경 모두 dev population을 그대로 보존하면서 screening/hidden 재현성만 개선). `submission.csv` 재생성, `_same_place_check_summary`/`_summary_only_composite_plan` screening 발동 횟수 0→3/0→3으로 확인.

**결론 (0.96 목표에 대한 정직한 평가)**: local dev 0.9384는 이미 "안전하게 고칠 수 있는 모든 것"을 고친 상태이고, 남은 0.0216만큼의 격차는 (a) 데이터 자체가 없어 코드로 못 푸는 3건, (b) 규정 위반 리스크 때문에 의도적으로 포기한 2건으로 전부 설명됨 — dev 점수만 억지로 0.96에 맞추려면 이번 세션에서 두 번이나 되돌린 것과 같은 위험한 패턴을 다시 넣어야 한다. 대신 이번 라운드는 dev 점수엔 안 보이지만 hidden 일반화에는 실제로 영향을 미치는 두 건(하나는 순수 위험 감소, 하나는 확인된 실질 버그)을 찾아 고쳤다 — **"dev 축 완벽화"가 아니라 "screening/hidden에서 실제로 작동하는 코드로 만들기"가 이제부터의 올바른 고도화 방향**이라는 게 이번 라운드의 핵심 결론.

## "다른 부분들에서는 부족한 부분이 없나" — harness.py 전체 문구 인벤토리 감사

앞선 두 건(`_same_place_check_summary`, `_summary_only_composite_plan`)을 찾은 방법(0-hit predicate 조사)은 **이름 붙은 predicate 함수**만 잡아낸다는 한계가 있음 — `decide_control`/`build_content_scope`/`build_policy` 안에 직접 인라인된 `_has_value(...)` 호출은 놓친다. 그래서 이번엔 `harness.py` 전체에서 조건문에 쓰이는 **모든 리터럴 한국어 문구 98개를 정규식으로 추출**해서 각각의 dev/screening 등장 횟수를 전수 대조했다.

**발견 1 — `_external_binding_blocked`의 `"표면 문장"` 제외 조건 (dev 40건 / screening 0건)**: 두 번째로 큰 dev-only 문구였음("최근 동의"의 50건 다음). 원인은 dev와 screening이 "표면 텍스트만 보지 말고 세션 상태를 확인하라"는 **같은 지시를 완전히 다른 문장으로 표현**하기 때문 — dev 1위 오프닝 문장은 "표면 문장만 보지 말고 세션 상태까지 확인해서 처리해줘."(40건, screening 0건)인 반면, screening 1·2위 오프닝은 "이번 턴에서는 보이는 요청과 최신 상태 기록을 함께 적용한다."(182건)와 "아래 요청은 최근 메모리와 정책 상태를 같이 읽어야 한다."(172건)인데 이 둘은 dev에 0건. 즉 이 오프닝 문장 자체는 순수 서사적 보일러플레이트(내용 없는 도입부)이고, dev와 screening이 그냥 다른 버전을 썼을 뿐.

이 조건을 통째로 지우면 dev 2건이 새로 깨짐(`681d2e291ea5`, `0ad44d955594` - 둘 다 `ask`를 원하는데 `hold`가 나옴) — 즉 이 조건이 막아주던 진짜 케이스가 있다는 뜻. 두 태스크를 직접 열어 구조적 차이를 찾음: 둘 다 `ambiguous_focal == "multiple_focal_candidates_present"`. 하지만 이걸 단순 대체하면 또 다른 dev 태스크(`0ab2e0715082`, 같은 `ambiguous_focal` 상태인데 `hold`를 원함)가 깨짐 — 차이를 더 파보니 `dispatch_authority_check`가 셋 중 이것만 `"user_binding_pending"`이고 나머지 둘은 `"authority_incomplete"`. 이 구분은 이미 `_target_ambiguity_signal`에 "dev-verified 0/120"으로 문서화된 기존 원칙("user_binding_pending=진짜 사용자 바인딩 대기, authority_incomplete=시스템 권한 해석 중일 뿐")과 정확히 일치 — 새로 지어낸 게 아니라 이미 검증된 구조적 원칙의 재사용. `ambiguous_focal == "multiple_focal_candidates_present" and dispatch_authority_check == "authority_incomplete"`로 교체 후 dev 전체 재검증: 기존에 이미 받아들이기로 한 `0937ccedef94` 1건 외 새 mismatch 0건(control뿐 아니라 content_scope.mode/risk_flags까지 포함해도 0건). screening에서 `_external_binding_blocked` 발동 56건→52건으로 변화(4건이 새로 "hold" 대신 다른 control로 바뀜, 구조적으로 더 정확해짐).

**발견 2 — 같은 함수 내 `"privacy_review"` → `mode="none"` 분기의 `"표면 문장만"` (dev 1건)**: 이것도 screening 0건이지만, 이번엔 **loosening을 보류했다**. 이유: dev가 단 1건뿐이라 대조군이 없고, 문구를 지웠을 때 screening에서 새로 걸리는 9건을 직접 열어보니 dev 예시(`route_candidate_snapshot="external_candidates_present"`, `dispatch_authority_check="authority_incomplete"`)와 완전히 다른 구조(`local_candidate_only`/`mixed_local_external_candidates`, `local_update_boundary` 등 훨씬 "로컬" 쪽에 가까운 신호)를 가지고 있었음 — 즉 dev의 단일 사례에서 관찰된 "완전히 보류(mode=none)"라는 결론이 이 9건에도 적용되는지 검증할 근거가 없음. `_summary_only_composite_plan` 때는 실제로 내용을 열어봐서 명백한 모순(요약만 요청했는데 raw로 답함)을 확인하고 고쳤지만, 이번엔 그런 확증이 없어 **건드리지 않기로 결정** — "dev-only라서 무조건 완화"가 아니라 매번 개별적으로 확증 가능한지 확인하는 게 원칙.

**발견 3 — 같은 함수의 `audit_vendor` 분기에 있는 `"누구에게 어떤 범위"` 부정 조건 (dev 3건 / screening 0건)**: 실제로 이 negation이 관련 있는 dev 3개 태스크에서 전부 이미 `False`였음(즉 원래도 아무 영향이 없었음) — dev에서도 screening에서도 한 번도 실질적으로 작동한 적 없는 조건. 해롭진 않으니 그대로 둠.

**발견 4 — OR-list 구조인 `_precondition_invalidated`/`_condition_uncertain`/`_explicit_user_confirmation_requested`**: 각각 몇 개 구성 문구가 screening 0건이지만(예: "허용 근거", "누구에게 어떤 범위" 등), **같은 리스트의 다른 문구들이 screening에서 15~40회씩 살아있어서** 전체 predicate는 건강함(예: "믿을 수 없으므로" 0/15, "허용의 근거" 0/21, "확정 정보가 없" 0/20 등이 각각 dev/screening에서 정확히 반대로 강함 — dev와 screening이 서로 다른 동의어를 쓰지만 OR로 묶여 있어서 어느 한쪽만 봐도 서로를 보완함). AND 조건에서 문구 하나가 dev-only면 전체가 죽지만, OR 리스트에서는 무해한 중복일 뿐 — 이 구조적 차이가 왜 이 셋은 안전하고 앞서 고친 것들은 위험했는지의 핵심 기준.

**검증**: 74개 테스트 통과, drift guard 통과, dev 0.9384 유지(변경 없음, 예측대로), `submission.csv` 재생성, `audit_screening.py` 재확인 이상 없음.

**정리된 원칙**: dev-only 문구를 발견했다고 무조건 고치는 게 아니라, (1) AND 체인의 유일한 게이트인지 OR 리스트의 중복 멤버인지 구분하고, (2) 대체/제거가 dev 전체에서 새 mismatch 0건임을 반드시 확인하고, (3) 새로 열리는 screening 케이스를 최소 1~2건 직접 열어서 답이 실제로 말이 되는지 확인할 수 있을 때만 적용한다 — 세 조건 중 하나라도 확인 불가능하면(이번 발견 2처럼) 손대지 않고 현상 유지한다.

## 실제 리더보드 0.752 (하위권) — CSV BOM 불일치를 발견하고 수정

로컬 dev 0.9384까지 다듬은 뒤 실제 제출 결과를 확인하니 **Public 리더보드 0.752, 순위는 하위권**이었음. 이번 세션에서 찾아 고친 개별 버그들은 전부 수십 개 태스크 단위였는데, dev-리더보드 격차(0.186)와 "하위권"이라는 순위는 그 정도 규모로는 설명이 안 됨 — 개별 규칙 문제가 아니라 **광범위하게 뭔가 깨졌을 가능성**을 의심하고 원점(제출 파일 형식)부터 재점검함.

**발견**: `harness.py`의 `write_submission_csv`가 `encoding="utf-8-sig"`(BOM 있음)로 파일을 썼는데, 공식 `SCPC2026_Final_baseline.ipynb`의 reference `write_submission_csv` 함수는 `encoding="utf-8"`(BOM 없음)를 씀. 지금까지 이 불일치를 못 알아챈 이유: 우리가 만든 파일을 검증할 때도 항상 우리가 쓴 것과 같은 `utf-8-sig`로 읽어서 대조했기 때문에(self-consistent), 실제 채점 파이프라인이 baseline 코드와 같은 방식(`encoding="utf-8"`, BOM 비고려)으로 읽을 가능성을 한 번도 테스트하지 못했음.

직접 재현: `open(path, encoding="utf-8", newline="")`로 우리 BOM 있는 파일을 읽으면 헤더가 `'submission'`이 아니라 `'﻿submission'`으로 나옴 — 컬럼명 매칭 기반의 읽기 로직이라면 `KeyError`나 전체 파싱 실패로 이어질 수 있는, 전체 700개 태스크에 균일하게 영향을 주는 실패 모드. "하위권"이라는 결과와 정확히 일치하는 규모(개별 태스크가 아니라 파일 전체 단위 실패).

참고로 `sample_submission.csv`는 실제로 BOM이 있음(이전 세션에서 이 파일과 바이트 단위로 맞춰서 "정상"이라고 판단했던 게 오히려 함정이었음) — 하지만 TERMS_GUIDE.md가 이 파일의 역할을 "제출 CSV 형식 예시"라고만 설명할 뿐, 실제 채점 파이프라인이 이 예시 파일을 생성한 것과 같은 방식으로 다시 읽어들이는지는 확인할 방법이 없음. 반면 baseline 노트북의 `write_submission_csv` 함수는 주최측이 참가자에게 "이대로 구현하라"고 준 **실행 가능한 reference 코드**이므로, 그 코드가 실제로 만들어내는 바이트 형식(BOM 없음)에 맞추는 쪽이 훨씬 안전한 선택이라고 판단 — 예시 파일의 바이트 형식보다 예시 코드의 바이트 형식을 신뢰하기로 함.

**조치**: `write_submission_csv`를 baseline과 완전히 동일하게 `encoding="utf-8"`(BOM 없음) + `csv.writer`로 교체. `tests/test_harness.py`의 `test_write_submission_csv_round_trips_json`도 "BOM이 있어야 한다"에서 "BOM이 없어야 한다"로 반대로 갱신(이전 가정 자체가 틀렸던 것이므로).

**검증**: 74개 테스트 통과(갱신된 BOM 부재 검증 포함), drift guard 통과, dev 0.9384 그대로(순수 파일 I/O 변경이라 로직에는 영향 없음 - 예측대로), `submission.csv` 재생성 후 `open(..., encoding="utf-8")`로 읽었을 때 헤더가 정확히 `'submission'`으로 읽히는 것 확인.

**남은 불확실성**: 이게 실제 원인인지는 다음 제출 결과로만 확인 가능함(리더보드에 세부 axis 점수가 없어 로컬에서 direct 검증 불가). 이것이 유일한 원인이 아닐 수도 있으므로, 다음 제출 후에도 여전히 격차가 크면 다른 광범위한 원인(예: novel record value 처리, 대량 샘플링된 구조적 조합에서의 체계적 오류)을 계속 의심해야 함.

**업데이트(재제출 결과)**: BOM 제거 버전으로 재제출했으나 점수가 **동일하게 0.752**로 나옴 — BOM 가설은 반증됨. 이건 오히려 유용한 정보: 채점 파이프라인이 파일을 정상적으로 파싱하고 있었다는 뜻이고, 0.752는 파일 형식 문제가 아니라 **답안 정확도 자체**를 그대로 반영한 값임이 확인됨. 진단 방향을 다시 로직/데이터 커버리지 쪽으로 돌림.

**정량적 재확인**: dev(120)와 screening(700)에서 `(record_type, value)` 조합을 전수 대조한 결과, **screening 태스크의 50.4%(353/700)가 dev에는 단 한 번도 없었던 조합을 최소 하나는 포함**함. 즉 우리가 dev로 "N/N 전수 검증"했다고 믿었던 규칙들도 애초에 screening의 절반에는 적용된 적이 없었던 셈 — 이번 세션 내내 찾은 개별 버그(수십 개 태스크 단위)로는 0.186점 격차를 설명하기 부족하고, 근본적으로는 "120개로 튜닝한 규칙이 700개의 다양성을 원천적으로 다 커버 못한다"는 구조적 한계일 가능성이 큼(사용자에게도 이 대회의 dev/screening/hidden 3단계 분할 자체가 일반화 테스트를 위한 의도적 설계임을 설명함).

## "baseline을 아주 꼼꼼하게 읽어볼래" — 노트북 17개 셀 전수 재검토

지금까지 baseline 노트북을 부분적으로만(검색 기반으로) 읽었는데, 사용자가 전체를 꼼꼼히 다시 읽으라고 요청. 17개 셀 전부를 순서대로 읽고 우리 코드와 하나씩 대조함.

**찾은 것 — `personal_memory`를 우리 하니스가 전혀 안 읽고 있었음**: baseline의 reference `FixedSLMClient.summarize_task`는 `personal_memory[*].text`를 evidence 계산에 포함시키는데, 우리 `TaskView.all_text`/`_has_value`는 `personal_memory`를 아예 스캔하지 않았음. `TERMS_GUIDE.md`는 `personal_memory`를 prompt/device_state/visible_history와 동급의 "대표적인 최상위 필드"로 명시하고, "task 안에 공개된 prompt, device_state, records, visible_history, personal_memory를 해석하는 로직 구현"을 권장 패턴으로 직접 언급함 — 이 필드를 완전히 무시한 건 명백한 누락.

이전 세션에서 "personal_memory는 dev 17건 기준으로 전부 이미 구조적 신호로 커버되는 중복 정보라 안 읽어도 된다"고 결론 냈었는데, 이번에 screening까지 다시 보니: **screening은 81건에 5종의 서로 다른 텍스트**(dev는 17건에 3종)를 가지고 있고, 그중 2종은 dev에 아예 없던 새 내용(`지난 세션에서 project_room은 내부 채널로 확인되었다`, `외부 공유에는 원문 인용과 위치 정보를 넣지 않는다`). 이 두 문구를 모두 가진 screening 태스크(`final_screening_12200624b6b7`)의 prompt는 **"지난번 선호를 반영해서 이 내용을 공유해줘"** — personal_memory를 읽으라는 명시적 지시임. dev만 보고 "필요 없다"고 판단했던 게 screening의 더 넓은 분포에서는 틀렸을 가능성.

**조치**: `TaskView`에 `personal_memory_text` property 추가, `all_text`와 `_has_value`에 반영. dev 전체 재검증: **기존에 이미 받아들인 6건의 mismatch 외 새 mismatch 0건**(personal_memory monkeypatch 테스트로 사전 확인 후 실제 코드에 반영) — dev에 안전, screening 81건에는 이제 실제로 신호가 전달됨.

**추가로 대조한 것들(문제 없음, 기록만)**:
- `participant_task_view`/`REMOVED_SCORING_KEYS`(baseline이 raw task에서 `expected_*`/`*_brief`/`*_notes`/`*_rubric`/`*_keywords`/`*_tags`류 필드를 미리 제거하는 로직) — 우리 `dev_tasks.jsonl`/`screening_tasks.jsonl`에 그런 필드가 실제로 하나도 없음을 확인, 이미 정제된 참가자용 파일이라 우리가 따로 처리할 필요 없음.
- `meta.seed` 값(baseline 노트북은 2026, TERMS_GUIDE.md 예시는 42, 우리는 42 사용) — `validate_payload`/채점 축 어디에도 seed가 실제로 검증/채점되는 곳이 없어 순수 메타데이터, 스코어에 영향 없음.
- **`evaluate_dev.py`의 스코어링 함수 전체**(`_f1`/`_scope_score`/`_policy_score`/`_event_similarity`/`_plan_score`/`score_dev_submission`, `target=focal*match`/`control=focal*match`/`dependent=target*control` 게이팅, `WEIGHTS`, `PUBLIC_PLAN_ARG_VALUES` 95개)를 baseline 노트북 원문과 함수 단위로 전부 diff — **완전히 동일**함을 재확인. 즉 우리 로컬 dev 0.9384는 서버가 계산할 값과 동일한 방식으로 계산된, 신뢰할 수 있는 수치.
- `PLAN_ARG_VALUE_ALIASES` 140개 중 55개가 우리 evaluate_dev.py에 없었는데, 54개는 key==value인 identity mapping이라 fallback 로직(`PUBLIC_PLAN_ARG_VALUES`에 있으면 그대로 반환)과 결과가 같아 실질적으로 무해. 단 1개(`"scope_pair_consent": "consent_check"`)는 진짜 누락이라 추가함 — 로컬 채점 도구 자체의 정밀도 문제였고 실제 리더보드 채점(서버가 자체 완전한 테이블을 쓸 것이므로)과는 무관.
- `validate_payload`(우리 harness.py 버전)이 baseline보다 더 엄격(`seed==42`, `temperature==0.0`, `user_response`/`audit_tags`/`counterfactual` 필수 등 추가 검증) — 우리가 항상 그 조건을 만족하는 값을 생성하므로 실제로 막힌 적은 없음, 문제 아님.

**검증**: 74개 테스트 통과, drift guard 통과, dev 0.9384 유지(personal_memory 추가는 dev에 영향 없음이 사전 확인대로 재현됨), `submission.csv` 재생성, `audit_screening.py` 재확인 이상 없음.

## 외부 리뷰 4건 검증 — 신뢰성/일반화 개선 3건 적용, 1건은 이미 해당 없음 확인

사용자가 외부에서 받은 리뷰(4개 제안)를 코드와 대조 검증하라고 요청. 각각 실제 코드를 열어 근거를 확인한 뒤 적용/기각을 판단함.

1. **`run_harness`에 태스크 단위 예외 처리 없음 — 채택**: 실제로 `for task in ordered: ... answer_one(harness, task, session) ...`에 try/except가 전혀 없어서, 태스크 하나가 unseen 스키마(예: `attrs`가 list로 옴)로 예외를 던지면 **전체 제출이 중단**됨. `_fallback_answer()`를 추가해 예외 발생 시 `validate_answer_consistency`의 `ask` 규칙을 만족하는 안전한 기본 답안으로 대체하도록 수정. 직접 예외를 던지는 가짜 harness로 시뮬레이션: 크래시 태스크는 fallback으로, 앞뒤 태스크는 정상 처리됨을 확인, `validate_payload` 통과 확인.
2. **`choose_focal`의 `WM-\d+` 하드코딩 — 채택**: focal 후보가 여러 개일 때 쓰는 5개 정규식(최상위 `refs` 추출 + `pass_match`/`fixed_match`/`stated_match`/`binding_match`) 전부가 "WM-숫자" 포맷을 가정하고 있었음. 이 포맷이 dev+screening 820개 전체에서 100% 일관되지만 스펙 문서 어디에도 고정 포맷으로 명시된 적은 없음. `re.findall(r"WM-\d+", ...)` 대신 이 태스크의 실제 `object_by_ref()` 키(그 태스크에 진짜 존재하는 ref_code들)로 동적 정규식을 만들도록 교체 — 리뷰어의 원안은 순서 보존이 깨지는 버그가 있어서(`object_by_ref().keys()`를 텍스트 등장 순서가 아니라 object 목록 순서로 씀) 직접 구현을 다시 짬. 교체 전/후로 screening 700개 전체의 `choose_focal` 출력을 직접 diff — **0건 변경**(현재 데이터에서 완전히 동일하게 동작하면서 포맷 가정만 제거됨).
3. **한국어 띄어쓰기 변형 매칭 — 채택(공백만)**: `_has_value`에 공백 제거 폴백 추가. 먼저 안전성부터 확인 — dev+screening 820개 전체에서 실제로 호출되는 모든 `_has_value(...)` 조합을 원래 방식과 공백-제거 방식으로 나란히 비교한 결과 **차이 0건**(현재 데이터에는 이 정규화가 새로 매치를 만들거나 깨는 사례가 전혀 없음 - 순수하게 hidden 데이터를 위한 안전장치). 단, 리뷰어가 예시로 든 "전달 대신" vs "전달은대신"은 공백이 아니라 조사 삽입이라 이 방식으로는 안 잡힘 — 조사 변형까지 다루려면 형태소 분석 수준의 훨씬 위험한 접근이 필요해서 범위에서 제외.
4. **`FixedSLMClient`의 `sensitive_content` 텍스트 매칭 — 기각(해당 없음)**: 리뷰가 지적한 evidence의 텍스트 기반 `sensitive_content` 플래그는 `build_policy` 963번째 줄에서 **즉시 버려지고**, 1040번째 줄에서 `contained_fields(focal) & SENSITIVE_FIELDS`(focal object의 실제 구조적 attrs)로 다시 계산됨을 코드로 재확인. `"resident_number"`/`"social_id"` 같은 새 단어가 텍스트에 나와도 이미 구조적 필드 목록으로 판단하고 있어서 리뷰가 지적한 경로 자체가 죽어있는 코드임.

**검증**: 74개 테스트 통과, drift guard 통과, dev 0.9384 유지(세 변경 모두 현재 데이터에서 무변화, hidden 데이터 대비 안전장치), `submission.csv` 재생성, `audit_screening.py` 재확인 이상 없음.

## 외부 리뷰 2차 4건 검증 — 전부 반증, 대신 "고칠 여지" 재조사에서 안전한 조사-유연 매칭 1건 발굴

사용자가 같은 리뷰어의 2차 제안 4건(지시어 기반 focal 추론, 프롬프트 동적 제외 필드, fuzzy 타겟 키 매칭, 조사 제거 정규화)을 검증 요청. 이번엔 전부 반증되거나 근거가 없었음:

1. **"나머지"/"다른 것"/"반대" focal 추론 — 기각(위험)**: "나머지"는 screening 115건에서 전부 `visible_history`의 "…승인 상태가 유지된 참조는 X이다. **나머지는 후보군에만 남았다**" 패턴으로, "선택 안 된 후보"라는 뜻. 이미 `stated_match` 정규식+`FOCAL_POSITIVE_TERMS`("승인")로 정확한 객체를 고르고 있음을 실제 object ref_code 대조로 확인(85건은 marker로, 나머지 30건은 stated_match로 이미 정확히 해결). "반대"는 3건 전부 "반대 말투로 보내도 되는지"(메시지 톤 질문)로 focal과 무관. 제안대로 구현했으면 반대로 틀린 객체를 골랐을 것. 게다가 `choose_focal`은애초에 `session`을 안 받아 코드가 그대로 작동도 안 함.
2. **프롬프트 기반 동적 제외 필드 — 기각(반증)**: "주소"/"카드"/"이름" 패턴은 dev+screening 어디에도 없음(가상 시나리오). "원문" 패턴(dev 1건, screening 35건)의 유일한 dev 케이스는 이미 정답과 일치(`excluded_fields: []`, status_only 관행). screening에서 애매해 보였던 `final_screening_a1c5253a74d4`(ask+summary, `contains`에 raw_quote 있는데 `excluded_fields=["name"]`뿐)도 다시 파보니 **dev 5건이 전부 동일 패턴**(`contains`에 raw_quote/numeric_value/rrn이 있어도 정답은 `["name"]`만) — 이미 dev로 검증된 관행이었음. 제안대로 구현했으면 이 dev 5건을 새로 틀리게 만들었을 것.
3. **Fuzzy 타겟 키 매칭 — 기각(가상)**: 820개 전체의 실제 object attrs 키를 전수 확인 — `target`/`channel`/`recipient`/`dest`/`endpoint` 힌트를 포함하는 키가 하나도 없음. 이 로직은 지금 데이터에서 단 한 번도 발동하지 않고, 발동 여부를 검증할 근거 자체가 없어 "고친 버전"을 만들 방법이 없음(순수 추측 vocabulary).
4. **조사 제거 정규화 — 기각(버그 확인) 후 재검토에서 안전한 대안 채택**: 제안된 정규식(`$` 앵커)이 전체 텍스트 블록 끝에서만 매치되어 실제로는 거의 작동 안 함을 직접 재현. 사용자가 "완전히 막힌 게 아니라 '있는'/'은행'과 다르게 안전한 버전을 만들 수 없냐"고 재질문 — haystack 전체에서 조사를 벗기는 대신, **우리가 이미 정의한 다중 단어 needle의 단어 이음새에만** 조사 삽입을 허용하는 `_particle_flexible_pattern`을 새로 설계: `"전달 대신"` → `전달[은는이가을를]?\s*대신` 형태의 정규식으로, 리뷰어의 원래 예시("전달 대신" vs "전달은대신")를 정확히 잡으면서 "은행"/"있는" 같은 무관한 단어는 애초에 이 정규식의 매칭 범위에 들어오지 않아 전혀 안 건드림. `_has_value`에 3번째 폴백 단계로 추가(1단계: 정확 일치, 2단계: 공백 제거, 3단계: 조사-유연 매칭). 안전성 검증: 820개 전체에서 실제 호출되는 모든 `_has_value` 조합에 대해 이 3단계가 1·2단계 결과를 바꾸는 경우 **0건** — whitespace 폴백과 동일하게 순수 hidden 데이터 안전장치.

**검증**: 74개 테스트 통과, drift guard 통과, dev 0.9384 유지, `submission.csv` 재생성(무변화 확인), `audit_screening.py` 재확인 이상 없음.

**이번 라운드의 교훈**: 리뷰 제안을 판정할 때 "코드가 버그가 있다"와 "코드를 고치면 쓸모 있다"는 별개의 질문이었음. 1·2·3번은 고쳐도 검증할 근거가 없거나(3번) 실제 정답과 반대(1·2번)라 "고친 버전"이 의미가 없었지만, 4번은 버그(전역 조사 제거의 오탐 위험)만 고치는 게 아니라 **위험의 범위 자체를 우리가 이미 신뢰하는 needle 목록으로 좁히는 재설계**를 통해 안전하게 살릴 수 있었음 — 반대 제안을 그대로 고치는 게 아니라, 그 제안이 정말 원하는 것(조사 변형 방어)을 더 안전한 다른 방식으로 구현하는 게 핵심이었음.

## 정합성 검증 실패로 인한 전체 크래시 방어 — `_reconcile_answer` 추가

사용자가 외부 리뷰의 세 번째 제안(정합성 자동 교정 래퍼)을 검증 요청. 이번엔 진짜 구멍이었음: `run_harness`는 태스크 루프가 **다 끝난 뒤 한 번에** `validate_payload`→`validate_answer_consistency`를 호출하는데, 지난 라운드에 추가한 try/except는 루프 **안**의 `answer_one()` 호출만 감싸고 있어서, "예외는 안 던지지만 `content_scope`/`plan_events` 조합이 `validate_answer_consistency`의 규칙(hold/ask/status_only/amend/proceed별 mode·verb 제약)을 위반하는" 케이스는 전혀 못 잡음 — 이 경우 루프가 전부 끝난 뒤에야 `ValueError`가 터져서 이미 계산된 700개 전부가 날아감.

리뷰어의 원안은 방향은 맞지만 그대로 쓰지 않고 재설계함: `validate_answer_consistency`의 정확한 5개 분기(elif 순서: hold, ask, `scope.mode=="status_only"`, amend, proceed)를 그대로 반영한 `_reconcile_answer()`를 작성하되, **`_fallback_answer()`처럼 답 전체를 갈아엎지 않고 `focal_id`/`target`은 보존**하도록 함 — scope/policy/plan_events만 해당 control이 요구하는 최소 조합으로 교정. `validate_payload`의 per-answer 검증 로직도 `_validate_single_answer()`로 분리해 재사용(단순히 `validate_answer_consistency`만이 아니라 `scope.mode` 유효성, `user_response`/`counterfactual` 비어있음 등 나머지 검증도 다 커버하기 위함).

`run_harness`의 루프를 2단 방어로 구성: (1) `answer_one()` 실패 시 `_fallback_answer()`(기존), (2) 성공했어도 `_validate_single_answer()`를 태스크마다 즉시 실행해 실패하면 `_reconcile_answer()`로 교정 후 재검증, 그래도 실패하면 최종적으로 `_fallback_answer()`. 가짜 harness로 직접 시뮬레이션: `control="proceed"`인데 `dispatch` 이벤트가 빠진 경우 → `focal_id`/`target`은 그대로 보존한 채 `dispatch` 이벤트만 주입되어 통과함을 확인. `control` 자체가 유효하지 않은(복구 불가능한) 경우 → 최종적으로 `_fallback_answer()`까지 정상적으로 떨어짐을 확인.

**검증**: 74개 테스트 통과, drift guard 통과, dev 0.9384 유지, `submission.csv` 재생성 시 무변화(현재 데이터는 이 경로를 전혀 안 타는 순수 안전장치임을 재확인), `audit_screening.py` 이상 없음.

## 안전장치를 `run_harness`에서 `FinalHarness.answer_task` 자체로 이전 — 검증 환경 직접 호출 대비

외부 리뷰 4차 제안 검증 중 2번(fuzzy 타겟 키)·3번(동적 제외 필드)은 지난 두 라운드와 데이터가 안 바뀌어 동일하게 기각(근거 0건 재확인). 1번(예외 시 세션 상태 미기록으로 인한 멀티턴 연쇄 오류)을 확인하다가 **훨씬 더 근본적인 구조적 문제**를 발견함.

`TERMS_GUIDE.md`(cell 7 markdown)에는 "검증 환경에서는 `FinalHarness.answer_task(task, session)`을 task stream 순서대로 호출"한다고 명시되어 있음 — 즉 상위권 진출 시 비공개 task stream 재현성 검증은 **우리 `run_harness`를 거치지 않고 `answer_task`를 직접 호출**할 수 있음. 그런데 지금까지 두 라운드에 걸쳐 쌓은 크래시 방지(`_fallback_answer`)와 정합성 교정(`_reconcile_answer`) 안전장치는 전부 `run_harness`의 루프 안에만 있었음 — 즉 `answer_task`가 직접 호출되는 검증 경로에서는 **이 안전장치들이 전혀 작동하지 않는** 상태였음.

**조치**: `FinalHarness.answer_task`의 기존 로직을 `_compute_answer`로 분리하고, `answer_task` 자체에 `run_harness`와 동일한 2단 방어(예외 → `_fallback_answer`, 정합성 위반 → `_reconcile_answer` → 그래도 실패 시 `_fallback_answer`)를 다시 구현. `update_session_state`/`update_session_memory`도 `_compute_answer` 내부(성공 경로에서만 실행)에서 `answer_task` 바깥(성공/교정/폴백 어느 경우든 항상 실행)으로 이동 — 리뷰 1번이 지적한 "예외 시 세션 상태 미기록으로 다음 턴이 마비되는" 문제를 여기서 함께 해결함. `run_harness`의 기존 안전장치는 그대로 유지(범용 harness 클래스를 받을 수 있어 `FinalHarness` 외의 harness에는 여전히 유일한 방어선이므로) — 결과적으로 이중 방어(둘 다 안전, `FinalHarness`에서는 중복이지만 무해).

**검증**: 가짜 crash를 주입해 `run_harness`를 거치지 않고 `answer_task`를 세션 내 3턴 연속 직접 호출 — 2번째 턴에서 크래시 발생 시 안전한 기본 답으로 대체되고 세션에 `last_control="ask"` 등이 정상 기록됨, 3번째 턴은 (2번째 턴 실패와 무관하게) 정상 처리됨을 확인. 74개 테스트 통과, drift guard 통과, dev 0.9384 유지, `submission.csv` 재생성 무변화, `audit_screening.py`/`tests/test_robustness.py`(직접 `FinalHarness()` 사용) 이상 없음.

## 외부 에이전트("antigravity")가 세션 밖에서 수정한 코드 검증 — 일부 채택, 일부 되돌림

사용자가 다른 AI 에이전트("antigravity")가 이미 `harness.py`/`diagnostics/trace_target.py`를 건드려놓은 상태에서 검증을 요청. 지금까지와 동일한 강도로(dev 재검증 + screening 실제 영향 분리 측정 + 근거 확인 없이는 반영 안 함) 하나씩 판정함.

**변경 내용 3가지**:
1. `_guardrail_verified_external_route`/`_surface_resolved_channel_conflict`의 `dispatch_authority_check`를 `{"internal_binding_confirmed", "local_authority_confirmed"}`로 확장.
2. 같은 두 함수의 `share_boundary_update`를 `{"redacted_external_boundary", "redacted_after_selection_boundary"}`로 확장.
3. `infer_target`에 `focal.get("type") == "health_record"` → `memory["health_channel"]` 분기 재추가(지난 라운드에 텍스트 매칭 버전을 제거했던 자리).

**검증 결과**:
- **1번(dispatch_authority_check 확장) — 채택**: screening에서 실제로 `control`을 5건 바꾸는 걸 발견해 각 값의 기여도를 분리 측정 — 5건 전부 이 확장 때문이고, `local_authority_confirmed`는 이미 `ROUTE_CONFIRMED_VALUES`/`_external_binding_blocked`에서 검증해 쓰던 것과 같은 동치 관계(`internal_binding_confirmed`의 로컬 버전)라 새로운 추측이 아님. 게다가 이 두 predicate 자체가(넓히기 전 원래 값으로도) dev에서 4건 발동해 전부 정답과 일치(`proceed` 3/3, `ask` 1/1)함을 재확인 — 세션 초반에 "0건 발동"이라 판단했던 건 session-threading 없이 확인한 오류였음. 실제 사례(`route_candidate_snapshot=single_internal_candidate`+`dispatch_authority_check=local_authority_confirmed`+`share_boundary_update=redacted_external_boundary` 조합)를 직접 열어봐도 원래 predicate가 의도한 "검증된 로컬 라우트" 상황과 구조적으로 동일함을 확인.
2. **2번(share_boundary_update 확장) — 되돌림**: 두 값의 기여도를 분리 측정한 결과 `redacted_after_selection_boundary` 쪽은 screening 700개 전체에서 **실제 영향 0건** — 지금 당장 위험하지도 이득도 없지만, `local_authority_confirmed`와 달리 이 값에 대응하는 동치 관계가 코드 어디에도 없어 순수 추측. 이득 없이 리스크만 남기는 조합이라 원래 값(`redacted_external_boundary` 단일)으로 되돌림.
3. **3번(health_record 분기) — 되돌림**: 이 분기를 검증할 수 있었던 유일한 dev 사례(`검진/점검` 텍스트 버전을 원래 검증했던 그 사례)의 실제 focal object 타입이 `"health_record"`가 아니라 `"message"`였음 — 즉 이 새 분기는 그 dev 사례를 아예 안 타서 (기존처럼 `preferred_channel` 폴백으로 빠짐) 검증된 적이 없음. screening에서 실제로 발동하는 3건 중 2건은 `health_channel==preferred_channel`이라 무해하지만, 1건은 값이 서로 달라 실제로 답이 바뀌는데 어느 쪽이 맞는지 확인할 근거가 전혀 없어 되돌림.

**부수 이슈**: 1·2번을 판정하며 작성한 주석에 실수로 구체적 dev task ID(`final_dev_...`)를 적어 넣었다가 `test_harness_source_does_not_hardcode_task_or_session_ids` 가드 테스트가 즉시 잡아냄 — 개수/조건 설명으로 교체해 해결. 이 가드 테스트가 정확히 의도대로 작동한 사례.

**검증**: 74개 테스트 통과(가드 테스트 포함), drift guard 통과, dev 0.9384 유지, `submission.csv` 재생성, `audit_screening.py` 이상 없음. 최종 반영: 1번만 유지, 2·3번은 harness.py와 diagnostics/trace_target.py 양쪽에서 원상복구.

## `_plain_composite_plan`의 자기모순 raw 출력 수정 — 제안된 트리거는 반증하고 정밀한 버전으로 재설계

같은 외부 검증 흐름에서 "일관성 클러스터 분석으로 실질 버그를 찾았다"는 제안을 검증. 트레일링 절 `"단, 요약 공유는 허용되지만 raw 문장과 위치, 숫자 값은 포함하지 않는다."`를 공유하는 10개 screening 태스크를 직접 추적 — 9건은 각자의 보안/정책 신호에 맞춰 hold/ask/amend로 정상 처리되는데, `final_screening_ab49ebf02567` 1건만 `_plain_composite_plan`이 먼저 매칭돼 `proceed+mode:"raw"`를 강제로 냄을 확인. 이 태스크의 focal object 자체도 `"회의 시간을 반영하고 요약본만 보낸 뒤..."`라고 명시하는데 결과가 raw(원문 전체 노출)를 내는 건 태스크 자신의 텍스트와 정면으로 모순되는 명백한 버그.

**제안된 수정("요약"/"제외"/"포함하지" 있으면 mode를 summary로") 자체는 반증됨**: dev에서 `_plain_composite_plan`+proceed에 도달하는 5개 태스크를 직접 대조한 결과, 그중 2건은 **정답이 이미 "raw"**인데 이 2건도 "요약" 트리거에 걸림 — 이유는 `_plain_composite_plan`을 발동시키는 게이팅 조건 자체가 `"파일 요약"`을 포함하고 있어서, "요약"이라는 단어가 이 분기에 도달하는 거의 모든 태스크에 이미 내장돼 있었기 때문(자기 자신과 거의 항상 겹치는 무의미한 판별 조건). "dev 영향 0%"라는 제안 측 주장은 실제로 확인해보니 틀렸음 — 그대로 구현했으면 정답이 raw인 dev 2건을 새로 틀리게 만들었을 것.

**재설계**: 문제의 실제 trailing 절에 쓰인 특정 문구 `"raw 문장"`(영어 raw + 문장, 일반적인 "요약"보다 훨씬 좁고 구체적) + 제외 관련 단어(`"포함하지"`/`"제외"`/`"가리"`/`"빼"`)의 조합으로 교체. dev 5건 전부(raw 정답 2건 포함) 재검증 결과 새 트리거가 전혀 안 걸림(진짜 0% 영향), screening에서는 의도한 `ab49ebf02567` 정확히 1건에만 발동함을 확인. `excluded_fields`도 하드코딩 대신 기존 `excluded or ["raw_quote"]` 패턴을 그대로 재사용해 다른 amend 분기와 일관성 유지.

**검증**: 74개 테스트 통과, drift guard 통과, dev 0.9384 그대로(재검증된 진짜 무영향), `submission.csv`에서 정확히 이 1개 태스크만 변경됨(`proceed/summary/excluded=[raw_quote]`로 정정) 확인, `audit_screening.py` 이상 없음.

**교훈**: "dev 영향 0%"라는 주장도 직접 재현해서 검증하기 전까지는 사실로 받아들이지 않는다 — 이번엔 근거가 되는 근본 원인(버그 자체)은 정확했지만 제안된 구체적 수정(트리거 단어 선택)은 검증 없이 나온 추정이었고, 실제로 재현해보니 그 추정이 틀렸음이 드러났다.

## 실제 제출 점수 0.752 → 0.7509로 소폭 하락 — `local_authority_confirmed` 확장 되돌림

BOM 제거 이후 재제출한 실제 리더보드 점수가 0.7509로, 이전(0.752)보다 소폭 하락했다는 피드백을 받음. 그 사이 `submission.csv` 내용을 실제로 바꾼 변경은 두 가지뿐임을 재확인: (1) antigravity의 `dispatch_authority_check`/`share_boundary_update` 확장(`local_authority_confirmed`/`redacted_after_selection_boundary`, screening 5개 태스크의 control 변경), (2) `_plain_composite_plan` 자기모순 버그 수정(1개 태스크). 나머지(BOM, personal_memory, WM-ref 일반화, 조사-유연 매칭, 크래시/정합성 방어)는 전부 `submission.csv` 내용에 영향 0임을 이미 각각 확인해뒀던 것들.

(2)번은 태스크 자신의 텍스트와 정면으로 모순되던 걸 고친 것이라 점수를 깎았을 가능성이 낮음. (1)번은 "이미 검증된 동치 관계(`local_authority_confirmed` ~ `internal_binding_confirmed`)를 유추로 확장"한 것으로, 그 UNDERLYING 동치 관계 자체는 다른 곳에서 dev 검증됐지만 **이 두 predicate에서 확장된 값 자체는 한 번도 dev로 검증된 적이 없었음** — 5개 screening 태스크에 살아있는 채로 제출됐던 유일한 미검증 변경.

이 두 변경의 순 효과가 하락(-0.0011)이라면, (2)번이 확실한 개선이라 가정할 때 (1)번의 음의 기여가 그보다 커야 함 — 즉 (1)번이 5건 중 일부를 더 나쁘게 만들었을 가능성이 가장 유력한 가설. per-axis/per-task 피드백이 없어 확답은 불가능하지만, 근거의 확실성이 가장 낮았던 변경을 먼저 되돌리는 게 합리적인 선택이라 판단해 `_guardrail_verified_external_route`/`_surface_resolved_channel_conflict`를 원래의 좁은 형태(`internal_binding_confirmed`/`redacted_external_boundary` 단일 값)로 되돌림. `_plain_composite_plan` 수정은 유지.

**검증**: 74개 테스트 통과, drift guard 통과, dev 0.9384 유지, `submission.csv`는 이 5개 태스크만 원래대로 되돌아감. 다음 제출로 이 가설이 맞는지(점수가 0.7509보다 회복되는지) 확인 필요.
