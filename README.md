# SCPC 2026 AI Agent Harness (1차 예선 코드 제출)

본 레포지토리는 **2026 SCPC : AI 챌린지 1차 예선**에 제출하는 에이전트 하네스(Harness) 소스 코드 및 검증용 프로젝트 파일입니다.

공개 데이터(`screening_tasks.jsonl`) 및 비공개 데이터(`Hidden Task`) 모두에서 일관되고 정합성 있게 동작하도록 설계된 결정론적 룰 기반 오케스트레이션 엔진(Deterministic Rule-based Orchestration Engine)입니다.

---

## 1. 사용한 Python 버전 및 의존성 (Dependencies)
- **Python 버전**: `Python 3.8` 이상 (테스트 완료: `Python 3.10` / `Python 3.11`)
- **외부 패키지 의존성**: **없음 (Python Standard Library 내장 모듈만 사용)**
  - 외부 모델 API 호출, 네트워크 서비스, pretrained artifact를 전혀 사용하지 않으므로 `pip install` 과정이 필요 없습니다.

---

## 2. 프로젝트 디렉토리 구조 및 파일 배치 방법
제출 ZIP 파일의 압축을 해제한 뒤, 아래와 같은 구조로 파일을 배치합니다.

```
scpc2026-ai-harness/
├── README.md               # 본 설명 파일
├── harness.py              # 에이전트 하네스 핵심 소스 코드
├── generate_submission.py  # 최종 submission.csv 생성 스크립트
├── evaluate_dev.py         # 로컬 dev 데이터셋 검증/채점 스크립트
├── audit_screening.py      # screening 데이터셋 정합성 검증 스크립트
├── submission.csv          # 최종 제출용 csv 파일
├── tests/                  # 유닛 테스트 코드 디렉토리
└── diagnostics/            # 흐름 진단 및 텔레메트리 툴 디렉토리
```

- **평가용 Task 데이터셋 배치**:
  - `screening_tasks.jsonl` 및 `dev_tasks.jsonl` 등의 데이터셋 파일은 로컬 환경 경로에 배치하여 스크립트 실행 인자(`--tasks`)로 지정합니다.

---

## 3. 실행 방법 및 명령어 (Execution Commands)

### A. 최종 제출 파일(submission.csv) 재생성 방법
제공된 `screening_tasks.jsonl` 데이터셋을 입력받아 제출용 CSV 파일을 100% 동일하게 재생성합니다.
```bash
python3 generate_submission.py \
  --tasks "[경로]/screening_tasks.jsonl" \
  --output submission.csv
```
*(예: `python3 generate_submission.py --tasks ./screening_tasks.jsonl`)*

### B. 로컬 검증 및 채점 실행 방법 (Development Evaluation)
로컬 개발용 정답 세트(`dev_answers.json`)와 대조하여 하네스의 정합성 스코어를 산출합니다.
```bash
python3 evaluate_dev.py \
  --tasks "[경로]/dev_tasks.jsonl" \
  --answers "[경로]/dev_answers.json" \
  --show 0
```
- **현재 로컬 dev proxy 결과**: `overall: 0.9395`
- **strict exact 진단**: `strict_exact_overall: 0.9395`, `core_key_set_exact: 1.000`
  - 이 로컬 점수는 공개 dev 참조답안 기준의 진단값이며, DACON 서버 점수를 보장하지 않습니다.

### C. 유닛 테스트 구동 방법
코드의 변형(Drift)이나 예외 처리를 전수 검사하는 74개 테스트를 실행합니다.
```bash
python3 -m unittest discover tests -v
```

---

## 4. 난수 및 재현 조건 (Reproducibility & Seed)
- **결정론적 실행**: 본 Harness는 샘플링이나 확률적 생성(LLM Generation)이 아닌, 규칙 및 상태 기계(State Machine)에 의한 **100% 결정론적(Deterministic) 룰 엔진**으로 작동하므로 임의의 난수가 개입하지 않아 항상 동일한 결과를 재현합니다.
- **제출용 메타데이터 고정값**:
  - `fixed_slm_policy`: `"local_fixed_slm_only"`
  - `model_id`: `"scpc-final-fixed-slm-local-facade"`
  - `temperature`: `0.0`
  - `seed`: `42`

---

## 5. 규정 준수 보증
1. **네트워크 호출 차단**: `socket` 및 `urllib` 등의 원격 네트워크 접근 코드가 전혀 존재하지 않습니다.
2. **하드코딩 및 정답 Lookup 배제**: 소스 코드 전체에 `task_id`나 `session_id` 등 특정 문제를 가리키는 정적 맵이 전혀 없으며, 오직 입력 스키마의 `records`와 `prompt` 분석에만 의존하여 답안을 산출합니다.
