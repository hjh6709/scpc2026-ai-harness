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

- **screening 정답 raw data가 없다.** 모든 검증은 간접적(구조 상관관계, dev와의 패턴 일치, 클러스터 내부 일관성)일 수밖에 없어서, 일부 수정(특히 `799c158`의 ask scope mode 판단)은 "다음 제출 전까지는 100% 확신 불가"인 상태로 남아있다.
- **제출 횟수 제한**으로 실험적 변경을 남발하면 안 되고, 확신도 높은 변경을 모아서 배치로 제출하는 편이 낫다.
- **대회 종료 시점**을 확인해서 남은 라운드 수를 가늠해야 한다 (현재 세션에서는 파악 못함).
