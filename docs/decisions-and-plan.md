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
