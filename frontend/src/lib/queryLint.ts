// 검색식 문법을 즉석에서 훑어보는 순수 함수. 네트워크 호출 없음 — 실제로 몇 건이 걸리는지는
// 여전히 정밀 견적(EstimatePanel)으로만 확인할 수 있다. 이 파일은 "명백히 깨진 문법"만 잡아낸다.
//
// OpenAlex 검색식은 AND/OR/NOT과 괄호, "구문 검색"을 지원한다(SubfieldEditor의
// QueryHelpPanel 참고). KCI는 문법 자체가 확인되지 않았으므로(단순 키워드로 추정) 괄호/따옴표
// 짝만 본다 — AND/OR/NOT을 KCI 검색식에 대고 검사하면 오검출만 늘어난다.

export interface LintIssue {
  code: string;
  message: string;
}

export interface LintResult {
  errors: LintIssue[];
  warnings: LintIssue[];
}

const OPERATORS = new Set(["AND", "OR", "NOT"]);

// 괄호·따옴표·빈괄호는 두 모드 공통. 문자열 안(따옴표로 묶인 구간)의 괄호까지 세밀하게 가릴
// 필요는 없다 — 실수로 짝이 안 맞는 괄호를 잡아내는 게 목적이지 완전한 파서가 아니다.
function checkBracketsAndQuotes(trimmed: string, errors: LintIssue[]) {
  let depth = 0;
  for (const ch of trimmed) {
    if (ch === "(") depth++;
    else if (ch === ")") {
      if (depth === 0) {
        errors.push({ code: "paren-order", message: "닫는 괄호가 여는 괄호보다 먼저 나옵니다." });
        depth = -1; // 이후 depth>0 판정에서 중복 보고하지 않도록
        break;
      }
      depth--;
    }
  }
  if (depth > 0) {
    errors.push({ code: "paren-unclosed", message: "괄호가 닫히지 않았습니다." });
  }

  const quoteCount = (trimmed.match(/"/g) ?? []).length;
  if (quoteCount % 2 !== 0) {
    errors.push({ code: "quote-unmatched", message: "큰따옴표(\")가 짝이 맞지 않습니다." });
  }

  if (/\(\s*\)/.test(trimmed)) {
    errors.push({ code: "empty-parens", message: "빈 괄호 ()가 있습니다." });
  }
}

// 따옴표로 묶인 구간은 하나의 토큰으로, 나머지는 공백/괄호 기준으로 쪼갠다.
// "AND NOT" / "OR NOT"은 부정 결합으로 흔히 쓰이는 유효한 형태라 연속 연산자 오류에서 뺀다.
function checkOperators(trimmed: string, errors: LintIssue[]) {
  const tokens = trimmed.match(/"[^"]*"|\(|\)|[^\s()]+/g) ?? [];
  const words = tokens.filter((t) => t !== "(" && t !== ")");
  if (words.length === 0) return;

  if (OPERATORS.has(words[0])) {
    errors.push({ code: "leading-operator", message: `연산자(${words[0]})로 시작할 수 없습니다.` });
  }
  const last = words[words.length - 1];
  if (OPERATORS.has(last)) {
    errors.push({ code: "trailing-operator", message: `연산자(${last})로 끝날 수 없습니다.` });
  }
  for (let i = 0; i < words.length - 1; i++) {
    const a = words[i];
    const b = words[i + 1];
    if (!OPERATORS.has(a) || !OPERATORS.has(b)) continue;
    if (b === "NOT" && (a === "AND" || a === "OR")) continue; // "AND NOT" / "OR NOT" 허용
    errors.push({ code: "consecutive-operators", message: `연산자가 연속됩니다 (${a} ${b}).` });
    break;
  }
}

export function lintQuery(raw: string, mode: "openalex" | "kci"): LintResult {
  const trimmed = raw.trim();
  const errors: LintIssue[] = [];
  const warnings: LintIssue[] = [];
  if (!trimmed) return { errors, warnings };

  checkBracketsAndQuotes(trimmed, errors);

  if (mode === "openalex") {
    checkOperators(trimmed, errors);
    if (/[,|]/.test(trimmed)) {
      warnings.push({
        code: "comma-pipe",
        message: "콤마(,) 또는 파이프(|)가 포함되어 있습니다 — 백엔드가 공백으로 치환하므로 의도와 다른 결과가 나올 수 있습니다.",
      });
    }
  }

  return { errors, warnings };
}

// 검증: frontend/src/lib/queryLint.test.ts
